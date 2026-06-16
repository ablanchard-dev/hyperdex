"""HyperDex FastAPI app."""
from fastapi import FastAPI

from app.config import get_settings

VERSION = "0.1.0"

app = FastAPI(title="HyperDex", version=VERSION)


@app.get("/")
def root() -> dict[str, str]:
    return {"name": "hyperdex", "version": VERSION, "status": "paper"}


@app.get("/health")
def health() -> dict[str, object]:
    s = get_settings()
    return {
        "ok": True,
        "mode": "paper",
        "network": s.hl_network,
        "account_configured": bool(s.hl_account_address),
    }
