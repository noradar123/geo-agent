import io
import json

import pytest
from PIL import Image

from geoagent import cli
from geoagent.agent import PlacementStopped, plan_map_click, play_desktop_round, snap_to_city
from geoagent.demo import DemoVision
from geoagent.models import FixedLayout, MapTarget, Point, Settings


def png(color="white") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buffer, format="PNG")
    return buffer.getvalue()


class FakeDesktop:
    """Records actions; each map screenshot is tagged with how many scrolls preceded it."""

    def __init__(self, size=(1917, 1198), title="Play - GeoGuessr - Google Chrome"):
        self.size = size
        self.title = title
        self.events = []
        self.position = Point(x=5, y=5)
        self.scrolls = 0
        self.hand_move_after = None

    def screen_size(self):
        return self.size

    def grab(self, rect, path):
        data = png()
        path.write_bytes(data)
        self.events.append(("grab", path.name, None if rect is None else rect.model_dump()))
        return data

    def cursor(self):
        return self.position

    def move(self, point):
        self.events.append(("move", point.x, point.y))
        self.position = point

    def scroll(self, notches):
        self.scrolls += notches
        self.events.append(("scroll", notches))

    def drag(self, start, end):
        self.events.append(("drag", start.x, end.x))
        self.position = end

    def click(self, point):
        self.events.append(("click", point.x, point.y))

    def wait(self, ms):
        self.events.append(("wait", ms))
        if self.hand_move_after is not None and len(self.events) >= self.hand_move_after:
            self.position = Point(x=0, y=0)

    def foreground_title(self):
        return self.title


class ZoomVision(DemoVision):
    """Returns a different map fraction per step, and records which screenshot it saw."""

    def __init__(self, desktop, targets):
        self.desktop = desktop
        self.targets = targets
        self.seen = []

    def map_target(self, image, guess, run_dir, log_name="map_api.json"):
        self.seen.append((image.name, self.desktop.scrolls, log_name))
        x, y, confidence = self.targets[len(self.seen) - 1]
        return MapTarget(
            visible=True, x_fraction=x, y_fraction=y, confidence=confidence, reason="test"
        )


TARGETS = [(0.5, 0.5, 0.9), (0.25, 0.75, 0.9), (0.6, 0.4, 0.9), (0.7, 0.2, 0.9)]
# One wheel notch per step keeps scroll counts equal to step indexes in these tests.
SETTINGS = Settings(desktop_layout=FixedLayout(zoom_steps=3, scroll_notches=1, scene_views=1))


def map_point(fx, fy):
    return round(1016 + fx * 864), round(430 + fy * 630)


def test_fraction_to_desktop_pixel():
    layout = FixedLayout()
    target = MapTarget(visible=True, x_fraction=0.5, y_fraction=0.25, confidence=1, reason="")
    assert plan_map_click(layout, target) == Point(x=1448, y=588)


@pytest.mark.parametrize(
    "visible,x,y,confidence,reason",
    [
        (False, 0.5, 0.5, 0.9, "not visible"),
        (True, 0.995, 0.5, 0.9, "edge"),
        # (1043, 457) is the + button: (1043-1016)/864, (457-430)/630
        (True, 27 / 864, 27 / 630, 0.9, "control"),
    ],
)
def test_unsafe_targets_stop(visible, x, y, confidence, reason):
    layout = FixedLayout(edge_margin=0 if reason == "control" else 0.02)
    target = MapTarget(
        visible=visible, x_fraction=x, y_fraction=y, confidence=confidence, reason=""
    )
    with pytest.raises(PlacementStopped, match=reason):
        plan_map_click(layout, target)


def test_layout_scales_only_with_matching_aspect():
    scaled = FixedLayout().scaled_to(1917 * 2, 1198 * 2)
    assert scaled.hover == Point(x=3450, y=1920)
    assert scaled.map_rect.width == 1728
    with pytest.raises(ValueError, match="recalibrate"):
        FixedLayout().scaled_to(1440, 900 + 200)


def test_dry_run_zooms_with_fresh_screenshot_each_step_but_never_clicks(tmp_path):
    desktop = FakeDesktop()
    vision = ZoomVision(desktop, TARGETS)
    report = play_desktop_round(desktop, SETTINGS, vision, tmp_path, "dry-run")
    assert report["status"] == "planned"
    # Step i is identified on a screenshot taken after exactly i wheel notches.
    assert vision.seen == [(f"map_{i}.png", i, f"map_{i}_api.json") for i in range(4)]
    moves = [e[1:] for e in desktop.events if e[0] == "move"]
    assert moves[0] == (1725, 960)
    # Each zoom anchor is the point recognized on the latest screenshot, not an older one.
    assert moves[1:] == [map_point(*t[:2]) for t in TARGETS[:3]]
    assert report["click"] == dict(zip("xy", map_point(*TARGETS[3][:2])))
    assert not any(e[0] == "click" for e in desktop.events)
    assert [s["action"] for s in report["steps"][:3]] == ["scroll_up_1"] * 3
    assert "action" not in report["steps"][3]
    for i in range(4):
        assert (tmp_path / f"map_{i}.png").exists()
        assert (tmp_path / f"map_{i}_target.json").exists()
    saved = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert saved["zoom_steps"] == 3 and len(saved["steps"]) == 4


