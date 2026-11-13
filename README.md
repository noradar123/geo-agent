# GeoAgent

A multimodal LLM agent that looks at a street-level scene, infers where on Earth it is, and grounds that answer as a click on an interactive map. It works with OpenAI, Claude, and Gemini behind one interface and is benchmarked offline on [OpenStreetView-5M](https://huggingface.co/datasets/osv5m/osv5m).

Built as a personal research project on visual geolocation and GUI grounding. Use it only in single-player or custom games, not in ranked or competitive play.

## How it works

```text
street scene (1–4 rotated views)
  └─ locate()      clues → country → region → lat/lon        (structured output)

guess map screenshot
  └─ map_target()  grid overlay + 2–4 landmark anchors
                   └─ Web Mercator least-squares fit → target pixel
                      (falls back to the grid cell if the fit is poor)
  └─ zoom in at the target, take a new screenshot, repeat N times → click
```

- **Scene reasoning:** the model fills a fixed schema in order (clues, reasoning, country, then coordinates), so it commits to evidence before an answer.
- **Map grounding:** LLMs are poor at raw pixel coordinates. The map is sent twice, clean and with a labelled 8×6 grid. The model also names visible cities with known coordinates, and the program fits the map's Mercator scale and offset from those anchors to compute the target pixel. The fit residual is logged; with fewer than two usable anchors, it uses the model's grid answer.
- **Iterative zoom:** each zoom step re-identifies the target on a fresh screenshot. If a zoom loses the target, it scrolls back out once and places on that view.
- **Always guesses:** model confidence is logged but never blocks a guess. The run stops only when a click is impossible: the target is off-map, on a map control, or the mouse was moved by hand.

## Results

Run on 50 OpenStreetView-5M test images, one per country (`scripts/eval_locate.py`, single view, no game UI):

<!-- RESULTS -->
_Benchmark run in progress; table coming soon._

Confidence values are the model's own estimates and are not calibrated. OSV-5M images are crowd-sourced Mapillary photos, not Google Street View, so in-game accuracy may differ.

## Setup

Python 3.11+, Windows for the desktop commands.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"      # add ,gemini for Gemini
Copy-Item .env.example .env                                # then add your API key
```

| Provider | Key | Model variable (default) |
| --- | --- | --- |
| `openai` (default) | `OPENAI_API_KEY` | `OPENAI_MODEL` (`gpt-4.1`) |
| `anthropic` | `ANTHROPIC_API_KEY` | `ANTHROPIC_MODEL` (`claude-opus-5-5`) |
| `gemini` | `GEMINI_API_KEY` | `GEMINI_MODEL` (`gemini-2.5-pro`) |

Select the provider with `GEOAGENT_PROVIDER` or `--provider`, and override the model with `--model`. Optional settings: `GEOAGENT_MAP_MODEL` uses a faster model for the per-zoom map calls, and `GEOAGENT_SCENE_EFFORT` / `GEOAGENT_MAP_EFFORT` set reasoning effort. See `.env.example` for the full list.

## Usage

Commands run as `.\.venv\Scripts\python.exe -m geoagent <command>`.

| Command | What it does |
| --- | --- |
| `run` | Countdown, capture the screen, guess the location. `--clipboard` uses a Win+Shift+S snip; `--region X Y W H` crops |
| `capture` | Same capture, no API call |
| `analyze a.png [b.png …]` | Guess from saved PNGs (several views of one spot) |
| `desktop-run` | Full loop in your signed-in Chrome: rotate view, guess, expand and zoom the map. Dry-run by default; `--place` clicks the pin, `--submit` also presses Guess |
| `rotate-test` | Turn the view once and save before/after images to calibrate `drag_px_per_degree` |

Each run writes `runs/<timestamp>/`: screenshots, `location.json`, every raw API response (`*_api.json` with tokens and latency), and `report.json`.

### Desktop layout

`desktop-run` uses fixed screen coordinates measured on a 1917×1198 screen with Chrome maximized. They scale to other screens with the same aspect ratio; otherwise copy `config.example.json` to `config.json` and re-measure `desktop_layout`. Defaults: 4 scene views, 5 zoom steps of 2 wheel notches each. The run aborts if the foreground window title does not contain `GeoGuessr` or if you move the mouse.

## Evaluation

```powershell
python scripts/eval_locate.py prepare --n 50       # free: country-balanced sample
python scripts/eval_locate.py run --budget 1.00    # paid: stops before exceeding budget
python scripts/eval_locate.py report               # metrics table -> eval/osv5m/summary.md
```

`prepare` uses HTTP range requests to fetch only the sampled images from the dataset's multi-GB zip files (about 18 MB for 50 images). `run` is resumable, logs every request, and estimates cost from token usage (`--input-price` / `--output-price` per 1M tokens). Metrics: median error, accuracy within 1/25/200/750/2500 km, country accuracy (via an ISO `country_code` in the schema), and an approximate GeoGuessr score `5000·e^(−d/1492.7)`.

## Development

```powershell
.\.venv\Scripts\python.exe -m pytest -q        # mocked API, no paid calls
.\.venv\Scripts\python.exe -m ruff check .
git config core.hooksPath .githooks            # secret scan on commit and push
```

Browser tests in `test_agent.py` need Chromium: set `PLAYWRIGHT_BROWSERS_PATH=.playwright-browsers` and run `python -m playwright install chromium`. The hooks run `scripts/check_secrets.py`, which rejects private files, API-token patterns, and your local keys in the index and the full history.

| Module | Responsibility |
| --- | --- |
| `vision.py` | Provider clients, prompts, grid overlay, anchor fitting |
| `agent.py` | Desktop zoom/placement loop and the legacy browser flow |
| `screen.py` | Capture, crops, DPI-aware Windows mouse |
| `evaluation.py` | Remote zip reader, sampling, cost and metrics |
| `models.py` | Pydantic schemas and layout validation |
| `cli.py` | Command parsing and output |

Legacy Playwright commands (`browser-run`, `browser-capture`, `login`, `calibrate`, `inspect`, `demo`) are kept but no longer documented.

## Data

Evaluation images: OpenStreetView-5M (Astruc et al., CVPR 2024), CC-BY-SA 4.0, from Mapillary contributors. They are downloaded to `eval/`, which is not committed.
