import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .models import Settings


def load_settings(path: Path) -> Settings:
    if not path.exists():
        raise ValueError(f"Missing {path}. Copy config.example.json to {path} first.")
    return Settings.model_validate_json(path.read_text(encoding="utf-8-sig"))


def save_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def new_run(root: Path = Path("runs")) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = root / f"{stamp}-{uuid4().hex[:8]}"
    path.mkdir(parents=True)
    return path
