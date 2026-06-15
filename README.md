# HyperDex

A research-grade **copy-trading framework** for the [Hyperliquid](https://hyperliquid.xyz)
perpetuals DEX, built with a **validation-first discipline**: the trading engine is
deliberately gated behind out-of-sample statistical proof that a copyable edge exists,
so the system never goes live on a strategy that only looks good in-sample.

> ⚠️ **Status: paper / research.** The system defaults to paper / dry-run; live
> execution is gated behind an explicit flag (`LIVE_ENABLED`, off by default) and is
> not enabled here. This repository showcases the data pipeline, the faithful
> simulation engine, the risk layer and the validation methodology — not a claim of
> profitability.

## Why it's built this way

Most retail trading bots are built first and validated never. HyperDex inverts that:
a **discovery + validation phase is a hard gate**. An edge must survive a real temporal
**out-of-sample holdout** *and* a **multiple-testing correction** (Bonferroni / FDR-BH,
sized to the candidate universe) before any execution code is trusted. Exchange
leaderboards are treated as survivorship-biased and never taken at face value.

## Highlights

- **Real-time ingestion** — sharded WebSocket client (Hyperliquid limits: 1000 subs/IP),
  with a watchdog that reconnects on data silence (≥ 90 s) rather than just on socket death.
- **Faithful paper engine** — fills are simulated by *walking the real on-chain order book*
  (`l2Book` snapshots), never via mid-price shortcuts. Paper PnL is meant to track live PnL
  honestly from day one.
- **Risk layer** — liquidation-safe position sizing, drawdown circuit breaker, funding-sign
  gate, depth guard, and automatic muting of underperforming tracked wallets.
- **Resilience** — boot preflight checks, a position reconciler that detects phantom closes,
  and a fill backfiller that recovers fills missed during WebSocket drops.
- **Data engineering at scale** — historical analysis pipeline over Hyperliquid event data
  on AWS S3 (boto3 / IAM), with memory-bounded, checkpoint-resumable processing for
  multi-gigabyte datasets, deployed multi-region (Paris + Tokyo Lightsail) for latency.

## Stack

`Python 3.12` · `FastAPI` · `SQLModel` / `SQLite` · `hyperliquid-python-sdk` ·
`websockets` · `numpy` · `pytest` · `systemd` (VPS) · `AWS S3 / Lightsail`

## Quickstart (dev)

Requires **Python 3.12+** (see `pyproject.toml`).

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp backend/.env.template backend/.env   # set HL_ACCOUNT_ADDRESS + HL_API_PRIVATE_KEY
uvicorn app.main:app --reload --app-dir backend
curl http://127.0.0.1:8000/health
```

> Secrets (`.env`), the API wallet key, the SQLite DB and all data dumps are **never**
> committed — see `.gitignore`. The API wallet is kept separate from the funding wallet
> (Hyperliquid best practice).

## Architecture

```
backend/
├── app/
│   ├── config.py            # env-driven config (no secrets in code)
│   ├── main.py              # FastAPI entrypoint
│   ├── models.py
│   └── services/
│       ├── hl_api/          # Info client + sharded WebSocket client
│       ├── copy/            # copy orchestrator + position sizer
│       ├── execution/       # order submission (paper/live via dry_run flag)
│       └── paper/           # order-book-walking fill simulator, PnL,
│                            #   funding, reconcile, risk guards, wallet perf
├── scripts/                 # discovery + validation research pipeline
│   ├── p1/, p1_6/           # candidate discovery, out-of-sample holdout
│   └── p2/                  # multiple-testing correction (DSR/PBO), paper launch
├── edge_factory/            # standalone edge-validation harness
└── tests/                   # unit tests on the critical bricks
```

## Engineering principles

1. Live trading stays off by default and is enabled only after measured proof
   (profit factor, max drawdown, minimum sample, beats baseline).
2. Paper must be a truthful mirror of live — real order book, never mid-price.
3. Validation = real out-of-sample holdout **with** multiple-testing correction.
4. PnL is the exchange's reported `closedPnl`, never reconstructed.
5. Watchdogs everywhere; no orphan positions; kill-switch; high-water-mark ratchet.

---

*Solo project — autodidact. Built end-to-end: data pipeline, simulation, risk, deployment.*
