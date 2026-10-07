"""Settings persisted to a JSON file in the data directory, with env-var fallbacks."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

INT_LIMITS = {"top_k": (1, 50)}


def model_dir() -> Path:
    """Where downloaded local models (embeddings) are cached."""
    d = Path(os.environ.get("RECALL_MODEL_DIR", data_dir() / "models")).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d


def data_dir() -> Path:
    d = Path(os.environ.get("RECALL_DATA_DIR", Path.home() / ".recall")).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class Settings:
    notes_dir: str = ""
    top_k: int = 12  # passages Ask retrieves
    semantic_search: bool = True  # local embeddings combined with keyword search
    embed_model: str = "BAAI/bge-base-en-v1.5"  # ~210 MB; bge-small-en-v1.5 (~70 MB) is faster, less accurate
    rerank: bool = True  # re-order the best results with a local cross-encoder (better ranking, ~0.5 s per search)
    rerank_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"  # ~80 MB
    ocr: bool = True  # read text from images and scanned PDF pages
    watch: bool = True  # re-index automatically when files change

    def public(self) -> dict:
        """Settings as sent to the browser."""
        return asdict(self)


def config_path() -> Path:
    return data_dir() / "config.json"


def load_settings() -> Settings:
    s = Settings()
    p = config_path()
    if p.exists():
        try:
            raw = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            raw = {}
        names = {f.name for f in fields(Settings)}
        for k, v in raw.items():
            if k in names:
                setattr(s, k, v)
    if not s.notes_dir and os.environ.get("RECALL_NOTES_DIR"):
        s.notes_dir = os.environ["RECALL_NOTES_DIR"]
    return s


def save_settings(s: Settings) -> None:
    p = config_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(s), indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(p)


def apply_update(s: Settings, update: dict) -> Settings:
    """Validate and apply a partial update from the UI."""
    if update.get("notes_dir"):
        nd = Path(update["notes_dir"]).expanduser()
        if not nd.is_dir():
            raise ValueError(f"Not a directory: {nd}")
        update["notes_dir"] = str(nd.resolve())
    for f in fields(Settings):
        if f.name not in update:
            continue
        v = update[f.name]
        if f.type in ("int", int):
            lo, hi = INT_LIMITS.get(f.name, (1, 50))
            v = max(lo, min(int(v), hi))
        elif f.type in ("bool", bool):
            v = bool(v)
        else:
            v = str(v).strip()
        setattr(s, f.name, v)
    return s
