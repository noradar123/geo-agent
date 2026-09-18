# Legacy browser automation

The primary workflow captures your normal browser through the desktop. See the root [README](../README.md). This document describes the earlier Playwright workflow, which remains available for local experiments and integration testing.

```text
Browser street-scene screenshot
  → OpenAI location estimate
  → Screenshot of the visible guess map
  → OpenAI estimate of the map click position
  → Place a pin and optionally click Guess
```

The browser workflow handles one round at a time. Login, game selection, and advancing to another round are manual. Website authentication or automation restrictions may prevent this workflow from working even when local tests pass.

## Setup

Install the project as described in the root README, then install Chromium and create a local browser configuration:

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD\.playwright-browsers"
.\.venv\Scripts\python.exe -m playwright install chromium
Copy-Item config.example.json config.json
```

Keep the same `PLAYWRIGHT_BROWSERS_PATH` when running browser commands. Alternatively, set it in `.env`. Configuration, browser binaries, and profiles remain local and are excluded from Git.

## Login

```powershell
.\.venv\Scripts\python.exe -m geoagent login
```

Sign in manually in the opened browser, then press Enter in the terminal. The dedicated `.browser-profile/` stores the session. Only one process may use this profile at a time. This command does not call OpenAI or need an API key. Terminal waits continue to dispatch Playwright events.

Use a locally installed browser if desired:

```powershell
.\.venv\Scripts\python.exe -m geoagent login --browser chrome
```

`--browser msedge` is also supported. After terminal confirmation, the chosen channel is saved to `config.json`. A different channel does not guarantee compatibility with website authentication restrictions.

## Calibration

```powershell
.\.venv\Scripts\python.exe -m geoagent calibrate --url "https://www.geoguessr.com/game/YOUR_GAME_ID"
```

Enter an active round and confirm readiness in the terminal. Hold Shift while selecting four points:

1. The collapsed map area, used as the hover target.
2. The top-left corner of the expanded map.
3. Its bottom-right corner, excluding borders and the Guess button.
4. The center of the Guess button.

The program captures these actions rather than sending them as game guesses. Disabled buttons can also be selected. Calibration saves the map rectangle, hover point, and button selector. Recalibrate after changing viewport dimensions or map layout.

Automatic map expansion uses hover. If expansion requires a click, manually keep the map expanded during preparation.

## Run a round

Produce a click plan without clicking:

```powershell
.\.venv\Scripts\python.exe -m geoagent browser-run --url "https://www.geoguessr.com/game/YOUR_GAME_ID"
```

Place a pin:

```powershell
.\.venv\Scripts\python.exe -m geoagent browser-run --place --url "https://www.geoguessr.com/game/YOUR_GAME_ID"
```

Place a pin and click Guess:

```powershell
.\.venv\Scripts\python.exe -m geoagent browser-run --submit --url "https://www.geoguessr.com/game/YOUR_GAME_ID"
```

Keep the round and map fixed during analysis. Each execution reanalyzes current images instead of replaying an earlier plan. The browser stays open until terminal confirmation. `--no-wait` skips readiness and review prompts.

## Capture and inspect

```powershell
.\.venv\Scripts\python.exe -m geoagent browser-capture --url "https://www.geoguessr.com/game/YOUR_GAME_ID"
.\.venv\Scripts\python.exe -m geoagent inspect --url "https://www.geoguessr.com/game/YOUR_GAME_ID"
```

Inspection saves visible UI metadata to `visible_ui.json`. It reads basic control attributes, rather than the game's hidden correct location.

## Configuration

| Setting | Purpose |
| --- | --- |
| `browser_channel` | Bundled Chromium (`null`), installed Chrome, or installed Edge |
| `scene_selector` | Capture only the visible scene element; otherwise use the viewport |
| `map_selector` | A single visible map container; preferred over a fixed rectangle |
| `map_rect` | Fixed map capture rectangle |
| `map_hover` | Mouse position for map expansion |
| `submit_selector` | Guess button selector |
| `round_selector` | Visible round identifier for change detection |
| `result_selector` | Result element used to confirm its appearance |
| `settle_ms` | Delay for scene and map loading |
| `min_location_confidence` | Minimum self-estimated location confidence |
| `min_map_confidence` | Minimum self-estimated map-target confidence |
| `map_change_tolerance` | Allowed fraction of changed map pixels before a click |

Without `round_selector`, only the URL identifies a round. Without `result_selector`, status is `submit_clicked`, which does not confirm server acceptance.

## Map placement and limitations

Coordinates are not mouse positions. Maps can pan and zoom, so the program asks the model to locate the destination on the current map image and return image fractions:

```text
browser_x = map_left + map_width × x_fraction
browser_y = map_top  + map_height × y_fraction
```

This is approximate visual placement, not an exact coordinate projection. The program stops for low confidence, unavailable targets, changed rounds, map movement, ambiguous selectors, or disabled submission controls. Clicks and submissions are not retried automatically.

Artifacts include `scene.png`, `location.json`, `map.png`, `map_target.json`, API usage reports, `placed.png`, `after_submit.png`, and `report.json`. Error reports preserve the last completed phase. All artifacts stay under the ignored `runs/` directory.

## Offline integration demo

```powershell
.\.venv\Scripts\python.exe -m geoagent demo
```

The demo uses a synthetic local game and fixed mock predictions. It verifies actual browser screenshots, a pin placed at `(75%, 30%)`, and the Guess/result interaction. It does not validate AI geography accuracy or call the paid API.
