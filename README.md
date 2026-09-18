# GeoAgent

Analyze a GeoGuessr street scene with OpenAI vision. Sign in and open the game in your normal browser, then capture your desktop or use a selected screenshot from the clipboard.

```text
Open a street-view round manually
  → Countdown while you switch to the game
  → Save scene.png
  → Analyze the image with OpenAI
  → Display coordinates, clues, alternatives, and estimated uncertainty
```

The main `run` and `capture` commands do not launch or connect to a browser, place map pins, or submit answers. They do not require a game URL, browser login profile, map calibration, or `config.json`.

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
OPENAI_TIMEOUT_SECONDS=90
```

Set `OPENAI_API_KEY` to your own API key. The `run` command requires a key; `capture` does not. Choose a model that supports both image inputs and structured outputs. API usage is billed through the OpenAI Platform.

Never commit your key. `.env` files, local configuration, browser profiles, screenshots, and run artifacts are excluded from Git. Only the empty `.env.example` template belongs in the repository. During analysis, the selected scene image is sent to OpenAI.

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

## Project structure

| Module | Responsibility |
| --- | --- |
| `cli.py` | Command parsing, orchestration, and result display |
| `screen.py` | Countdown, desktop/clipboard capture, crops, and reports |
| `vision.py` | OpenAI image requests and structured parsing |
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

The pre-commit hook checks the full Git index. The pre-push hook checks every reachable commit. Both reject private local files, recognizable API tokens, private keys, and exact matches for the local OpenAI key. Reports contain file names and detection labels, with secret values redacted.

Run the checks manually:

```powershell
.\.venv\Scripts\python.exe scripts/check_secrets.py
.\.venv\Scripts\python.exe scripts/check_secrets.py --history
```

These checks are additional protection, not a substitute for keeping credentials out of source files. Hooks must be enabled in each clone.

## Legacy browser automation

The optional browser workflow is documented in [docs/BROWSER_AUTOMATION.md](docs/BROWSER_AUTOMATION.md). Its commands are `browser-run` and `browser-capture`. `login`, `calibrate`, `inspect`, and the offline `demo` also remain available.

## References

- [Pillow ImageGrab](https://pillow.readthedocs.io/en/stable/reference/ImageGrab.html)
- [OpenAI image inputs](https://developers.openai.com/api/docs/guides/images-vision)
- [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
