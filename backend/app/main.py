"""HyperDex FastAPI app — scaffold P0.1."""
from fastapi import FastAPI

from app.config import get_settings

app = FastAPI(title="HyperDex", version="0.0.1")


@app.get("/")
def root() -> dict[str, str]:
    return {"name": "hyperdex", "version": "0.0.1", "status": "scaffold-P0.1"}


@app.get("/health")
def health() -> dict[str, object]:
    s = get_settings()
    return {
        "ok": True,
        "network": s.hl_network,
        "account_configured": bool(s.hl_account_address),
        "paper_only": s.paper_only,
        "live_enabled": s.live_enabled,
    }
