# GeoAgent

Analyze a GeoGuessr street scene with OpenAI vision (Claude and Gemini are optional). Sign in and open the game in your normal browser, then capture your desktop or use a selected screenshot from the clipboard.

```text
Open a street-view round manually
  → Countdown while you switch to the game
  → Save scene.png
  → Analyze the image with OpenAI
  → Display coordinates, clues, alternatives, and estimated uncertainty
```

The main `run` and `capture` commands do not launch or connect to a browser, place map pins, or submit answers. The separate `desktop-run` command (below) uses the real mouse on a fixed screen layout. They do not require a game URL, browser login profile, map calibration, or `config.json`.

## Setup

Requires Python 3.11 or later. On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

If the project is already installed, use the existing virtual environment. Edit your local `.env`:

```dotenv
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4.1
GEOAGENT_TIMEOUT_SECONDS=90
```

Set `OPENAI_API_KEY` to your own API key and `OPENAI_MODEL` to a model with image input and structured outputs. The `run` command requires a key; `capture` does not. Both the street-scene guess and map placement use OpenAI by default; see [Choosing the vision provider](#choosing-the-vision-provider) to switch.

Never commit your key. `.env` files, local configuration, browser profiles, screenshots, and run artifacts are excluded from Git. Only the empty `.env.example` template belongs in the repository. During analysis, the selected images are sent to the configured provider (OpenAI by default).

`requirements.lock.txt` records the dependency versions used during development.

## Capture and analyze

Open a street-view round in your normal browser, then run:

```powershell
.\.venv\Scripts\python.exe -m geoagent run
```

The program counts down for five seconds. Use Alt+Tab to switch to the game and keep the street scene visible. After capture, it makes one location-analysis request. Switch back to the terminal to read the coordinates, clues, model confidence, and estimated uncertainty.

Customize the countdown:

```powershell
.\.venv\Scripts\python.exe -m geoagent run --delay 10
```

By default, capture uses the primary monitor. On Windows, `--all-screens` includes all monitors. The `screen` command behaves the same as `run`.

## Capture without calling the API

```powershell
.\.venv\Scripts\python.exe -m geoagent capture
```

This saves `scene.png` after the countdown. It does not require an API key. `run --capture-only` is equivalent.

## Analyze only the street scene

Use the Windows snipping tool to avoid capturing unrelated parts of the screen:

1. Press **Win+Shift+S** in the game.
2. Select the street-scene area.
3. Return to the terminal and run:

```powershell
.\.venv\Scripts\python.exe -m geoagent run --clipboard
```

The program reads the clipboard image, saves it as PNG, and analyzes it without another countdown or desktop capture. `capture --clipboard` only saves the image. Text or file lists in the clipboard are rejected.

For a fixed scene area, specify a crop:

```powershell
.\.venv\Scripts\python.exe -m geoagent run --region 100 120 1200 650
```

The arguments are `X Y WIDTH HEIGHT` in captured-image pixels, starting at the top-left `(0, 0)`. Adjust them for your screen. Crops outside the image are rejected. With `--all-screens`, coordinates are relative to the composite image, rather than Windows desktop coordinates.

## Existing screenshots and multiple views

Analyze a saved PNG:

```powershell
.\.venv\Scripts\python.exe -m geoagent analyze "runs\YOUR_RUN\scene.png"
```

Analyze several views from the same round together:

```powershell
.\.venv\Scripts\python.exe -m geoagent analyze front.png left.png right.png
```

Output includes `latitude`, `longitude`, `country`, `region`, `city`, `clues`, and `alternatives`. Confidence and uncertainty are model estimates, not calibrated statistics. Coordinates are predictions rather than verified ground truth. Generated geography explanations remain in Traditional Chinese.

## Local artifacts

Each execution creates a separate `runs/TIMESTAMP-ID/` directory:

| File | Contents |
| --- | --- |
| `scene.png` | The captured and optionally cropped image |
| `location.json` | Predicted coordinates, clues, and alternatives |
| `location_api.json` | Structured API output, model, and token usage |
| `report.json` | Capture source, dimensions, crop, status, and errors |

If analysis fails, the saved screenshot remains available for retry with `analyze`. A normal `run` makes one location-analysis request; the SDK may retry eligible temporary failures. The desktop workflow does not make a map-placement request.

## Desktop map placement (fixed layout)

`desktop-run` drives your normal, already-signed-in Chrome window with the real Windows mouse. It uses only the visible street scene and visible map:

```text
Capture the content area → locate() → hover the minimap so it expands
  → repeat N times: capture map → map_target() → move to target → wheel up one notch → wait
  → capture map → map_target() → final click point
```

Before analysis, the street view is turned in equal steps by dragging across the middle of the scene: `scene_views: 4` captures every 90° (`scene.png`, `scene_1.png`, ...), `3` every 120°, and `1` disables rotation. All views are sent to `locate()` together. The drag distance per degree depends on the game's field of view, so calibrate it once without API calls:

```powershell
.\.venv\Scripts\python.exe -m geoagent rotate-test --degrees 90
```

Compare `before.png` and `after.png`: something at the right edge of `before.png` should end up near the left edge after 90° if the view spans about 90°. If the turn is too small, increase `desktop_layout.drag_px_per_degree` in `config.json`; if too large, decrease it. With a "No move, pan, zoom" mode, set `scene_views` to 1.

Each zoom step identifies the destination again on a new screenshot; pixels from before a zoom are never reused. The map is never treated as a fixed latitude/longitude grid.

For each map screenshot the model receives two images: the clean map and a copy with a labelled 8×6 grid (`map_N_grid.png`). It first lists nearby visible labels (cities, coastlines, borders), explains where the destination lies relative to them, and then answers with a grid cell plus a position inside that cell. This is converted back to a fraction of the clean image. The model also reports 2–4 visible anchor places (city dots) with their known coordinates and marker positions. Because the guess map is Web Mercator, the program fits this screenshot's scale and offset from those anchors and computes the destination's pixel directly (`method: "anchor_fit"` and `fit_residual_px` in `map_N_target.json`). If fewer than two anchors are usable or they disagree, the model's own cell answer is used (`method: "grid"`).

If a zoom makes the destination unreliable (not visible, low confidence, at the edge), the wheel is scrolled back out once at the same cursor position, the map is identified again (`map_N_back.png`), and that result is used for the final click. Set `zoom_out_on_failure` to `false` to stop instead.

```powershell
# Dry-run (default): expands and zooms the map, but never places a pin
.\.venv\Scripts\python.exe -m geoagent desktop-run
# Place the pin, but do not submit
.\.venv\Scripts\python.exe -m geoagent desktop-run --place
# Place the pin and click Guess
.\.venv\Scripts\python.exe -m geoagent desktop-run --submit
# Change the number of zoom iterations (default 3)
.\.venv\Scripts\python.exe -m geoagent desktop-run --zoom-steps 2 --delay 8
```

Requirements: Windows, Chrome maximized on the primary monitor, and the game tab in front during the countdown. The process enables per-monitor DPI awareness, so screenshot pixels and mouse coordinates are both physical pixels; it refuses to run if they differ.

Safety stops (status `stopped`, exit code 2, no click): low location or map confidence, destination not visible, within `edge_margin` of the map border, or within `control_radius` of the +/− buttons. Moving the mouse by hand aborts the run. The run also aborts if the foreground window title does not contain `window_title_contains` (default `GeoGuessr`; set `null` to disable).

The layout lives in `desktop_layout` in `config.json` (optional; defaults are used without it). Coordinates are desktop pixels measured on a 1917×1198 reference screenshot (Chrome toolbar and taskbar included):

| Setting | Default |
| --- | --- |
| `content_rect` (street scene sent to `locate`) | x=0, y=109, 1917×1032 |
| `minimap_rect` / `hover` | x=1573, y=875, 307×185 / (1725, 960) |
| `map_rect` (expanded map) | x=1016, y=430, 864×630 |
| `zoom_in_button` / `zoom_out_button` | (1043, 457) / (1043, 487) |
| `guess_button` | (1448, 1098) |
| `expand_wait_ms` / `zoom_wait_ms` / `click_wait_ms` | 1500 / 1500 / 1000 |
| `zoom_steps` / `scroll_notches` | 5 / 2 |
| `zoom_out_on_failure` | `true` |
| `scene_views` / `drag_px_per_degree` / `rotate_wait_ms` | 4 / 14.2 / 700 |

On a different screen size with the same aspect ratio (within `aspect_tolerance`), all coordinates are scaled proportionally. Otherwise the run stops and asks you to re-measure the coordinates. Proportional scaling assumes the page layout scales with the screen; if the map lands in a different place, re-measure.

Artifacts in the run directory: `screen` metadata and the scaled `layout` in `report.json`, plus `scene.png`, `location.json`, and for every step `map_N.png`, `map_N_api.json` (raw model response), and `map_N_target.json`. `report.json` lists each step's target, click pixel, and zoom action, plus the final `click`. With `--place`/`--submit`, it also saves `map_before_click.png`, `placed.png`, and `after_submit.png`.

## Choosing the vision provider

OpenAI is the default for both the street-scene guess and map placement. Claude and Gemini are also supported. Add the provider's key to `.env`, then pick the provider per run or in `.env` (Gemini needs its optional SDK):

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[gemini]"
.\.venv\Scripts\python.exe -m geoagent desktop-run --provider anthropic
.\.venv\Scripts\python.exe -m geoagent desktop-run --provider gemini --model gemini-2.5-pro
```

| Provider | Key | Model variable (default) |
| --- | --- | --- |
| `openai` (default) | `OPENAI_API_KEY` | `OPENAI_MODEL` (`gpt-4.1`) |
| `anthropic` | `ANTHROPIC_API_KEY` | `ANTHROPIC_MODEL` (`claude-opus-5-5`); `ANTHROPIC_EFFORT` (`high`) |
| `gemini` | `GEMINI_API_KEY` | `GEMINI_MODEL` (`gemini-2.5-pro`) |

`GEOAGENT_PROVIDER` overrides the default provider (`openai`).

Map placement makes one request per zoom step, so it can use a faster model than the street-scene guess. Set `GEOAGENT_MAP_MODEL` (or `--map-model`) to a fast model of the same provider, and optionally `GEOAGENT_MAP_EFFORT` (OpenAI reasoning models: `low`/`medium`/`high`; Claude: effort level). Leave the effort empty for non-reasoning models such as `gpt-4.1`. When unset, map placement uses the main model. `GEOAGENT_SCENE_EFFORT` sets the reasoning effort of the street-scene guess the same way; `low` is the fastest. Every `*_api.json` log records `elapsed_seconds` for that request.

 `--provider` and `--model` work on `run`, `analyze`, `desktop-run`, and `browser-run`. Each `*_api.json` log records the provider and model used. Claude requests enable server-side refusal fallback (`fallbacks: "default"`). If Claude declines a request, the API reruns it on a fallback model instead of failing.

## Project structure

| Module | Responsibility |
| --- | --- |
| `cli.py` | Command parsing, orchestration, and result display |
| `screen.py` | Countdown, desktop/clipboard capture, crops, reports, DPI-aware Windows mouse |
| `agent.py` | Browser round flow and the fixed-layout desktop zoom/placement flow |
| `vision.py` | Claude / OpenAI / Gemini image requests, map grid, structured parsing |
| `models.py` | Coordinate and result validation |
| `storage.py` | Run directories and UTF-8 JSON storage |
| `tests/test_screen.py` | Desktop workflow and error handling |
| `scripts/check_secrets.py` | Staged-file and committed-history secret checks |

Desktop capture uses Pillow. It does not inspect browser sessions or game DOM elements.

## Development checks

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_screen.py tests/test_vision.py tests/test_secrets.py -q
.\.venv\Scripts\python.exe -m ruff check .
```

Tests use mock images and API responses, so they do not send desktop images or call the paid API. They cover crops, capture-only execution, clipboard validation, preserved screenshots after failures, and secret detection.

The full test suite also includes legacy browser tests. To run those, install Chromium:

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD\.playwright-browsers"
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m pytest -q
```

## Secret checks before publishing

Enable the repository hooks after cloning:

```powershell
git config core.hooksPath .githooks
```

The pre-commit hook checks the full Git index. The pre-push hook checks every reachable commit. Both reject private local files, recognizable API tokens, private keys, and exact matches for local Claude, OpenAI, and Gemini keys. Reports contain file names and detection labels, with secret values redacted.

Run the checks manually:

```powershell
.\.venv\Scripts\python.exe scripts/check_secrets.py
.\.venv\Scripts\python.exe scripts/check_secrets.py --history
```

These checks are additional protection, not a substitute for keeping credentials out of source files. Hooks must be enabled in each clone.

## Legacy browser automation

An optional Playwright-based browser workflow is still available through the `browser-run` and `browser-capture` commands. `login`, `calibrate`, `inspect`, and the offline `demo` also remain available.

## References

- [Pillow ImageGrab](https://pillow.readthedocs.io/en/stable/reference/ImageGrab.html)
- [OpenAI image inputs](https://developers.openai.com/api/docs/guides/images-vision)
- [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
