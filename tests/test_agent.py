from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright
from pydantic import ValidationError

from geoagent.agent import play_round
from geoagent.browser import CALIBRATION_SCRIPT, changed_fraction, point_on_map
from geoagent.demo import DemoVision
from geoagent.models import MapTarget, Rect, Settings


@pytest.fixture
def game():
    fixture = Path(__file__).parents[1] / "src/geoagent/fixtures/game.html"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(fixture.resolve().as_uri())
        settings = Settings(
            scene_selector="#scene",
            map_selector="#map",
            submit_selector="#guess",
            result_selector="#result",
            round_selector="#round",
            settle_ms=0,
        )
        yield page, settings
        browser.close()


@pytest.mark.parametrize(
    "mode,status", [("dry-run", "planned"), ("place", "placed"), ("submit", "result_visible")]
)
def test_round_modes(game, tmp_path, mode, status):
    page, settings = game
    report = play_round(page, settings, DemoVision(), tmp_path, mode)
    assert report["status"] == status
    assert (tmp_path / "scene.png").exists()
    assert (tmp_path / "map.png").exists()
    if mode == "dry-run":
        assert page.locator("#pin").is_hidden()
    else:
        assert page.locator("#pin").is_visible()
    if mode == "submit":
        assert page.locator("#result").inner_text() == "Submitted: 75%, 30%"
    else:
        assert page.locator("#result").is_hidden()


def test_invisible_map_target_cannot_click(game, tmp_path):
    page, settings = game

    class Uncertain(DemoVision):
        def map_target(self, image, guess, run_dir):
            return MapTarget(
                visible=False,
                confidence=0.99,
                x_fraction=0.5,
                y_fraction=0.5,
                reason="Cannot place reliably",
            )

    with pytest.raises(ValueError, match="not visible"):
        play_round(page, settings, Uncertain(), tmp_path, "submit")
    assert page.locator("#pin").is_hidden()


def test_map_pan_during_analysis_cancels_click(game, tmp_path):
    page, settings = game

    class Panning(DemoVision):
        def map_target(self, image, guess, run_dir):
            page.locator("#map").evaluate("el => el.style.background = 'black'")
            return super().map_target(image, guess, run_dir)

    with pytest.raises(ValueError, match="Map contents changed"):
        play_round(page, settings, Panning(), tmp_path, "submit")
    assert page.locator("#pin").is_hidden()


def test_round_change_cancels_click(game, tmp_path):
    page, settings = game

    class NextRound(DemoVision):
        def map_target(self, image, guess, run_dir):
            page.locator("#round").evaluate("el => el.textContent = 'Round 2'")
            return super().map_target(image, guess, run_dir)

    with pytest.raises(ValueError, match="Round or map geometry changed"):
        play_round(page, settings, NextRound(), tmp_path, "submit")
    assert page.locator("#pin").is_hidden()


def test_missing_map_configuration_does_not_spend_api_calls(game, tmp_path):
    page, settings = game
    settings.map_selector = None

    class MustNotCall(DemoVision):
        def locate(self, images, run_dir):
            pytest.fail("Known missing configuration should fail before API analysis")

    with pytest.raises(ValueError, match="before calling the API"):
        play_round(page, settings, MustNotCall(), tmp_path, "submit")
    assert page.locator("#pin").is_hidden()


def test_calibration_captures_disabled_button_without_submitting(game):
    page, _ = game
    page.evaluate(CALIBRATION_SCRIPT)
    page.keyboard.down("Shift")
    page.mouse.click(300, 500)
    page.mouse.click(24, 432)
    page.mouse.click(624, 732)
    button = page.locator("#guess").bounding_box()
    page.mouse.click(button["x"] + button["width"] / 2, button["y"] + button["height"] / 2)
    page.keyboard.up("Shift")
    points = page.evaluate("window.__geoagentCalibration")
    assert len(points) == 4
    assert points[-1]["selector"] == "#guess"
    assert page.locator("#result").is_hidden()
    assert page.locator("#pin").is_hidden()


def test_invalid_output_and_bounds():
    with pytest.raises(ValidationError):
        MapTarget(visible=True, x_fraction=1.2, y_fraction=0.3, confidence=0.8, reason="bad")
    with pytest.raises(ValidationError):
        Settings(map_rect=Rect(x=1400, y=0, width=200, height=100))
    with pytest.raises(ValueError, match="image edge"):
        point_on_map(Rect(x=0, y=0, width=600, height=300), 1, 0.5)


def test_pixel_comparison(game):
    page, _ = game
    png = page.screenshot()
    assert changed_fraction(png, png) == 0