def test_event_order_scroll_happens_at_anchor_before_next_capture(tmp_path):
    desktop = FakeDesktop()
    play_desktop_round(desktop, SETTINGS, ZoomVision(desktop, TARGETS), tmp_path, "dry-run", 1)
    names = [e[0] if e[0] != "grab" else e[1] for e in desktop.events]
    assert names == [
        "scene.png",
        "move",
        "wait",
        "map_0.png",
        "move",
        "scroll",
        "wait",
        "map_1.png",
    ]


def test_place_and_submit_click_final_point_then_guess(tmp_path):
    desktop = FakeDesktop()
    report = play_desktop_round(desktop, SETTINGS, ZoomVision(desktop, TARGETS), tmp_path, "submit")
    assert report["status"] == "submit_clicked"
    clicks = [e[1:] for e in desktop.events if e[0] == "click"]
    assert clicks == [map_point(*TARGETS[3][:2]), (1448, 1098)]
    assert (tmp_path / "placed.png").exists() and (tmp_path / "after_submit.png").exists()


def test_place_does_not_submit(tmp_path):
    desktop = FakeDesktop()
    report = play_desktop_round(desktop, SETTINGS, ZoomVision(desktop, TARGETS), tmp_path, "place")
    assert report["status"] == "placed"
    assert len([e for e in desktop.events if e[0] == "click"]) == 1


def test_scene_rotates_and_sends_every_view(tmp_path):
    desktop = FakeDesktop()
    vision = ZoomVision(desktop, [(0.5, 0.5, 0.9)] * 6)
    seen = []
    vision.locate = lambda images, run_dir: seen.extend(images) or DemoVision().locate(images, 0)
    report = play_desktop_round(desktop, Settings(), vision, tmp_path)
    assert [p.name for p in seen] == ["scene.png", "scene_1.png", "scene_2.png", "scene_3.png"]
    assert report["scene_images"] == [p.name for p in seen]
    # 90 degrees * 14.2 px = 1278 px per turn, split into drags of at most 60% of the width.
    drags = [e for e in desktop.events if e[0] == "drag"]
    assert sum(start - end for _, start, end in drags) == pytest.approx(3 * 1278, abs=6)
    assert all(start - end <= 1917 * 0.6 + 1 for _, start, end in drags)
    names = [e[1] if e[0] == "grab" else e[0] for e in desktop.events]
    # Every capture after the first follows a drag and a wait, and all precede map work.
    assert names.index("scene_3.png") < names.index("map_0.png")


def test_default_layout_zooms_five_times_two_notches_each(tmp_path):
    desktop = FakeDesktop()
    targets = [(0.5, 0.5, 0.9)] * 6
    settings = Settings(desktop_layout=FixedLayout(scene_views=1))
    report = play_desktop_round(desktop, settings, ZoomVision(desktop, targets), tmp_path)
    assert report["zoom_steps"] == 5 and desktop.scrolls == 10
    assert [e for e in desktop.events if e[0] == "scroll"] == [("scroll", 2)] * 5


def test_lost_target_after_zoom_scrolls_back_and_places_on_restored_view(tmp_path):
    desktop = FakeDesktop()
    targets = [TARGETS[0], TARGETS[1], (0.995, 0.5, 0.9), (0.3, 0.6, 0.8)]
    vision = ZoomVision(desktop, targets)
    report = play_desktop_round(desktop, SETTINGS, vision, tmp_path, "place")
    assert report["status"] == "placed" and report["zoomed_back"] is True
    # map_2 was taken after 2 zoom-ins; map_2_back after scrolling one notch back out.
    assert [(name, scrolls) for name, scrolls, _ in vision.seen] == [
        ("map_0.png", 0),
        ("map_1.png", 1),
        ("map_2.png", 2),
        ("map_2_back.png", 1),
    ]
    assert report["steps"][2]["action"] == "scroll_down_1" and "rejected" in report["steps"][2]
    assert report["steps"][3]["zoom_ins_before"] == 1
    clicks = [e[1:] for e in desktop.events if e[0] == "click"]
    assert clicks == [map_point(0.3, 0.6)]


def test_uncertain_intermediate_step_still_zooms(tmp_path):
    desktop = FakeDesktop()
    targets = [TARGETS[0], (0.4, 0.4, 0.3), TARGETS[2], TARGETS[3]]
    report = play_desktop_round(desktop, SETTINGS, ZoomVision(desktop, targets), tmp_path, "place")
    assert report["status"] == "placed" and "zoomed_back" not in report
    assert desktop.scrolls == 3
    assert report["click"] == dict(zip("xy", map_point(*TARGETS[3][:2])))


