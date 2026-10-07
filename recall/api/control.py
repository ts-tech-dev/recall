"""The desktop app's controls: move to another port, shut down. Only present when started by recall.desktop."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..config import INT_LIMITS, save_settings
from ..desktop import port_free
from ..state import get_state

router = APIRouter(prefix="/api/app")


class PortBody(BaseModel):
    port: int


def launcher(request: Request):
    """The running desktop Launcher, or None (command line, Docker, tests)."""
    return getattr(request.app.state, "launcher", None)


def app_info(request: Request) -> dict | None:
    ln = launcher(request)
    return None if ln is None else {"port": ln.port, "port_locked": ln.port_locked}


def _require(request: Request):
    ln = launcher(request)
    if ln is None:
        raise HTTPException(404, "Only the Recall app can do this")
    return ln


@router.post("/port")
def change_port(body: PortBody, request: Request):
    """Save the new port and restart the server there. Answers with the new address."""
    ln = _require(request)
    lo, hi = INT_LIMITS["port"]
    if not lo <= body.port <= hi:
        raise HTTPException(400, f"Pick a port from {lo} to {hi}")
    if ln.port_locked:
        raise HTTPException(409, "The port is set by the RECALL_PORT environment variable")
    state = get_state(request)
    if body.port != ln.port and not port_free(body.port):
        raise HTTPException(409, f"Port {body.port} is in use by another program")
    with state.lock:
        state.settings.port = body.port
        save_settings(state.settings)
    if body.port != ln.port:
        ln.restart(body.port)
    return {"url": f"http://localhost:{body.port}/"}


@router.post("/shutdown")
def shutdown(request: Request):
    _require(request).shutdown()
    return {"ok": True}
