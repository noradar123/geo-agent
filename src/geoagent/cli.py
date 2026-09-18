import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAIError
from playwright.sync_api import Error as PlaywrightError

from .agent import play_round
from .browser import browser_session, calibrate, inspect_ui, screenshot_scene
from .demo import run_demo
from .interactive import wait_for_enter
from .screen import analyze_screen
from .storage import load_settings, new_run, save_json
from .vision import Vision


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Capture your open game screen and analyze location"
    )
    result.add_argument("--config", type=Path, default=Path("config.json"))
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("run", "screen", "capture"):
        command = commands.add_parser(name, help="Capture the desktop; run/screen also analyze")
        command.add_argument(
            "--delay",
            type=int,
            default=5,
            choices=range(61),
            metavar="SECONDS",
            help="Countdown before capture, default 5 (0..60)",
        )
        command.add_argument(
            "--region",
            type=int,
            nargs=4,
            metavar=("X", "Y", "WIDTH", "HEIGHT"),
            help="Crop in captured-image pixel coordinates",
        )
        source = command.add_mutually_exclusive_group()
        source.add_argument(
            "--clipboard", action="store_true", help="Use Win+Shift+S clipboard image"
        )
        source.add_argument(
            "--all-screens", action="store_true", help="Include all monitors (Windows)"
        )
        command.add_argument(
            "--capture-only", action="store_true", help="Save PNG without API analysis"
        )
    for name in ("login", "calibrate", "inspect", "browser-capture", "browser-run"):
        command = commands.add_parser(name)
        command.add_argument("--url", help="Game URL, overrides config for this execution")
        command.add_argument(
            "--browser",
            choices=["chrome", "msedge", "chromium"],
            help="Use an installed browser channel; login saves this choice to config",
        )
        if name != "login":
            command.add_argument(
                "--no-wait", action="store_true", help="Skip terminal readiness/review prompts"
            )
        if name == "browser-run":
            mode = command.add_mutually_exclusive_group()
            mode.add_argument("--place", action="store_true", help="Place the map pin")
            mode.add_argument("--submit", action="store_true", help="Place pin and click Guess")
    analyze = commands.add_parser("analyze", help="Analyze PNGs without opening the browser")
    analyze.add_argument("images", type=Path, nargs="+")
    commands.add_parser("demo", help="Headless offline browser test; no OpenAI calls")
    return result


def execute(args):
    load_dotenv()
    if args.command in {"run", "screen", "capture"}:
        capture_only = args.command == "capture" or args.capture_only
        vision = None if capture_only else Vision()
        path = new_run()
        print(f"Artifacts: {path.resolve()}", flush=True)
        report = analyze_screen(
            path,
            vision,
            delay=args.delay,
            region=args.region,
            clipboard=args.clipboard,
            all_screens=args.all_screens,
        )
        if "location" in report:
            guess = report["location"]
            print(f"\n位置：{guess['country']} / {guess['region']} / {guess['city']}")
            print(f"經緯度：{guess['latitude']}, {guess['longitude']}")
            print(
                f"模型信心：{guess['confidence']:.0%}；估計誤差範圍：{guess['uncertainty_km']} km"
            )
            print(f"判斷：{guess['summary']}")
            for clue in guess["clues"]:
                print(f"  - {clue}")
            print(f"完整結果：{(path / 'location.json').resolve()}")
        return
    if args.command == "demo":
        path = run_demo()
        print(f"Offline demo passed (mock AI). Artifacts: {path.resolve()}")
        return
    if args.command == "analyze":
        vision = Vision()
        path = new_run()
        guess = vision.locate(args.images, path)
        save_json(path / "location.json", guess.model_dump())
        print(guess.model_dump_json(indent=2))
        print(f"Artifacts: {path.resolve()}")
        return
    settings = load_settings(args.config)
    if args.url:
        settings.url = args.url
    if args.browser:
        settings.browser_channel = args.browser
    # Fail before opening a browser if the key is missing.
    vision = Vision() if args.command == "browser-run" else None
    with browser_session(settings) as page:
        if args.command == "login":
            wait_for_enter(page, "請在瀏覽器手動登入，完成後按 Enter 儲存登入狀態並關閉：")
            if args.browser:
                save_json(args.config, settings.model_dump())
            return
        if not args.no_wait:
            wait_for_enter(page, "請在瀏覽器進入要操作的遊戲回合，畫面載入完成後按 Enter：")
        if args.command == "calibrate":
            print("依瀏覽器提示按住 Shift 點擊：縮小地圖區 → 展開後左上角 → 右下角 → Guess 按鈕。")
            settings = calibrate(page, settings)
            save_json(args.config, settings.model_dump())
            print(f"Calibration saved: {args.config}")
            return
        path = new_run()
        print(f"Artifacts: {path.resolve()}", flush=True)
        if args.command == "inspect":
            screenshot_scene(page, settings, path / "scene.png")
            ui = inspect_ui(page)
            save_json(path / "visible_ui.json", ui)
            print(json.dumps(ui, ensure_ascii=False, indent=2))
        elif args.command == "browser-capture":
            screenshot_scene(page, settings, path / "scene.png")
            print(f"Saved: {path / 'scene.png'}")
        elif args.command == "browser-run":
            mode = "submit" if args.submit else "place" if args.place else "dry-run"
            report = play_round(page, settings, vision, path, mode)
            print(json.dumps(report, ensure_ascii=False, indent=2))
        if not args.no_wait:
            wait_for_enter(page, "操作完成；可查看瀏覽器與紀錄。按 Enter 關閉：")


def main():
    args = parser().parse_args()
    try:
        execute(args)
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        raise SystemExit(130)
    except (ValueError, RuntimeError, OSError, EOFError, OpenAIError, PlaywrightError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
