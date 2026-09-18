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
from .models import Settings
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
        if guess.confidence < settings.min_location_confidence:
            raise ValueError("Location confidence below threshold; inspect location.json")
        if round_identity(page, settings) != identity:
            raise ValueError("Game URL or round changed during location analysis")
        open_map(page, settings)
        rect = map_rect(page, settings)
        before = screenshot_map(page, rect, run_dir / "map.png")
        target = vision.map_target(run_dir / "map.png", guess, run_dir)
        report["map_target"] = target.model_dump()
        save_json(run_dir / "map_target.json", target.model_dump())
        if not target.visible or target.confidence < settings.min_map_confidence:
            raise ValueError(
                "Destination not reliably visible on map; expand/zoom the map and retry"
            )
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
