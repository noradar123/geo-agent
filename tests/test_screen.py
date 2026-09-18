import json

import pytest
from PIL import Image

from geoagent import cli
from geoagent.demo import DemoVision
from geoagent.screen import analyze_screen, capture_screen


def test_run_captures_crops_analyzes_without_browser_or_config(monkeypatch, tmp_path):
    def grab(**kwargs):
        image = Image.new("RGB", (120, 100), "green")
        image.putpixel((25, 25), (0, 0, 255))
        return image

    monkeypatch.setattr("geoagent.screen.ImageGrab.grab", grab)
    monkeypatch.setattr(cli, "new_run", lambda: tmp_path)
    monkeypatch.setattr(cli, "Vision", DemoVision)
    monkeypatch.setattr(cli, "browser_session", lambda *a, **k: pytest.fail("Opened a browser"))
    monkeypatch.setattr(cli, "load_settings", lambda *a: pytest.fail("Read browser config"))
    args = cli.parser().parse_args(["run", "--delay", "0", "--region", "10", "20", "30", "40"])
    cli.execute(args)
    with Image.open(tmp_path / "scene.png") as image:
        assert image.size == (30, 40)
        assert image.getpixel((15, 5)) == (0, 0, 255)
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "analyzed"
    assert report["location"]["latitude"] == 25.033


def test_capture_only_never_initializes_api(monkeypatch, tmp_path):
    monkeypatch.setattr("geoagent.screen.ImageGrab.grab", lambda **k: Image.new("RGB", (50, 50)))
    monkeypatch.setattr(cli, "new_run", lambda: tmp_path)
    monkeypatch.setattr(cli, "Vision", lambda: pytest.fail("Initialized API client"))
    monkeypatch.setattr(cli, "browser_session", lambda *a, **k: pytest.fail("Opened a browser"))
    cli.execute(cli.parser().parse_args(["capture", "--delay", "0"]))
    assert (tmp_path / "scene.png").is_file()
    assert not (tmp_path / "location.json").exists()


def test_clipboard_image_skips_desktop_capture_and_countdown(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "geoagent.screen.ImageGrab.grabclipboard", lambda: Image.new("RGBA", (80, 40), "red")
    )
    monkeypatch.setattr("geoagent.screen.ImageGrab.grab", lambda **k: pytest.fail("Read desktop"))
    monkeypatch.setattr("geoagent.screen.countdown", lambda *a: pytest.fail("Started countdown"))
    report = analyze_screen(tmp_path, clipboard=True)
    assert report["capture"]["source"] == "clipboard"
    with Image.open(tmp_path / "scene.png") as image:
        assert image.size == (80, 40)
        assert image.mode == "RGB"


def test_clipboard_file_list_is_not_treated_as_image(monkeypatch, tmp_path):
    monkeypatch.setattr("geoagent.screen.ImageGrab.grabclipboard", lambda: ["private-file.png"])
    with pytest.raises(ValueError, match="剪貼簿沒有圖片"):
        capture_screen(tmp_path / "scene.png", clipboard=True)


def test_region_outside_screen_does_not_save_padded_image(monkeypatch, tmp_path):
    monkeypatch.setattr("geoagent.screen.ImageGrab.grab", lambda **k: Image.new("RGB", (50, 50)))
    with pytest.raises(ValueError, match="outside the captured image"):
        capture_screen(tmp_path / "scene.png", [10, 10, 100, 100])
    assert not (tmp_path / "scene.png").exists()


def test_analysis_failure_preserves_screenshot_and_phase(monkeypatch, tmp_path):
    monkeypatch.setattr("geoagent.screen.ImageGrab.grab", lambda **k: Image.new("RGB", (50, 50)))

    class FailedVision:
        def locate(self, images, run_dir):
            raise RuntimeError("API unavailable")

    with pytest.raises(RuntimeError, match="API unavailable"):
        analyze_screen(tmp_path, FailedVision(), delay=0)
    assert (tmp_path / "scene.png").exists()
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["status_before_error"] == "captured"
