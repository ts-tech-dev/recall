"""Settings persisted to a JSON file in the data directory, with env-var fallbacks."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

PROVIDERS = ("anthropic", "openai", "none")
EFFORTS = ("low", "medium", "high", "xhigh", "max")


INT_LIMITS = {"max_images": (1, 20), "top_k": (1, 50), "caption_limit": (0, 5000)}


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
    provider: str = "anthropic"
    api_key: str = ""
    model: str = "claude-opus-5-5"
    base_url: str = ""  # OpenAI-compatible endpoint, e.g. http://localhost:11434/v1 for Ollama
    effort: str = "medium"
    send_images: bool = True  # attach retrieved images to the AI request (vision)
    max_images: int = 6
    top_k: int = 12
    semantic_search: bool = True  # local embeddings combined with keyword search
    embed_model: str = "BAAI/bge-small-en-v1.5"
    ocr: bool = True  # read text from images and scanned PDF pages
    caption_images: bool = False  # ask the AI to describe images (costs API calls)
    caption_model: str = ""  # blank = same as `model`
    caption_limit: int = 100  # max new captions per indexing run
    watch: bool = True  # re-index automatically when files change

    def effective_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        env = "ANTHROPIC_API_KEY" if self.provider == "anthropic" else "OPENAI_API_KEY"
        return os.environ.get(env, "")

    def ai_enabled(self) -> bool:
        if self.provider == "none":
            return False
        if self.provider == "openai" and self.base_url:
            return True  # local servers (Ollama, LM Studio) usually need no key
        return bool(self.effective_api_key())

    def public(self) -> dict:
        """Settings safe to send to the browser: the key is masked."""
        d = asdict(self)
        key = self.effective_api_key()
        d["api_key"] = ""
        d["has_key"] = bool(key)
        d["key_hint"] = f"…{key[-4:]}" if len(key) >= 8 else ""
        d["key_from_env"] = bool(key) and not self.api_key
        d["ai_enabled"] = self.ai_enabled()
        return d


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
    os.chmod(tmp, 0o600)  # holds the API key
    tmp.replace(p)


def apply_update(s: Settings, update: dict) -> Settings:
    """Validate and apply a partial update from the UI. An empty api_key keeps the existing one."""
    if "provider" in update and update["provider"] not in PROVIDERS:
        raise ValueError(f"provider must be one of {PROVIDERS}")
    if "effort" in update and update["effort"] not in EFFORTS:
        raise ValueError(f"effort must be one of {EFFORTS}")
    if update.get("notes_dir"):
        nd = Path(update["notes_dir"]).expanduser()
        if not nd.is_dir():
            raise ValueError(f"Not a directory: {nd}")
        update["notes_dir"] = str(nd.resolve())
    for f in fields(Settings):
        if f.name not in update:
            continue
        v = update[f.name]
        if f.name == "api_key":
            if update.get("clear_api_key"):
                s.api_key = ""
            elif v:
                s.api_key = str(v).strip()
            continue
        if f.type in ("int", int):
            lo, hi = INT_LIMITS.get(f.name, (1, 50))
            v = max(lo, min(int(v), hi))
        elif f.type in ("bool", bool):
            v = bool(v)
        else:
            v = str(v).strip()
        setattr(s, f.name, v)
    if update.get("clear_api_key"):
        s.api_key = ""
    return s
