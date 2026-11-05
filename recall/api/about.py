"""About Recall: the security and data-handling document, for download from Settings."""

import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import Response

from .. import __version__
from ..config import data_dir, downloads_allowed, model_dir
from .control import launcher

router = APIRouter(prefix="/api")

SECURITY_DOC = Path(__file__).resolve().parent.parent / "docs" / "SECURITY.md"


def _installation(request: Request) -> str:
    """What this copy of the document says about the computer it was downloaded from."""
    mode = "the Windows app" if launcher(request) is not None else "a server started from the command line or Docker"
    return "\n".join([
        f"> **This copy** was downloaded from Recall {__version__} on {time.strftime('%Y-%m-%d')}, running as {mode}",
        f"> at `{str(request.base_url).rstrip('/')}`.",
        f"> Data folder: `{data_dir()}`. Models: `{model_dir()}`. "
        f"Model downloads: {'**allowed** (RECALL_ALLOW_DOWNLOADS is set)' if downloads_allowed() else 'off'}.",
    ])


@router.get("/security-doc")
def security_doc(request: Request):
    text = SECURITY_DOC.read_text(encoding="utf-8")
    text = text.replace("{{version}}", __version__).replace("{{installation}}", _installation(request))
    return Response(text, media_type="text/markdown; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="Recall-{__version__}-security.md"'})
