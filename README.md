# HyperDex

Hyperliquid copy-trading bot (perps DEX). Build neuf — voir `CLAUDE.md` pour
le contexte complet, la doctrine et les phases.

Stack : Python 3.12 + FastAPI + SQLModel + SQLite + `hyperliquid-python-sdk`.

## Quickstart (dev)

```bash
cd /home/dexter/hyperdex
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp backend/.env.template backend/.env  # remplir HL_ACCOUNT_ADDRESS + HL_API_PRIVATE_KEY
uvicorn app.main:app --reload --app-dir backend
```

Sanity : `curl http://127.0.0.1:8000/health`.

## Phase courante

**P0 — Fondation.** Scaffold en place. Suite : P0.2 (install SDK), P0.3 (info
client), P0.4 (WS client + watchdog), P0.5 (smoke end-to-end).

Décision-clé : **P1 est un GATE**, voir `CLAUDE.md`.
