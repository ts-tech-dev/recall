"""Recall works offline: the local models ship with it and are never downloaded unless RECALL_ALLOW_DOWNLOADS is set."""

from __future__ import annotations

import logging
import os
import ssl
import urllib.error
import urllib.request

from .config import downloads_allowed, model_dir

log = logging.getLogger("recall")

PROBE_URL = "https://huggingface.co/api/models/BAAI/bge-base-en-v1.5"


def setup_network() -> None:
    """Offline by default; with downloads allowed, trust the OS certificates (company HTTPS proxies)."""
    if not downloads_allowed():
        # fastembed and huggingface_hub then only read the model folder and make no network requests.
        os.environ["HF_HUB_OFFLINE"] = "1"
        return
    os.environ.pop("HF_HUB_OFFLINE", None)
    use_system_certificates()


def use_system_certificates() -> None:
    """Trust the certificates the OS trusts (Windows certificate store, macOS keychain), not only Python's list.

    Company proxies that inspect HTTPS sign traffic with their own certificate, which IT adds to the OS store.
    Without this, the model downloads fail there with a certificate error.
    """
    try:
        import truststore

        truststore.inject_into_ssl()
    except Exception as e:  # not installed, or an OS it doesn't support: keep Python's own list
        log.info("System certificates not used: %s", e)


def model_load_error(model: str, e: Exception) -> Exception:
    """fastembed only says "could not load model ... from any source"; say why and what to do."""
    network = isinstance(e, OSError) or type(e).__module__.split(".")[0] in ("httpx", "httpcore", "requests", "urllib3")
    if "from any source" not in str(e) and not network:
        return e
    if not downloads_allowed():
        return ValueError(
            f"The model {model} isn't in {model_dir()}. Recall works offline and doesn't download models: pick the "
            f"model Recall came with in Settings, or add this one with `python -m recall.fetch_models`."
        )
    try:
        urllib.request.urlopen(PROBE_URL, timeout=10).close()
        why = f"huggingface.co is reachable, but the download failed ({type(e).__name__}: {e})."
    except urllib.error.HTTPError as he:
        why = f"huggingface.co answered HTTP {he.code} (a proxy or firewall may be blocking it)."
    except urllib.error.URLError as ue:
        r = ue.reason
        if isinstance(r, ssl.SSLCertVerificationError):
            why = ("the HTTPS certificate isn't trusted. A company proxy probably inspects traffic; ask IT to add "
                   "its certificate to the system certificate store.")
        elif isinstance(r, TimeoutError) or "timed out" in str(r):
            why = "huggingface.co didn't answer (blocked by a firewall, or a proxy is needed: set HTTPS_PROXY)."
        else:
            why = f"huggingface.co can't be reached ({r}). If a proxy is needed, set HTTPS_PROXY."
    except Exception as pe:
        why = f"huggingface.co can't be reached ({type(pe).__name__}: {pe})."
    return ValueError(f"Could not download the model {model}: {why}")
