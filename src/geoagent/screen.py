import ctypes
import io
import sys
import time
from ctypes import wintypes
from pathlib import Path

from PIL import Image, ImageGrab

from .models import Point, Rect
from .storage import save_json

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120


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


def enable_dpi_awareness(user32) -> None:
    """Make mouse coordinates physical pixels, matching Pillow's DPI-aware screenshots."""
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except AttributeError:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            user32.SetProcessDPIAware()
    # A call can fail because awareness was already set; only the resulting state matters.
    if not user32.IsProcessDPIAware():
        raise RuntimeError("Could not enable Windows DPI awareness; mouse and screenshot differ")


class WindowsDesktop:
    """Primary-monitor screenshots and real mouse input in one physical-pixel coordinate system."""

    def __init__(self):
        if sys.platform != "win32":
            raise RuntimeError("Desktop mouse control is only implemented for Windows")
        self.user32 = ctypes.windll.user32
        enable_dpi_awareness(self.user32)

    def screen_size(self) -> tuple[int, int]:
        with ImageGrab.grab() as image:
            size = image.size
        metrics = (self.user32.GetSystemMetrics(0), self.user32.GetSystemMetrics(1))
        if size != metrics:
            raise RuntimeError(
                f"Screenshot size {size} differs from mouse coordinate range {metrics}; "
                "DPI awareness is not effective"
            )
        return size

    def grab(self, rect: Rect | None, path: Path) -> bytes:
        bbox = None
        if rect is not None:
            bbox = (int(rect.x), int(rect.y), int(rect.x + rect.width), int(rect.y + rect.height))
        image = ImageGrab.grab(bbox=bbox)
        try:
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, format="PNG")
        finally:
            image.close()
        data = buffer.getvalue()
        path.write_bytes(data)
        return data

    def cursor(self) -> Point:
        point = wintypes.POINT()
        if not self.user32.GetCursorPos(ctypes.byref(point)):
            raise OSError("GetCursorPos failed")
        return Point(x=max(point.x, 0), y=max(point.y, 0))

    def move(self, point: Point, steps: int = 12, duration: float = 0.15):
        # Glide instead of jumping so hover handlers see ordinary pointer movement.
        start = self.cursor()
        for i in range(1, steps + 1):
            x = start.x + (point.x - start.x) * i / steps
            y = start.y + (point.y - start.y) * i / steps
            self.user32.SetCursorPos(round(x), round(y))
            time.sleep(duration / steps)
        self.user32.mouse_event(MOUSEEVENTF_MOVE, 0, 0, 0, 0)

    def scroll(self, notches: int):
        """Positive notches scroll up (zoom in), negative scroll down, at the cursor position."""
        delta = WHEEL_DELTA if notches > 0 else -WHEEL_DELTA
        for _ in range(abs(notches)):
            self.user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, delta, 0)
            time.sleep(0.05)

    def drag(self, start: Point, end: Point, steps: int = 30, duration: float = 0.4):
        """Press at start, glide to end, release: a pan gesture, not a click."""
        self.move(start)
        self.user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.05)
        for i in range(1, steps + 1):
            x = start.x + (end.x - start.x) * i / steps
            y = start.y + (end.y - start.y) * i / steps
            self.user32.SetCursorPos(round(x), round(y))
            self.user32.mouse_event(MOUSEEVENTF_MOVE, 0, 0, 0, 0)
            time.sleep(duration / steps)
        time.sleep(0.05)
        self.user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

    def click(self, point: Point):
        self.user32.SetCursorPos(round(point.x), round(point.y))
        self.user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.05)
        self.user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

    def wait(self, ms: int):
        time.sleep(ms / 1000)

    def foreground_title(self) -> str:
        window = self.user32.GetForegroundWindow()
        length = self.user32.GetWindowTextLengthW(window)
        buffer = ctypes.create_unicode_buffer(length + 1)
        self.user32.GetWindowTextW(window, buffer, length + 1)
        return buffer.value
