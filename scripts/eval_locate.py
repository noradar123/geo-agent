"""Benchmark the street-scene guess (Vision.locate) on OpenStreetView-5M test images.

    python scripts/eval_locate.py prepare --n 100                 # free: sample "uniform100"
    python scripts/eval_locate.py run --sample uniform100 --budget 1.50   # paid
    python scripts/eval_locate.py report                          # free: metrics table

The default sample is uniformly random over the test set, as in the OSV-5M protocol, so the
country mix follows the dataset (roughly by land area). `--strategy balanced` instead takes
one image per country, which over-weights small island territories.

Only the sampled images are fetched (HTTP range requests into the dataset's zip files).
`run` is resumable and stops before a request could push spending past --budget.
Images are CC-BY-SA 4.0 (Mapillary contributors via OSV-5M) and stay under eval/, which
is ignored by Git.
"""

import argparse
import csv
import io
import json
import random
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image

from geoagent.evaluation import (
    THRESHOLDS_KM,
    USER_AGENT,
    HttpRangeFile,
    balanced_sample,
    request_cost,
    score_prediction,
    summarize,
)

BASE = "https://huggingface.co/datasets/osv5m/osv5m/resolve/main"
ROOT = Path("eval/osv5m")
META_FIELDS = ("id", "latitude", "longitude", "country", "region", "city")


def prepare(args):
    ROOT.mkdir(parents=True, exist_ok=True)
    archive = HttpRangeFile(f"{BASE}/images/test/{args.shard}.zip")
    zf = zipfile.ZipFile(archive)
    members = {Path(n).stem: n for n in zf.namelist() if n.endswith(".jpg")}
    print(f"Shard {args.shard}: {len(members)} images listed")

    meta_path = ROOT / f"meta_{args.shard}.csv"
    if not meta_path.exists():
        print("Streaming test.csv labels (about 116 MB, kept only for this shard)...")
        request = urllib.request.Request(f"{BASE}/test.csv", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=120) as response:
            reader = csv.DictReader(io.TextIOWrapper(response, encoding="utf-8", newline=""))
            rows = [{k: r[k] for k in META_FIELDS} for r in reader if r["id"] in members]
        with meta_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, META_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
    with meta_path.open(encoding="utf-8", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["country"]]

    if args.strategy == "balanced":
        sample = balanced_sample(rows, args.n, args.seed)
    else:
        sample = random.Random(args.seed).sample(rows, args.n)
    name = args.sample or f"{args.strategy}{args.n}"
    images = ROOT / "images"
    images.mkdir(exist_ok=True)
    for i, row in enumerate(sample, 1):
        out = images / f"{row['id']}.png"
        if not out.exists():
            # Vision only accepts PNG; the pixels are unchanged.
            Image.open(io.BytesIO(zf.read(members[row["id"]]))).save(out)
        print(f"\r{i}/{len(sample)} images", end="", flush=True)
    print()
    manifest = ROOT / "manifests" / f"{name}.csv"
    manifest.parent.mkdir(exist_ok=True)
    with manifest.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, META_FIELDS)
        writer.writeheader()
        writer.writerows(sample)
    countries = len({r["country"] for r in sample})
    mb = archive.bytes_fetched / 1e6
    print(f"{name}: {len(sample)} images from {countries} countries ({mb:.1f} MB fetched)")


