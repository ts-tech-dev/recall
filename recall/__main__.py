"""Run with: python -m recall [--notes DIR] [--port 9999] [--host 127.0.0.1], then open http://localhost:9999"""

import argparse
import os

import uvicorn


def main():
    ap = argparse.ArgumentParser(prog="recall", description="Local notes knowledge base")
    ap.add_argument("--notes", help="notes directory (also settable in the UI)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.environ.get("RECALL_PORT") or 9999))
    a = ap.parse_args()
    if a.notes:
        os.environ["RECALL_NOTES_DIR"] = os.path.abspath(os.path.expanduser(a.notes))
    from .app import create_app
    from .config import load_settings, save_settings

    if a.notes:
        s = load_settings()
        s.notes_dir = os.environ["RECALL_NOTES_DIR"]
        save_settings(s)
    from .access import open_url

    shown = "localhost" if a.host in ("127.0.0.1", "0.0.0.0", "::") else a.host
    app = create_app()
    # The key lets this browser in once; it then keeps it in a cookie (see recall/access.py).
    print(f"Recall running at {open_url(f'http://{shown}:{a.port}/')}", flush=True)
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
