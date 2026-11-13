"""Offline evaluation helpers: remote zip access, sampling, cost and geolocation metrics."""

import io
import math
import random
import statistics
import urllib.request
from collections import defaultdict

from .agent import distance_km

# Distance thresholds used by im2gps-style geolocation benchmarks (street .. continent).
THRESHOLDS_KM = (1, 25, 200, 750, 2500)
USER_AGENT = "geoagent-eval/0.1"


class HttpRangeFile(io.RawIOBase):
    """Seekable read-only view of a remote file using HTTP Range requests.

    zipfile only needs the central directory plus the selected members, so a few small
    requests replace downloading a multi-gigabyte archive.
    """

    def __init__(self, url: str, block_size: int = 1 << 18, timeout: float = 60):
        self.url, self.block_size, self.timeout = url, block_size, timeout
        self.requests = 0
        self.bytes_fetched = 0
        self._pos = 0
        self._cache_start, self._cache = 0, b""
        request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            self.size = int(response.headers["Content-Length"])
            if response.headers.get("Accept-Ranges") != "bytes":
                raise OSError(f"{url} does not support range requests")
            # Follow redirects once so later range requests go straight to the storage host.
            self.url = response.url

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self._pos

    def seek(self, offset, whence=io.SEEK_SET):
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._pos, io.SEEK_END: self.size}[whence]
        self._pos = max(0, base + offset)
        return self._pos

    def _fetch(self, start: int, end: int) -> bytes:
        headers = {"Range": f"bytes={start}-{end - 1}", "User-Agent": USER_AGENT}
        request = urllib.request.Request(self.url, headers=headers)
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            data = response.read()
        self.requests += 1
        self.bytes_fetched += len(data)
        return data

    def read(self, size=-1):
        if self._pos >= self.size:
            return b""
        end = self.size if size is None or size < 0 else min(self.size, self._pos + size)
        cache_end = self._cache_start + len(self._cache)
        if not (self._cache_start <= self._pos and end <= cache_end):
            # Read ahead so zipfile's many small header reads share one request.
            fetch_end = min(self.size, max(end, self._pos + self.block_size))
            self._cache_start, self._cache = self._pos, self._fetch(self._pos, fetch_end)
        offset = self._pos - self._cache_start
        data = self._cache[offset : offset + (end - self._pos)]
        self._pos += len(data)
        return data

    def readinto(self, buffer):
        data = self.read(len(buffer))
        buffer[: len(data)] = data
        return len(data)


def balanced_sample(rows: list[dict], n: int, seed: int = 0, key: str = "country") -> list[dict]:
    """Round-robin over groups so a skewed dataset still yields many different countries."""
    rng = random.Random(seed)
    groups = defaultdict(list)
    for row in rows:
        groups[row[key]].append(row)
    order = sorted(groups)
    rng.shuffle(order)
    for name in order:
        rng.shuffle(groups[name])
    picked = []
    while len(picked) < n and any(groups.values()):
        for name in order:
            if groups[name] and len(picked) < n:
                picked.append(groups[name].pop())
    return picked


def request_cost(usage: dict | None, input_per_m: float, output_per_m: float) -> float:
    """Upper-bound USD cost of one request (cached input is billed at the full rate here)."""
    if not usage:
        return 0.0
    tokens_in = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
    tokens_out = usage.get("output_tokens") or usage.get("completion_tokens") or 0
    return (tokens_in * input_per_m + tokens_out * output_per_m) / 1_000_000


def geoguessr_score(km: float) -> float:
    """Approximate world-map round score (community-derived formula, not official)."""
    return 5000 * math.exp(-km / 1492.7)


def score_prediction(truth: dict, guess: dict) -> dict:
    km = distance_km(truth["latitude"], truth["longitude"], guess["latitude"], guess["longitude"])
    return {
        "error_km": round(km, 2),
        "country_correct": guess.get("country_code", "").upper() == truth["country"].upper(),
        "score": round(geoguessr_score(km)),
    }


def summarize(results: list[dict]) -> dict:
    """Aggregate scored results; failed requests count as misses in every accuracy metric."""
    ok = [r for r in results if r.get("error_km") is not None]
    total = len(results)
    if not total:
        return {"n": 0}
    errors = [r["error_km"] for r in ok]
    summary = {
        "n": total,
        "failed": total - len(ok),
        "median_km": round(statistics.median(errors), 1) if errors else None,
        "mean_km": round(statistics.mean(errors), 1) if errors else None,
        "country_accuracy": sum(r["country_correct"] for r in ok) / total,
        "within_km": {t: sum(e <= t for e in errors) / total for t in THRESHOLDS_KM},
        "mean_score": round(sum(r["score"] for r in ok) / total),
        "cost_usd": round(sum(r.get("cost_usd", 0) for r in results), 4),
    }
    latencies = [r["elapsed_seconds"] for r in ok if r.get("elapsed_seconds") is not None]
    summary["median_latency_s"] = round(statistics.median(latencies), 1) if latencies else None
    return summary