def load_results(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def run(args):
    load_dotenv()
    from pydantic import ValidationError

    from geoagent.vision import Vision, provider_errors

    vision = Vision(provider=args.provider, model=args.model)
    if args.effort is not None:
        vision.scene_effort = args.effort or None
    tag = args.tag or f"{vision.provider}-{vision.model}-{vision.scene_effort or 'default'}"
    with (ROOT / "manifests" / f"{args.sample}.csv").open(encoding="utf-8", newline="") as f:
        manifest = list(csv.DictReader(f))[: args.limit]
    results_path = ROOT / "results" / args.sample / f"{tag}.jsonl"
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results = load_results(results_path)
    done = {r["id"] for r in results}
    spent = sum(r.get("cost_usd", 0) for r in results)
    recoverable = (*provider_errors(), RuntimeError, ValidationError, OSError)
    todo = [row for row in manifest if row["id"] not in done]
    print(f"{args.sample} / {tag}: {len(done)} done, {len(todo)} to go, ${spent:.3f} spent of ${args.budget:.2f}")

    for row in todo:
        observed = [r["cost_usd"] for r in results if r.get("cost_usd")]
        next_cost = max(observed) if observed else args.first_estimate
        if spent + next_cost > args.budget:
            print(f"Budget stop: next request may cost ~${next_cost:.3f}, ${spent:.3f} spent")
            break
        run_dir = ROOT / "runs" / args.sample / tag / row["id"]
        run_dir.mkdir(parents=True, exist_ok=True)
        truth = {**row, "latitude": float(row["latitude"]), "longitude": float(row["longitude"])}
        record = {"id": row["id"], "country": row["country"]}
        try:
            guess = vision.locate([ROOT / "images" / f"{row['id']}.png"], run_dir).model_dump()
            record.update(score_prediction(truth, guess))
            record.update(
                pred_country=guess["country_code"],
                pred_latitude=guess["latitude"],
                pred_longitude=guess["longitude"],
                confidence=guess["confidence"],
            )
        except recoverable as exc:  # recorded as a miss; the loop continues
            record.update(error=f"{type(exc).__name__}: {exc}", error_km=None)
        log = run_dir / "location_api.json"
        if log.exists():
            api = json.loads(log.read_text(encoding="utf-8"))
            record["elapsed_seconds"] = api.get("elapsed_seconds")
            record["usage"] = api.get("usage")
            record["cost_usd"] = round(
                request_cost(api.get("usage"), args.input_price, args.output_price), 5
            )
        spent += record.get("cost_usd", 0)
        results.append(record)
        with results_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        status = record.get("error") or (
            f"{record['error_km']:>8.1f} km  {record['country']}->{record['pred_country']}"
        )
        print(f"[{len(results):>3}] {row['id']}  {status}  (${spent:.3f})", flush=True)
        if "error" in record and "429" in record["error"]:
            print("Rate limited or out of credit; stopping.")
            break
        time.sleep(args.pause)
    report(args)


def report(args):
    lines = [
        "| Sample | Run | n | Median km | Country | "
        + " | ".join(f"≤{t} km" for t in THRESHOLDS_KM)
        + " | Score | Latency | Cost |",
        "|" + "---|" * (8 + len(THRESHOLDS_KM)),
    ]
    for path in sorted((ROOT / "results").glob("*/*.jsonl")):
        s = summarize(load_results(path))
        if not s["n"]:
            continue
        within = " | ".join(f"{s['within_km'][t]:.0%}" for t in THRESHOLDS_KM)
        lines.append(
            f"| {path.parent.name} | {path.stem} | {s['n']} | {s['median_km']} | {s['country_accuracy']:.0%} | "
            f"{within} | {s['mean_score']} | {s['median_latency_s']} s | ${s['cost_usd']:.2f} |"
        )
    table = "\n".join(lines)
    print(table)
    (ROOT / "summary.md").write_text(table + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare", help="download a sample of test images (no API calls)")
    p.add_argument("--n", type=int, default=100)
    p.add_argument("--strategy", choices=("uniform", "balanced"), default="uniform")
    p.add_argument("--sample", help="sample name (default: strategy + n, e.g. uniform100)")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--shard", default="00", help="test zip to sample from (00-04)")
    r = sub.add_parser("run", help="call the vision model on a sample (paid)")
    r.add_argument("--sample", required=True, help="sample name from prepare")
    r.add_argument("--budget", type=float, default=1.0, help="hard USD cap for this run tag")
    r.add_argument("--limit", type=int, help="only the first N manifest images")
    r.add_argument("--provider")
    r.add_argument("--model")
    r.add_argument("--effort", help="scene reasoning effort; empty string for none")
    r.add_argument("--tag", help="results name (default: provider-model-effort)")
    r.add_argument("--input-price", type=float, default=2.0, help="USD per 1M input tokens")
    r.add_argument("--output-price", type=float, default=10.0, help="USD per 1M output tokens")
    r.add_argument("--first-estimate", type=float, default=0.03, help="USD guess before data")
    r.add_argument("--pause", type=float, default=0.5, help="seconds between requests")
    sub.add_parser("report", help="print and save the metrics table")
    args = parser.parse_args(argv)
    # Windows consoles default to a legacy code page that cannot print the table symbols.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    {"prepare": prepare, "run": run, "report": report}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
