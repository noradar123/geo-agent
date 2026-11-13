import math
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

from .browser import (
    changed_fraction,
    map_rect,
    open_map,
    point_on_map,
    round_identity,
    screenshot_map,
    screenshot_scene,
    unique_visible,
)
from .models import FixedLayout, LocationGuess, MapTarget, Point, Settings
from .storage import save_json


def play_round(page: Page, settings: Settings, vision, run_dir: Path, mode: str = "dry-run"):
    if mode not in {"dry-run", "place", "submit"}:
        raise ValueError(f"Unknown mode: {mode}")
    report = {"mode": mode, "status": "started"}
    report_path = run_dir / "report.json"
    save_json(report_path, report)
    try:
        if not settings.map_selector and not settings.map_rect:
            raise ValueError("Configure map_selector or run calibrate before calling the API")
        if mode == "submit" and not settings.submit_selector:
            raise ValueError("Set submit_selector or run calibrate before submitting")
        page.wait_for_timeout(settings.settle_ms)
        if (
            settings.result_selector
            and page.locator(settings.result_selector).filter(visible=True).count()
        ):
            raise ValueError("Result screen is visible; enter an active round before analysis")
        identity = round_identity(page, settings)
        screenshot_scene(page, settings, run_dir / "scene.png")
        guess = vision.locate([run_dir / "scene.png"], run_dir)
        report["location"] = guess.model_dump()
        save_json(run_dir / "location.json", guess.model_dump())
        if round_identity(page, settings) != identity:
            raise ValueError("Game URL or round changed during location analysis")
        open_map(page, settings)
        rect = map_rect(page, settings)
        before = screenshot_map(page, rect, run_dir / "map.png")
        target = vision.map_target(run_dir / "map.png", guess, run_dir)
        report["map_target"] = target.model_dump()
        save_json(run_dir / "map_target.json", target.model_dump())
        if not target.visible:
            raise ValueError("Destination not visible on map; expand/zoom the map and retry")
        point = point_on_map(rect, target.x_fraction, target.y_fraction)
        report["click"] = point.model_dump()
        if mode == "dry-run":
            report["status"] = "planned"
            return report
        if round_identity(page, settings) != identity or map_rect(page, settings) != rect:
            raise ValueError("Round or map geometry changed while planning; click cancelled")
        after = screenshot_map(page, rect, run_dir / "map_before_click.png")
        delta = changed_fraction(before, after)
        report["map_changed_fraction"] = delta
        if delta > settings.map_change_tolerance:
            raise ValueError("Map contents changed while planning; click cancelled")
        # Move first so map controls can respond to hover before the hit test.
        page.mouse.move(point.x, point.y)
        hit = page.evaluate(
            """p => {
          const el = document.elementFromPoint(p.x, p.y);
          return Boolean(el && !el.closest('button, [role="button"], a, input'));
        }""",
            point.model_dump(),
        )
        if not hit:
            raise ValueError("Map click would hit a control or no visible element")
        page.mouse.click(point.x, point.y)
        report["status"] = "placed"
        # Save placement immediately: submission failure must not conceal that the pin was placed.
        save_json(report_path, report)
        page.wait_for_timeout(settings.settle_ms)
        page.screenshot(path=str(run_dir / "placed.png"), scale="css")
        if mode == "submit":
            if round_identity(page, settings) != identity:
                raise ValueError("Round changed after map placement; submit cancelled")
            button = unique_visible(page, settings.submit_selector)
            if not button.is_enabled():
                raise ValueError("Guess button is disabled after map placement")
            button.click()
            report["status"] = "submit_clicked"
            save_json(report_path, report)
            if settings.result_selector:
                page.locator(settings.result_selector).filter(visible=True).wait_for(
                    state="visible"
                )
                unique_visible(page, settings.result_selector).wait_for(state="visible")
                report["status"] = "result_visible"
            else:
                page.wait_for_timeout(settings.settle_ms)
            page.screenshot(path=str(run_dir / "after_submit.png"), scale="css")
        return report
    except Exception as exc:
        report["error"] = str(exc)
        report["status_before_error"] = report["status"]
        report["status"] = "error"
        # Browser errors can prevent this diagnostic screenshot; preserve the original exception.
        try:
            page.screenshot(path=str(run_dir / "error.png"), scale="css", timeout=3000)
        except (PlaywrightError, OSError) as screenshot_error:
            report["diagnostic_screenshot_error"] = str(screenshot_error)
        raise
    finally:
        save_json(report_path, report)


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def snap_to_city(guess: LocationGuess, settings: Settings) -> tuple[LocationGuess, dict]:
    """Aim at the city centre for confident urban scenes; returns the map destination."""
    info = {"applied": False, "setting": guess.setting, "city": guess.city}
    if not settings.city_snap:
        return guess, {**info, "reason": "disabled"}
    if guess.setting != "urban" or guess.city_latitude is None or guess.city_longitude is None:
        return guess, {**info, "reason": "not an identified urban scene"}
    if guess.city_confidence < settings.city_snap_min_confidence:
        return guess, {**info, "reason": f"city confidence {guess.city_confidence:.2f} too low"}
    km = distance_km(guess.latitude, guess.longitude, guess.city_latitude, guess.city_longitude)
    # A city far from the model's own estimate means the two answers disagree; keep the estimate.
    if km > max(50.0, guess.uncertainty_km):
        return guess, {**info, "reason": f"city centre is {km:.0f} km from the estimate"}
    snapped = guess.model_copy(
        update={"latitude": guess.city_latitude, "longitude": guess.city_longitude}
    )
    return snapped, {**info, "applied": True, "moved_km": round(km, 1)}


