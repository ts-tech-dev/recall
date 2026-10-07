"""Download local models into a folder that ships with Recall, so the app never needs the internet.

    python -m recall.fetch_models [--out DIR] [MODEL ...]

Without MODEL names it fetches the default embedding and re-ranking models. DIR defaults to `models/` next to the
`recall` package, which the Windows build and the Docker image pack into the app.
"""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

from .config import BUNDLED_MODELS, Settings


def _flatten(root: Path) -> None:
    """Replace Hugging Face's symlinks (snapshots -> blobs) with the files and drop the blobs and locks.

    The folder then copies anywhere (PyInstaller, zip, another PC) without doubling in size or breaking links.
    """
    for link in [p for p in root.rglob("*") if p.is_symlink()]:
        target = link.resolve()
        link.unlink()
        shutil.copy2(target, link)
    for junk in [*root.rglob("blobs"), *root.rglob(".locks")]:
        if junk.is_dir():
            shutil.rmtree(junk)


def main() -> None:
    s = Settings()
    ap = argparse.ArgumentParser(prog="python -m recall.fetch_models", description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=BUNDLED_MODELS, help=f"folder to download into (default {BUNDLED_MODELS})")
    ap.add_argument("models", nargs="*", help=f"model names (default {s.embed_model} and {s.rerank_model})")
    a = ap.parse_args()
    os.environ.pop("HF_HUB_OFFLINE", None)
    from fastembed import TextEmbedding
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    from .net import use_system_certificates

    use_system_certificates()
    a.out.mkdir(parents=True, exist_ok=True)
    rerankers = {m["model"].lower() for m in TextCrossEncoder.list_supported_models()}
    for name in a.models or [s.embed_model, s.rerank_model]:
        print(f"Fetching {name} ...", flush=True)
        cls = TextCrossEncoder if name.lower() in rerankers else TextEmbedding
        cls(name, cache_dir=str(a.out))
    _flatten(a.out)
    print(f"Models are in {a.out}")


if __name__ == "__main__":
    main()
