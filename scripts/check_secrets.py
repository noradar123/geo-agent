"""Check staged files or committed history without printing secret values."""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

PATTERNS = {
    "OpenAI key": re.compile(rb"sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}"),
    "GitHub token": re.compile(rb"(?:gh[opsur]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}
PRIVATE_PARTS = {".browser-profile", ".playwright-browsers", ".venv", ".codex", ".agents", "runs"}


def git(*args: str) -> bytes:
    result = subprocess.run(["git", *args], capture_output=True, check=True)
    return result.stdout


def local_keys(root: Path) -> list[bytes]:
    """Read local API values for exact matching; never log those values."""
    keys = []
    value = os.getenv("OPENAI_API_KEY", "").strip()
    if value:
        keys.append(value.encode())
    env_file = root / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            match = re.match(r"\s*(?:export\s+)?OPENAI_API_KEY\s*=\s*(.*)", line)
            if match:
                value = match[1].strip()
                if value.startswith(("'", '"')):
                    value = value[1:].split(value[0], 1)[0]
                else:
                    value = value.split(" #", 1)[0].strip()
                if len(value) >= 12:
                    keys.append(value.encode())
    return keys


def prohibited(path: str) -> bool:
    file = PurePosixPath(path)
    if path == ".env.example":
        return False
    return (
        any(part in PRIVATE_PARTS for part in file.parts)
        or file.name.startswith(".env")
        or file.name in {"config.json", "credentials.json", "secrets.json"}
        or file.suffix.lower() in {".pem", ".key", ".log"}
    )


def staged_blobs() -> list[tuple[str, str]]:
    entries = []
    for record in git("ls-files", "--stage", "-z").split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        _, blob, _ = metadata.decode().split()
        entries.append((raw_path.decode("utf-8"), blob))
    return entries


def history_blobs() -> list[tuple[str, str]]:
    entries = set()
    for commit in git("rev-list", "--all").decode().splitlines():
        for record in git("ls-tree", "-r", "-z", commit).split(b"\0"):
            if not record:
                continue
            metadata, raw_path = record.split(b"\t", 1)
            _, kind, blob = metadata.decode().split()
            if kind == "blob":
                entries.add((raw_path.decode("utf-8"), blob))
    return sorted(entries)


def check(entries: list[tuple[str, str]], keys: list[bytes]) -> list[tuple[str, str]]:
    findings = []
    contents = {}
    for path, blob in entries:
        if prohibited(path):
            findings.append((path, "private local file"))
        if blob not in contents:
            contents[blob] = git("cat-file", "blob", blob)
        data = contents[blob]
        if any(key in data or key in path.encode() for key in keys):
            findings.append((path, "matches a local API key"))
        for label, pattern in PATTERNS.items():
            if pattern.search(data):
                findings.append((path, label))
    return findings


def redacted_path(path: str, keys: list[bytes]) -> str:
    """Redact a secret even if it accidentally appears in a file name."""
    data = path.encode()
    for key in keys:
        data = data.replace(key, b"[REDACTED]")
    for pattern in PATTERNS.values():
        data = pattern.sub(b"[REDACTED]", data)
    return data.decode("utf-8", errors="replace")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true", help="Scan every reachable commit")
    args = parser.parse_args()
    try:
        root = Path(git("rev-parse", "--show-toplevel").decode().strip())
        entries = history_blobs() if args.history else staged_blobs()
        keys = local_keys(root)
        findings = check(entries, keys)
    except (OSError, ValueError, subprocess.CalledProcessError):
        print("Secret check could not finish; commit/push cancelled.", file=sys.stderr)
        raise SystemExit(2)
    if findings:
        for path, label in findings:
            # Report labels only: neither secret text nor matching lines are displayed.
            print(f"Blocked: {redacted_path(path, keys)} ({label})", file=sys.stderr)
        raise SystemExit(1)
    print(f"Secret check passed: {len(entries)} file versions; no values printed.")


if __name__ == "__main__":
    main()
