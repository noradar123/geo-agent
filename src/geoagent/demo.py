from pathlib import Path

from .agent import play_round
from .browser import browser_session
from .models import LocationGuess, MapTarget, Settings
from .storage import new_run


class DemoVision:
    """Fixed fake predictions for integration checks, never for a live game."""

    def locate(self, images, run_dir):
        return LocationGuess(
            country="台灣",
            region="台北",
            city="台北市",
            latitude=25.033,
            longitude=121.5654,
            confidence=0.95,
            uncertainty_km=5,
            clues=["離線測試固定輸出，沒有呼叫 AI"],
            summary="Mock prediction for browser integration testing",
            alternatives=[],
        )

    def map_target(self, image, guess, run_dir):
        return MapTarget(
            visible=True,
            x_fraction=0.75,
            y_fraction=0.3,
            confidence=0.99,
            reason="離線 fixture 的固定測試點，非 AI 地圖辨識",
        )


def run_demo() -> Path:
    run_dir = new_run()
    fixture = Path(__file__).parent / "fixtures" / "game.html"
    settings = Settings(
        url=fixture.resolve().as_uri(),
        profile_dir=str(run_dir / "profile"),
        scene_selector="#scene",
        map_selector="#map",
        submit_selector="#guess",
        round_selector="#round",
        result_selector="#result",
        settle_ms=20,
    )
    with browser_session(settings, headless=True) as page:
        report = play_round(page, settings, DemoVision(), run_dir, mode="submit")
        assert report["status"] == "result_visible"
        assert page.locator("#result").inner_text() == "Submitted: 75%, 30%"
    return run_dir
