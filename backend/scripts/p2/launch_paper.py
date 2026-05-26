"""HyperDex paper launcher — full stack P2.

Composants wired :
  - WsUserFillsListener (38 wallets cohorte, watchdog 90s)
  - ExchangeClient (dry_run=True, FillSimulator via paper.fill_simulator)
  - PnLTracker (état + JSONL avec PaperPosition perp model)
  - FundingAccrual (loop hourly snapshot)
  - CopyOrchestrator (bridge)
  - CopySizer (target $25 notional 5x leverage)
"""
from __future__ import annotations

import asyncio
import csv
import json
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, "/home/dexter/hyperdex/backend")

from hyperliquid.info import Info
from hyperliquid.utils import constants

from app.services.copy.orchestrator import CopyOrchestrator, is_skipped_coin
from app.services.copy.sizer import CopySizer
from app.services.execution import ExchangeClient, LatencyModel
from app.services.hl_api.ws_user_fills import WsUserFillsListener
from app.services.paper import FundingAccrual, PnLTracker
from app.services.paper.reconcile import PositionReconciler
from app.services.paper.wallet_perf import WalletPerformanceTracker
from app.services.paper.preflight import run_preflight, PreflightError
from app.services.paper.fill_backfiller import FillBackfiller


DETAILED_CSV = Path("/home/dexter/hyperdex/backend/data/p1/consistent_set.csv")
PAPER_DIR = Path("/home/dexter/hyperdex/backend/data/paper")
PAPER_DIR.mkdir(parents=True, exist_ok=True)
POSITIONS_LOG = PAPER_DIR / "positions.jsonl"
LAUNCHER_LOG = PAPER_DIR / "launcher.log"
MUTED_PATH = PAPER_DIR / "muted_wallets.json"
WALLET_PERF_PATH = PAPER_DIR / "wallet_perf.json"
BACKFILLER_STATE_PATH = PAPER_DIR / "fill_backfiller_state.json"

# Cohorte filters — étendue à TOUS les Bonferroni-validés
# Sharpe 0 = aucun filtre Sharpe (tous Bonferroni passent z>4.34 déjà)
MIN_SHARPE = 0.0

# Sizing R-based (doctrine polyoracle, herité durci) :
# - PAPER_CAPITAL = label opérateur (NANO=$300)
# - R_PCT = 2% du capital risqué par position (margin)
# - DEFAULT_LEVERAGE = levier perp HL
# - TARGET_NOTIONAL = capital * R * leverage = exposition par trade
PAPER_CAPITAL = 300.0
R_PCT = 0.02
DEFAULT_LEVERAGE = 5.0
TARGET_NOTIONAL = PAPER_CAPITAL * R_PCT * DEFAULT_LEVERAGE  # $30 = 10% capital expo / pos

# Concurrent caps — à NANO $300, max_concurrent=10 limite expo à $300 = 100% capital
# (margin total = 100% × R% = 20% capital ; liquidation requiert -20% sur 1 position lev5)
MAX_CONCURRENT = 20
# 5 = 50% capital max sur 1 coin (5 × $30 = $150 sur $300). Plus permissif que 3
# pour ne pas rater les signaux convergents quand plusieurs wallets cohorte
# tradent le même coin.
# MAX_CONCURRENT bumped 10→20 le 2026-05-25 : portfolio saturé 10/10 pendant
# 14h+ (wallets ne ferment pas swing positions), 324 REJECT en peak US =
# signal raté. À 20 × $30 = $600 expo théorique (200% capital) - risk levier
# effectif ~10x sur $300 capital. Acceptable en paper, à reconsidérer en live.
MAX_PER_ASSET = {
    # Coins liquides où la cohorte 232 converge naturellement (100+ wallets
    # peuvent toucher HYPE simultanément). Cap 8 = capture plus de signal majeur
    # sans saturer ($30 × 8 = $240 expo / coin sur $300 capital = 80%).
    "HYPE": 8,
    "ETH": 8,
    "BTC": 8,
    # Alts moins liquides : cap prudent reste 5 (volatilité plus haute,
    # liq tighter, concentration risque concrète).
    "_default": 5,
}

# Latence simulée copy
LATENCY_MIN_MS = 200
LATENCY_MAX_MS = 800