def test_low_confidence_final_step_still_clicks(tmp_path):
    desktop = FakeDesktop()
    targets = TARGETS[:3] + [(0.5, 0.5, 0.05)]
    report = play_desktop_round(desktop, SETTINGS, ZoomVision(desktop, targets), tmp_path, "place")
    assert report["status"] == "placed" and "zoomed_back" not in report
    clicks = [e[1:] for e in desktop.events if e[0] == "click"]
    assert clicks == [map_point(0.5, 0.5)]


def test_low_location_confidence_still_guesses(tmp_path):
    desktop = FakeDesktop()
    vision = ZoomVision(desktop, TARGETS)
    base_locate = vision.locate

    def unsure(images, run_dir):
        return base_locate(images, run_dir).model_copy(update={"confidence": 0.01})

    vision.locate = unsure
    report = play_desktop_round(desktop, SETTINGS, vision, tmp_path, "submit")
    assert report["status"] == "submit_clicked"


def test_scaled_screen_uses_scaled_coordinates(tmp_path):
    desktop = FakeDesktop(size=(1917 * 2, 1198 * 2))
    report = play_desktop_round(
        desktop, SETTINGS, ZoomVision(desktop, TARGETS), tmp_path, "dry-run", 0
    )
    assert report["layout"]["map_rect"] == {"x": 2032, "y": 860, "width": 1728, "height": 1260}
    assert report["click"] == {"x": 2032 + 0.5 * 1728, "y": 860 + 0.5 * 1260}
    assert ("move", 3450, 1920) in desktop.events


def test_hand_mouse_movement_aborts(tmp_path):
    desktop = FakeDesktop()
    desktop.hand_move_after = 1
    with pytest.raises(ValueError, match="moved by hand"):
        play_desktop_round(desktop, SETTINGS, ZoomVision(desktop, TARGETS), tmp_path, "place")
    assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))["status"] == "error"
    assert not any(e[0] == "click" for e in desktop.events)


def test_wrong_foreground_window_refuses(tmp_path):
    desktop = FakeDesktop(title="Terminal")
    with pytest.raises(ValueError, match="Foreground window"):
        play_desktop_round(desktop, SETTINGS, ZoomVision(desktop, TARGETS), tmp_path)
    assert not any(e[0] == "move" for e in desktop.events)


def test_cli_desktop_run_defaults_to_dry_run_without_config(monkeypatch, tmp_path):
    desktop = FakeDesktop()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "new_run", lambda: tmp_path)
    monkeypatch.setattr(cli, "WindowsDesktop", lambda: desktop)
    monkeypatch.setattr(cli, "Vision", lambda: ZoomVision(desktop, TARGETS))
    cli.execute(cli.parser().parse_args(["desktop-run", "--delay", "0", "--zoom-steps", "2"]))
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "planned" and report["zoom_steps"] == 2
    assert not any(e[0] == "click" for e in desktop.events)


def city_guess(**update):
    base = (
        DemoVision()
        .locate([], None)
        .model_copy(
            update={
                "latitude": 42.80,
                "longitude": 74.50,
                "uncertainty_km": 40,
                "city": "Bishkek",
                "city_latitude": 42.874,
                "city_longitude": 74.590,
            }
        )
    )
    return base.model_copy(update=update)


def test_confident_urban_scene_snaps_to_city_centre():
    snapped, info = snap_to_city(city_guess(), Settings())
    assert info["applied"] and (snapped.latitude, snapped.longitude) == (42.874, 74.590)
    assert 5 < info["moved_km"] < 15


@pytest.mark.parametrize(
    "update,reason",
    [
        ({"setting": "rural"}, "not an identified urban"),
        ({"city_latitude": None, "city_longitude": None}, "not an identified urban"),
        ({"city_confidence": 0.4}, "too low"),
        ({"city_latitude": 43.24, "city_longitude": 76.95}, "km from the estimate"),
    ],
)
def test_city_snap_skipped(update, reason):
    guess = city_guess(**update)
    snapped, info = snap_to_city(guess, Settings())
    assert not info["applied"] and reason in info["reason"]
    assert (snapped.latitude, snapped.longitude) == (guess.latitude, guess.longitude)


def test_map_steps_receive_snapped_destination(tmp_path):
    desktop = FakeDesktop()
    vision = ZoomVision(desktop, TARGETS)
    vision.locate = lambda images, run_dir: city_guess()
    destinations = []
    original = vision.map_target

    def record(image, guess, run_dir, log_name="map_api.json"):
        destinations.append((guess.latitude, guess.longitude))
        return original(image, guess, run_dir, log_name)

    vision.map_target = record
    report = play_desktop_round(desktop, SETTINGS, vision, tmp_path, "dry-run", 1)
    assert report["city_snap"]["applied"]
    assert set(destinations) == {(42.874, 74.590)}
    assert report["location"]["latitude"] == 42.80
