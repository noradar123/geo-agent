import time
from pathlib import Path

from PIL import Image, ImageGrab

from .storage import save_json


def countdown(seconds: int):
    """Give the user time to switch from the terminal to the game window."""
    if not 0 <= seconds <= 60:
        raise ValueError("Delay must be between 0 and 60 seconds")
    if seconds:
        print("請切回已開啟的遊戲畫面。", flush=True)
    for remaining in range(seconds, 0, -1):
        print(f"{remaining}...", flush=True)
        time.sleep(1)


def capture_screen(
    path: Path,
    region: list[int] | None = None,
    *,
    clipboard: bool = False,
    all_screens: bool = False,
) -> dict:
    """Save a desktop or clipboard image, optionally cropped in image coordinates."""
    if clipboard:
        image = ImageGrab.grabclipboard()
        if not isinstance(image, Image.Image):
            raise ValueError("剪貼簿沒有圖片；請先用 Win+Shift+S 擷取街景，再執行指令。")
    else:
        image = ImageGrab.grab(all_screens=all_screens)
    try:
        original_size = image.size
        if region is not None:
            x, y, width, height = region
            if x < 0 or y < 0 or width <= 0 or height <= 0:
                raise ValueError("Region needs nonnegative X/Y and positive WIDTH/HEIGHT")
            if x + width > image.width or y + height > image.height:
                raise ValueError("Region extends outside the captured image")
            cropped = image.crop((x, y, x + width, y + height))
            image.close()
            image = cropped
        image.convert("RGB").save(path, format="PNG")
        return {
            "source": "clipboard" if clipboard else "desktop",
            "region": region,
            "all_screens": all_screens if not clipboard else False,
            "original_size": list(original_size),
            "image_size": list(image.size),
        }
    finally:
        image.close()


def analyze_screen(
    run_dir: Path,
    vision=None,
    *,
    delay: int = 5,
    region: list[int] | None = None,
    clipboard: bool = False,
    all_screens: bool = False,
) -> dict:
    """Capture a visible scene and save its location analysis and execution status."""
    report = {"mode": "screen-analysis" if vision else "screen-capture", "status": "started"}
    try:
        if not clipboard:
            countdown(delay)
        metadata = capture_screen(
            run_dir / "scene.png", region, clipboard=clipboard, all_screens=all_screens
        )
        report.update(capture=metadata, status="captured")
        save_json(run_dir / "report.json", report)
        print(f"截圖已保存：{(run_dir / 'scene.png').resolve()}", flush=True)
        if vision is not None:
            print("正在分析街景...", flush=True)
            guess = vision.locate([run_dir / "scene.png"], run_dir)
            save_json(run_dir / "location.json", guess.model_dump())
            report.update(location=guess.model_dump(), status="analyzed")
        return report
    except Exception as exc:
        report.update(error=str(exc), status_before_error=report["status"], status="error")
        raise
    finally:
        save_json(run_dir / "report.json", report)
