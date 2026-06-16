# HyperDex

A research-grade **copy-trading framework** for the [Hyperliquid](https://hyperliquid.xyz)
perpetuals DEX, built with a **validation-first discipline**: edge research is done in a
separate harness before any strategy is taken seriously, so the project never builds a
live engine on top of a strategy that only looks good in-sample.

> **Status: paper-only / research.** This repository runs in paper / dry-run only —
> there is no live trading runner. The order-submission client (`execution/exchange.py`)
> contains a live branch, but it is not wired into any entry point and is not gated by a
> runtime flag; the only runner (`scripts/p2/launch_paper.py`) forces `dry_run=True`.
> The repo showcases the data pipeline, the faithful simulation engine, the risk layer
> and the validation methodology — not a live system and not a claim of profitability.

## Why it's built this way

Most retail trading bots are built first and validated never. HyperDex inverts that:
the **discovery + validation work comes first**, in a separate research harness
(`edge_factory/`, `scripts/`). It is a manual research workflow, not a runtime gate
wired into the orchestrator — `app/` does not import the research code. An edge is only
taken seriously after it survives a real temporal **out-of-sample holdout** *and* a
**multiple-testing correction** (Bonferroni / FDR-BH, sized to the candidate universe).
Exchange leaderboards are treated as survivorship-biased and never taken at face value.

## Highlights

- **Real-time ingestion** — sharded WebSocket client (Hyperliquid limits: 1000 subs/IP),
  with a watchdog that reconnects on data silence (per-shard 180 s, global 1800 s) rather
  than just on socket death.
- **Faithful paper engine** — fills are simulated by *walking the real on-chain order book*
  (`l2Book` snapshots), never via mid-price shortcuts, so paper fills stay close to what a
  live order would have gotten.
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

1. Paper-only by design — there is no live runner here. Live execution would only be
   built after measured proof (profit factor, max drawdown, minimum sample, beats baseline).
2. Paper aims to be a truthful mirror of a live fill — real order book, never mid-price.
3. Validation = real out-of-sample holdout **with** multiple-testing correction, run as a
   manual research workflow (not coupled into the orchestrator).
4. On a live path, PnL would be taken from the exchange's reported `closedPnl`, not
   reconstructed. In this paper repo, PnL is reconstructed from simulated fills.
5. Watchdogs everywhere; no orphan positions; kill-switch; high-water-mark ratchet.

---

*Solo project — autodidact. Built end-to-end: data pipeline, simulation, risk, deployment.*