class PlacementStopped(Exception):
    """A visible-evidence check failed; stop without clicking and record why."""


def plan_map_click(layout: FixedLayout, target: MapTarget) -> Point:
    """Convert a map_target result into a desktop pixel, rejecting unclickable placements.

    Model confidence is recorded but never blocks a click: a real round always needs a guess.
    """
    if not target.visible:
        raise PlacementStopped("Model reports the destination is not visible on the map")
    m = layout.edge_margin
    if not (m <= target.x_fraction <= 1 - m and m <= target.y_fraction <= 1 - m):
        raise PlacementStopped("Destination is at the map edge; cannot zoom or click reliably")
    r = layout.map_rect
    point = Point(
        x=round(r.x + target.x_fraction * r.width), y=round(r.y + target.y_fraction * r.height)
    )
    for name in ("zoom_in_button", "zoom_out_button"):
        control = getattr(layout, name)
        if math.hypot(point.x - control.x, point.y - control.y) <= layout.control_radius:
            raise PlacementStopped(f"Destination lies on the map control {name}")
    return point


def rotate_view(desktop, layout: FixedLayout, degrees: float) -> Point:
    """Turn the panorama right by dragging left across the middle band; returns the cursor."""
    r = layout.content_rect
    # The vertical middle stays clear of the HUD, compass and the minimap in the bottom right.
    cx, cy = r.x + r.width / 2, round(r.y + r.height / 2)
    remaining = degrees * layout.drag_px_per_degree
    segment_max = r.width * 0.6
    end = None
    while remaining > 0.5:
        segment = min(remaining, segment_max)
        start = Point(x=round(cx + segment / 2), y=cy)
        end = Point(x=round(cx - segment / 2), y=cy)
        desktop.drag(start, end)
        remaining -= segment
    return end


