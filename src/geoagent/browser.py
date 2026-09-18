import io
from contextlib import contextmanager
from pathlib import Path

from PIL import Image, ImageChops
from playwright.sync_api import Page, sync_playwright

from .models import Point, Rect, Settings


@contextmanager
def browser_session(settings: Settings, headless: bool = False):
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(Path(settings.profile_dir).resolve()),
            headless=headless,
            viewport={"width": settings.viewport_width, "height": settings.viewport_height},
            device_scale_factor=1,
            channel=settings.browser_channel,
        )
        context.set_default_timeout(15000)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(settings.url, wait_until="domcontentloaded")
            yield page
        finally:
            context.close()


def unique_visible(page: Page, selector: str):
    visible = page.locator(selector).filter(visible=True)
    count = visible.count()
    if count != 1:
        raise ValueError(
            f"Selector {selector!r} matched {count} visible elements; expected exactly 1"
        )
    return visible


def screenshot_scene(page: Page, settings: Settings, path: Path) -> bytes:
    if settings.scene_selector:
        return unique_visible(page, settings.scene_selector).screenshot(path=str(path), scale="css")
    return page.screenshot(path=str(path), full_page=False, scale="css")


def map_rect(page: Page, settings: Settings) -> Rect:
    if settings.map_selector:
        locator = unique_visible(page, settings.map_selector)
        box = locator.bounding_box()
        if not box:
            raise ValueError("Map element has no visible bounding box")
        rect = Rect.model_validate(box)
    elif settings.map_rect:
        rect = settings.map_rect
    else:
        raise ValueError("Configure map_selector or run the calibrate command first")
    size = page.viewport_size
    if not size or rect.x + rect.width > size["width"] or rect.y + rect.height > size["height"]:
        raise ValueError("Map extends outside the browser viewport; recalibrate or expand it")
    return rect


def open_map(page: Page, settings: Settings):
    if settings.map_hover:
        page.mouse.move(settings.map_hover.x, settings.map_hover.y)
    elif settings.map_selector:
        unique_visible(page, settings.map_selector).hover()
    elif settings.map_rect:
        r = settings.map_rect
        page.mouse.move(r.x + r.width / 2, r.y + r.height / 2)
    page.wait_for_timeout(settings.settle_ms)


def screenshot_map(page: Page, rect: Rect, path: Path | None = None) -> bytes:
    return page.screenshot(path=str(path) if path else None, clip=rect.model_dump(), scale="css")


def changed_fraction(before: bytes, after: bytes) -> float:
    with Image.open(io.BytesIO(before)) as first, Image.open(io.BytesIO(after)) as second:
        if first.size != second.size:
            return 1.0
        diff = ImageChops.difference(first.convert("RGB"), second.convert("RGB"))
        # Count changed pixels, not compressed PNG bytes. Ignore tiny rasterization noise.
        mask = diff.convert("L").point(lambda value: 255 if value > 12 else 0)
        histogram = mask.histogram()
        return histogram[255] / (first.width * first.height)


def round_identity(page: Page, settings: Settings) -> tuple[str, str | None]:
    text = None
    if settings.round_selector:
        text = unique_visible(page, settings.round_selector).inner_text()
    return page.url, text


def point_on_map(rect: Rect, x_fraction: float, y_fraction: float) -> Point:
    if not 0 < x_fraction < 1 or not 0 < y_fraction < 1:
        raise ValueError("Map target touches the image edge; cannot click reliably")
    return Point(x=rect.x + rect.width * x_fraction, y=rect.y + rect.height * y_fraction)


CALIBRATION_SCRIPT = """() => {
  window.__geoagentCalibration = [];
  const panel = document.createElement('div');
  panel.id = 'geoagent-calibration-panel';
  Object.assign(panel.style, {position: 'fixed', top: '8px', left: '8px', zIndex: '2147483647',
    background: '#101820', color: 'white', padding: '14px', font: '16px sans-serif',
    pointerEvents: 'none', border: '2px solid #00d9ad'});
  const labels = ['Shift+click the COLLAPSED MAP area, then expand the map normally',
    'Shift+click MAP TOP LEFT (exclude borders / Guess button)',
    'Shift+click MAP BOTTOM RIGHT', 'Shift+click the GUESS BUTTON center'];
  panel.textContent = labels[0];
  document.body.appendChild(panel);
  const handler = event => {
    if (!event.shiftKey) return;
    event.preventDefault(); event.stopImmediatePropagation();
    const target = event.target.closest('button, [role="button"]');
    let selector = null;
    if (target) {
      if (target.id) selector = '#' + CSS.escape(target.id);
      else if (target.getAttribute('data-qa'))
        selector = '[data-qa=' + JSON.stringify(target.getAttribute('data-qa')) + ']';
      else if (target.getAttribute('data-testid'))
        selector = '[data-testid=' + JSON.stringify(target.getAttribute('data-testid')) + ']';
      else if (target.getAttribute('aria-label'))
        selector = '[aria-label=' + JSON.stringify(target.getAttribute('aria-label')) + ']';
      else if (target.textContent.trim())
        selector = ':is(button, [role="button"]):text-is(' +
          JSON.stringify(target.textContent.trim()) + ')';
    }
    window.__geoagentCalibration.push({x: event.clientX, y: event.clientY, selector});
    const n = window.__geoagentCalibration.length;
    panel.textContent = labels[n] || 'Calibration captured. Return to terminal.';
    if (n === 4) document.removeEventListener('pointerdown', handler, true);
  };
  document.addEventListener('pointerdown', handler, true);
  document.addEventListener('click', event => {
    if (event.shiftKey) { event.preventDefault(); event.stopImmediatePropagation(); }
  }, true);
}"""


def calibrate(page: Page, settings: Settings) -> Settings:
    page.evaluate(CALIBRATION_SCRIPT)
    page.wait_for_function("window.__geoagentCalibration?.length === 4", timeout=0)
    points = page.evaluate("window.__geoagentCalibration")
    page.locator("#geoagent-calibration-panel").evaluate("element => element.remove()")
    hover, first, second, button = points
    rect = Rect(
        x=first["x"], y=first["y"], width=second["x"] - first["x"], height=second["y"] - first["y"]
    )
    if not button["selector"]:
        raise ValueError("Guess target is not a recognizable button. Set submit_selector manually.")
    unique_visible(page, button["selector"])
    values = settings.model_dump()
    values.update(
        map_rect=rect.model_dump(),
        map_selector=None,
        map_hover={"x": hover["x"], "y": hover["y"]},
        submit_selector=button["selector"],
    )
    return Settings.model_validate(values)


def inspect_ui(page: Page) -> list[dict]:
    # Only visible DOM metadata used to configure UI controls; no scripts/game state are read.
    return page.evaluate("""() => [...document.querySelectorAll(
      'button, [role="button"], canvas, [class*="guess-map"], [class*="guessMap"]')]
      .filter(el => {const r = el.getBoundingClientRect();
        const s = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';})
      .slice(0, 100).map(el => { const r = el.getBoundingClientRect();
        return {tag: el.tagName, id: el.id, class: el.className?.toString(),
          text: el.innerText?.slice(0, 120), aria_label: el.getAttribute('aria-label'),
          data_qa: el.getAttribute('data-qa'), data_testid: el.getAttribute('data-testid'),
          rect: {x: r.x, y: r.y, width: r.width, height: r.height}};})""")