def load_cohort() -> list[dict]:
    rows: list[dict] = []
    with open(DETAILED_CSV) as fh:
        reader = csv.DictReader(fh)
        for r in reader:
            try:
                r["sharpe"] = float(r.get("sharpe", 0))
                r["hold_med"] = float(r.get("hold_med", 0))
                r["total_pnl"] = float(r.get("total_pnl", 0))
                rows.append(r)
            except Exception:
                continue
    # hold_med >=5min : seul filtre statique = perps-only + ce floor cosmétique.
    # Le verdict P1.5 mesure faux le hold_med (fills atomiques au lieu de positions
    # logiques) → filtrer sur cette métrique = filtrer du bruit. Le runtime mute
    # de l'orchestrator (médiane des 5+ derniers holds < 30s → mute) fait le vrai
    # boulot, sur observation réelle, et est réversible.
    filtered = [
        r for r in rows
        if r["sharpe"] >= MIN_SHARPE
        and not is_skipped_coin(r.get("top_coin", ""))
        and 5 <= r["hold_med"] <= 48 * 60
    ]
    filtered.sort(key=lambda r: -r["sharpe"])
    return filtered


def log(msg: str):
    line = f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LAUNCHER_LOG, "a") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


async def main():
    log("=" * 70)
    log("HYPERDEX PAPER FULL P2 — copy paper avec FillSimulator + FundingAccrual")
    log("=" * 70)

    # 1. cohorte
    cohort = load_cohort()
    log(f"Cohorte chargée : {len(cohort)} wallets (Sharpe ≥ {MIN_SHARPE}, perps-only, hold 5min-48h)")
    if not cohort:
        log("AUCUN wallet — STOP")
        return
    log("Top 5 :")
    for i, r in enumerate(cohort[:5]):
        log(f"  #{i+1} {r['addr'][:14]} Sharpe={r['sharpe']:.2f} "
            f"hold={r['hold_med']:.0f}min top_coin={r['top_coin']}")
    addresses = [r["addr"] for r in cohort]

    # ─── Option B (2026-05-26) — Split HOT (WS) vs WARM (REST backfiller) ───
    # HL limite WS à 10 conn × 10 users = 100 wallets/IP. Cohort 232 dépasse.
    # On split en : HOT 100 (top score = WS sub-second) + WARM le reste (REST 60s).
    # Source : https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits
    MAX_HOT_WS = 100
    # Score = sharpe + log(1+activity_observée_24h). On lit l'activity
    # depuis positions.jsonl (proxy = nb opens observés dans les dernières 24h).
    import math
    now_ms = int(time.time() * 1000)
    cutoff_24h_ms = now_ms - 24 * 3600 * 1000
    activity_24h: dict[str, int] = {}
    if POSITIONS_LOG.exists():
        try:
            with open(POSITIONS_LOG) as fh:
                for line in fh:
                    try:
                        ev = json.loads(line)
                    except Exception:
                        continue
                    if ev.get("event") != "open":
                        continue
                    t = int(ev.get("open_ts_ms", 0))
                    if t < cutoff_24h_ms:
                        continue
                    tr = (ev.get("trader") or "").lower()
                    if tr:
                        activity_24h[tr] = activity_24h.get(tr, 0) + 1
        except Exception as e:
            log(f"[HOT_TIER] activity scan fail: {type(e).__name__}: {e}")
    # Scoring : sharpe brut + boost log(1+activity)
    scored = []
    for r in cohort:
        addr_l = r["addr"].lower()
        sharpe = float(r.get("sharpe", 0))
        act = activity_24h.get(addr_l, 0)
        score = sharpe + 2.0 * math.log1p(act)  # 2.0 = poids activité observée
        scored.append((score, r["addr"]))
    scored.sort(key=lambda x: -x[0])
    hot_addresses = [a for _, a in scored[:MAX_HOT_WS]]
    warm_addresses = [a for _, a in scored[MAX_HOT_WS:]]
    n_active_hot = sum(1 for a in hot_addresses if activity_24h.get(a.lower(), 0) > 0)
    log(f"[HOT_TIER] HOT (WS) : {len(hot_addresses)} wallets "
        f"(dont {n_active_hot} actifs 24h)")
    log(f"[WARM_TIER] WARM (REST backfiller) : {len(warm_addresses)} wallets")

    # 2. setup
    info = Info(constants.MAINNET_API_URL, skip_ws=True)

    # ─── PREFLIGHT health check (Phase A bonus) ───
    # Valide HL API, cohorte CSV, muted, funding, l2_snapshot, user_state.
    # Abort proper si critical fail.
    try:
        run_preflight(
            info=info,
            cohort_csv=DETAILED_CSV,
            muted_path=MUTED_PATH,
            wallet_perf_path=WALLET_PERF_PATH,
            sample_coin="BTC",
        )
    except PreflightError as e:
        log(f"PREFLIGHT FAILED — abort: {e}")
        return

    tracker = PnLTracker(POSITIONS_LOG)
    # state recovery — réimporte les open positions du JSONL si crash précédent
    restore_stats = tracker.restore_from_jsonl()
    log(f"[RESTORE] events={restore_stats['events']} opens={restore_stats['opens']} "
        f"closes={restore_stats['closes']} funding={restore_stats['funding']} "
        f"bad={restore_stats['bad_lines']} → open_restored={restore_stats['open_restored']}")

    exchange = ExchangeClient(
        dry_run=True, info=info,
        latency_model=LatencyModel(min_ms=LATENCY_MIN_MS, max_ms=LATENCY_MAX_MS),
    )
    # A1+A2 : sizer reçoit info (funding rate) + tracker (drawdown_pct)
    sizer = CopySizer(
        target_notional=TARGET_NOTIONAL,
        max_concurrent=MAX_CONCURRENT,
        max_per_asset=MAX_PER_ASSET,
        info=info,
        tracker=tracker,
    )
    # A4 : per-wallet performance + auto-mute négatif
    wallet_perf = WalletPerformanceTracker(persist_path=WALLET_PERF_PATH)
    orchestrator = CopyOrchestrator(
        exchange=exchange, sizer=sizer, tracker=tracker,
        default_leverage=DEFAULT_LEVERAGE, verbose=True,
        muted_path=MUTED_PATH,
        wallet_perf=wallet_perf,
    )
    # bootstrap anti-HFT depuis l'historique paper accumulé
    orchestrator.bootstrap_holds_from_jsonl(POSITIONS_LOG)
    funding = FundingAccrual(info=info, tracker=tracker, dry_run=True)
    # Reconcile : détecte positions phantom (close ratés via WS) toutes les 5min
    reconciler = PositionReconciler(tracker=tracker, info=info, verbose=True)
    # FillBackfiller : tier WARM uniquement (option B 2026-05-26).
    # 132 wallets WARM / 60s × 33 batch = full cycle 4min, sous budget HL.
    # Si len(warm_addresses) = 0 (cohort ≤ 100), backfiller idle.
    backfiller = FillBackfiller(
        addresses=warm_addresses,
        orchestrator=orchestrator,
        info=info,
        state_path=BACKFILLER_STATE_PATH,
        batch_per_loop=max(20, min(33, len(warm_addresses) // 4 or 1)),
        loop_interval_s=60.0,
        initial_lookback_s=300,
        verbose=True,
    )
    log(f"Setup : target=${TARGET_NOTIONAL} lev={DEFAULT_LEVERAGE}x max_conc={MAX_CONCURRENT} max/asset={MAX_PER_ASSET}")
    log(f"Latence simulée : {LATENCY_MIN_MS}-{LATENCY_MAX_MS}ms")
    log(f"Positions JSONL → {POSITIONS_LOG}")

    # 3. WS listener — tier HOT uniquement (option B 2026-05-26).
    # Respecte limite HL 10 conn × 10 users = 100 wallets/IP max.
    listener = WsUserFillsListener(
        addresses=hot_addresses,
        on_fill=orchestrator.on_trader_fill,
        testnet=False,
        ignore_initial_snapshot=True,
    )

    # 4. summary loop (5 min)
    async def summary_loop():
        while True:
            await asyncio.sleep(300)
            s = tracker.summary()
            log(f"[SUMMARY] open={s['open']} opens={s['n_opens']} "
                f"closes={s['n_closes']} wins={s['wins']} losses={s['losses']} "
                f"WR={s['wr_pct']:.1f}% total_pnl=${s['total_pnl']:+.2f} "
                f"funding_total=${s['total_funding_paid']:+.4f}")
            log(f"[STATS] {orchestrator.stats}")

    # 5. signal handlers
    stop_event = asyncio.Event()

    def _shutdown(*a):
        log("SIGNAL received — arrêt propre")
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            asyncio.get_event_loop().add_signal_handler(sig, _shutdown)
        except (NotImplementedError, RuntimeError):
            pass

    # 6. lance tout
    log("Lancement WS + funding loop + summary loop + reconciler + backfiller")
    listener_task = asyncio.create_task(listener.run())
    funding_task = asyncio.create_task(funding.run())
    summary_task = asyncio.create_task(summary_loop())
    reconcile_task = asyncio.create_task(reconciler.run())
    backfill_task = asyncio.create_task(backfiller.run())
    stop_task = asyncio.create_task(stop_event.wait())

    done, pending = await asyncio.wait(
        {listener_task, funding_task, summary_task, reconcile_task,
         backfill_task, stop_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    log("Une tâche s'est terminée — shutdown")
    listener.stop()
    funding.stop()
    reconciler.stop()
    backfiller.stop()
    for t in pending:
        t.cancel()
    log("STOPPED")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("KeyboardInterrupt — bye")