def play_desktop_round(
    desktop,
    settings: Settings,
    vision,
    run_dir: Path,
    mode: str = "dry-run",
    zoom_steps: int | None = None,
):
    """Fixed-layout desktop flow: scene -> expand map -> (locate, zoom) x N -> locate -> click."""
    if mode not in {"dry-run", "place", "submit"}:
        raise ValueError(f"Unknown mode: {mode}")
    steps = settings.desktop_layout.zoom_steps if zoom_steps is None else zoom_steps
    if not 0 <= steps <= 10:
        raise ValueError("zoom_steps must be between 0 and 10")
    report = {"mode": mode, "status": "started", "zoom_steps": steps, "steps": []}
    report_path = run_dir / "report.json"
    save_json(report_path, report)
    expected = None

    def check_cursor():
        # The user grabbing the mouse is the emergency stop for real desktop input.
        if expected is not None:
            now = desktop.cursor()
            if abs(now.x - expected.x) > 2 or abs(now.y - expected.y) > 2:
                raise ValueError("Mouse was moved by hand; desktop automation aborted")

    def move(point: Point):
        nonlocal expected
        check_cursor()
        desktop.move(point)
        expected = point

    def wait(ms: int):
        desktop.wait(ms)
        check_cursor()

    def check_window(layout: FixedLayout):
        title = desktop.foreground_title()
        if layout.window_title_contains and layout.window_title_contains not in title:
            raise ValueError(
                f"Foreground window {title!r} does not contain "
                f"{layout.window_title_contains!r}; switch to the game window"
            )

    try:
        width, height = desktop.screen_size()
        layout = settings.desktop_layout.scaled_to(width, height)
        report.update(screen={"width": width, "height": height}, layout=layout.model_dump())
        report["vision"] = {
            "provider": getattr(vision, "provider", None),
            "model": getattr(vision, "model", None),
            "map_model": getattr(vision, "map_model", None),
        }
        check_window(layout)
        scenes = []
        for view in range(layout.scene_views):
            if view:
                check_cursor()
                expected = rotate_view(desktop, layout, 360 / layout.scene_views)
                wait(layout.rotate_wait_ms)
            scene = run_dir / ("scene.png" if view == 0 else f"scene_{view}.png")
            desktop.grab(layout.content_rect, scene)
            scenes.append(scene)
        report["scene_images"] = [path.name for path in scenes]
        guess = vision.locate(scenes, run_dir)
        report["location"] = guess.model_dump()
        save_json(run_dir / "location.json", guess.model_dump())
        # location.json keeps the model's estimate; the map steps aim at the snapped point.
        guess, report["city_snap"] = snap_to_city(guess, settings)
        report["map_destination"] = {"latitude": guess.latitude, "longitude": guess.longitude}
        check_window(layout)
        move(layout.hover)
        wait(layout.expand_wait_ms)
        report["status"] = "map_expanded"

        def identify(name: str, zoom_ins: int):
            # Every identification uses a fresh screenshot: pixels from before a zoom are invalid.
            image = run_dir / f"{name}.png"
            shot = desktop.grab(layout.map_rect, image)
            target = vision.map_target(image, guess, run_dir, log_name=f"{name}_api.json")
            step = {"name": name, "zoom_ins_before": zoom_ins, "image": image.name}
            step["target"] = target.model_dump()
            report["steps"].append(step)
            save_json(run_dir / f"{name}_target.json", target.model_dump())
            try:
                point = plan_map_click(layout, target)
            except PlacementStopped as exc:
                step["rejected"] = str(exc)
                raise
            step["point"] = point.model_dump()
            return shot, point

        for index in range(steps + 1):
            try:
                last_map, point = identify(f"map_{index}", index)
            except PlacementStopped:
                if index == 0 or not layout.zoom_out_on_failure:
                    raise
                # Zoomed in too far to place reliably: undo the last zoom at the same cursor
                # position and identify again on the restored view.
                desktop.scroll(-layout.scroll_notches)
                report["steps"][-1]["action"] = f"scroll_down_{layout.scroll_notches}"
                wait(layout.zoom_wait_ms)
                last_map, point = identify(f"map_{index}_back", index - 1)
                report["zoomed_back"] = True
                break
            if index == steps:
                break
            move(point)
            desktop.scroll(layout.scroll_notches)
            report["steps"][-1]["action"] = f"scroll_up_{layout.scroll_notches}"
            save_json(report_path, report)
            wait(layout.zoom_wait_ms)
        report["click"] = point.model_dump()
        if mode == "dry-run":
            report["status"] = "planned"
            return report
        check_window(layout)
        move(point)
        after = desktop.grab(layout.map_rect, run_dir / "map_before_click.png")
        delta = changed_fraction(last_map, after)
        report["map_changed_fraction"] = delta
        if delta > settings.map_change_tolerance:
            raise PlacementStopped("Map contents changed after the last analysis; click cancelled")
        check_cursor()
        desktop.click(point)
        report["status"] = "placed"
        # Save placement immediately: submission failure must not conceal that the pin was placed.
        save_json(report_path, report)
        wait(layout.click_wait_ms)
        desktop.grab(None, run_dir / "placed.png")
        if mode == "submit":
            check_window(layout)
            move(layout.guess_button)
            check_cursor()
            desktop.click(layout.guess_button)
            report["status"] = "submit_clicked"
            save_json(report_path, report)
            wait(layout.click_wait_ms)
            desktop.grab(None, run_dir / "after_submit.png")
        return report
    except PlacementStopped as exc:
        report.update(stop_reason=str(exc), status_before_stop=report["status"], status="stopped")
        return report
    except Exception as exc:
        report.update(error=str(exc), status_before_error=report["status"], status="error")
        try:
            desktop.grab(None, run_dir / "error.png")
        except OSError as screenshot_error:
            report["diagnostic_screenshot_error"] = str(screenshot_error)
        raise
    finally:
        save_json(report_path, report)
