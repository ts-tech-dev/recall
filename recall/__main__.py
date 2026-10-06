"""Run with: python -m recall [--notes DIR] [--port 8765] [--host 127.0.0.1]"""

import argparse
import os

import uvicorn


def main():
    ap = argparse.ArgumentParser(prog="recall", description="Local notes knowledge base")
    ap.add_argument("--notes", help="notes directory (also settable in the UI)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    if a.notes:
        os.environ["RECALL_NOTES_DIR"] = os.path.abspath(os.path.expanduser(a.notes))
    from .app import create_app
    from .config import load_settings, save_settings

    if a.notes:
        s = load_settings()
        s.notes_dir = os.environ["RECALL_NOTES_DIR"]
        save_settings(s)
    print(f"Recall running at http://{a.host}:{a.port}")
    uvicorn.run(create_app(), host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
