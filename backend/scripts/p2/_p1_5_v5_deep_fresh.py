"""HyperDex P1.5 v5 — Full fresh fetch univers leaderboard avec pagination deep.

Objectif : re-fetcher ALL ~7000 wallets actifs leaderboard, pagination >10k cap,
re-appliquer critères P1.5 (train+/holdout+/sub-window/hold 5min-48h) MAIS PAS
Bonferroni strict. Output : consistent_set_v5.csv pour cohorte paper finale.

ETA ~10-12h. Stream-processing (pas d'OOM), JSONL append-only.
"""
from __future__ import annotations

import csv
import json
import math
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, "/opt/app/hyperdex/backend")

from app.services.hl_api.info_client import InfoClient

OUT_DIR = Path("/opt/app/hyperdex/backend/data/p1")
FILLS_JSONL_V5 = OUT_DIR / "fills_raw_v5.jsonl"
CSV_V5 = OUT_DIR / "consistent_set_v5.csv"
RUN_LOG = OUT_DIR / "v5_run.log"

# Univers filtres (mêmes que P1.5 v4)
MIN_ACCOUNT_VALUE = 5_000.0
MAX_ACCOUNT_VALUE = 100_000_000.0
MIN_WEEKLY_VOLUME = 0.0
HARD_CAP = 10000  # pas de cap réel

# Fenêtre + filtres validation
NOW = datetime.now(timezone.utc)
WINDOW_DAYS = 90
HOLDOUT_DAYS = 30
WINDOW_START_MS = int((NOW - timedelta(days=WINDOW_DAYS)).timestamp() * 1000)
HOLDOUT_CUTOFF_MS = int((NOW - timedelta(days=HOLDOUT_DAYS)).timestamp() * 1000)
N_SUB = 3
MIN_FILLS = 50
HOLD_MIN_MIN = 5
HOLD_MAX_MIN = 48 * 60
MIN_HOLD_N = 20

# Pagination deep
MAX_PAGES_DEEP = 50  # 50 × 500 = 25 000 fills max


def log(msg):
    line = f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(RUN_LOG, "a") as fh:
        fh.write(line + "\n")


def monthly_pnl(row):
    for w, p in row.get("windowPerformances", []):
        if w == "month":
            try:
                return float(p.get("pnl", 0))
            except Exception:
                return 0.0
    return 0.0


def alltime_pnl(row):
    for w, p in row.get("windowPerformances", []):
        if w == "allTime":
            try:
                return float(p.get("pnl", 0))
            except Exception:
                return 0.0
    return 0.0


def weekly_volume(row):
    for w, p in row.get("windowPerformances", []):
        if w == "week":
            try:
                return float(p.get("vlm", 0))
            except Exception:
                return 0.0
    return 0.0


def compute_hold_times(fills):
    sf = sorted(fills, key=lambda f: int(f.get("time", 0)))
    opens = {}
    holds = []
    for f in sf:
        d = (f.get("dir") or "").lower()
        ts = int(f.get("time", 0))
        coin = f.get("coin", "")
        if "open" in d:
            side = "long" if "long" in d else "short"
            opens.setdefault((coin, side), ts)
        elif "close" in d:
            side = "long" if "long" in d else "short"
            start = opens.pop((coin, side), None)
            if start is not None and ts > start:
                holds.append((ts - start) / 60000.0)
    return holds


def sub_window_pos(ts_pnl, n_sub, t_lo, t_hi):
    if t_hi <= t_lo:
        return False
    step = (t_hi - t_lo) // n_sub
    for i in range(n_sub):
        lo, hi = t_lo + i * step, t_lo + (i + 1) * step
        s = sum(p for ts, p in ts_pnl if lo <= ts < hi)
        if s <= 0:
            return False
    return True


def sharpe(pnls):
    if len(pnls) < 5:
        return 0
    m = statistics.mean(pnls)
    s = statistics.stdev(pnls)
    return m / s * math.sqrt(len(pnls)) if s else 0


def top_coin(fills):
    if not fills:
        return "?"
    c = Counter(f.get("coin", "?") for f in fills)
    return c.most_common(1)[0][0]


def validate(addr, fills):
    if len(fills) < MIN_FILLS:
        return None
    holds = compute_hold_times(fills)
    hold_med = statistics.median(holds) if holds else 0
    if not (HOLD_MIN_MIN <= hold_med <= HOLD_MAX_MIN):
        return None
    fs = sorted(fills, key=lambda f: int(f.get("time", 0)))
    ts_pnl = [(int(f.get("time", 0)), float(f.get("closedPnl", 0))) for f in fs]
    train_tp = [x for x in ts_pnl if x[0] < HOLDOUT_CUTOFF_MS]
    hold_tp = [x for x in ts_pnl if x[0] >= HOLDOUT_CUTOFF_MS]
    train_pnls = [p for _, p in train_tp]
    hold_pnls = [p for _, p in hold_tp]
    if not train_pnls or not hold_pnls:
        return None
    if sum(train_pnls) <= 0 or sum(hold_pnls) <= 0:
        return None
    if len(hold_pnls) < MIN_HOLD_N:
        return None
    if not sub_window_pos(train_tp, N_SUB, WINDOW_START_MS, HOLDOUT_CUTOFF_MS):
        return None
    m_h = statistics.mean(hold_pnls)
    s_h = statistics.stdev(hold_pnls) if len(hold_pnls) > 1 else 0
    t_stat = m_h / (s_h / math.sqrt(len(hold_pnls))) if s_h else 0
    return dict(
        addr=addr, n=len(fills), hold_med=hold_med,
        train_pnl=sum(train_pnls), hold_pnl=sum(hold_pnls),
        sharpe=sharpe([p for _, p in ts_pnl]),
        t_stat=t_stat, top_coin=top_coin(fills),
    )


def append_jsonl(path, wallet, fills):
    with open(path, "a") as fh:
        fh.write(json.dumps({"wallet": wallet, "fills": fills}) + "\n")


def load_jsonl_addrs(path):
    addrs = set()
    if not path.exists():
        return addrs
    with open(path) as fh:
        for line in fh:
            try:
                obj = json.loads(line)
                addrs.add(obj["wallet"])
            except Exception:
                continue
    return addrs


def main():
    log("=" * 70)
    log("HyperDex P1.5 v5 — Fresh fetch deep pagination, no Bonferroni strict")
    log("=" * 70)

    # 1. Leaderboard
    ic = InfoClient(mainnet=True, min_interval_s=2.5)  # plus lent pour partager budget avec paper
    log("Fetch leaderboard...")
    lb = ic.fetch_leaderboard()
    log(f"  {len(lb)} traders total")

    universe = []
    for r in lb:
        av = float(r.get("accountValue", 0) or 0)
        mp = monthly_pnl(r)
        ap = alltime_pnl(r)
        wv = weekly_volume(r)
        if (MIN_ACCOUNT_VALUE <= av <= MAX_ACCOUNT_VALUE
                and (mp > 0 or ap > 0) and wv > MIN_WEEKLY_VOLUME):
            universe.append(r["ethAddress"].lower())
    log(f"Univers retenu : {len(universe)}")

    # 2. Fetch deep (resume si existant)
    already = load_jsonl_addrs(FILLS_JSONL_V5)
    log(f"Cache JSONL v5 : {len(already)} déjà fetched")
    to_fetch = [a for a in universe if a not in already]
    log(f"À fetcher : {len(to_fetch)}")

    t0 = time.time()
    for i, addr in enumerate(to_fetch):
        try:
            fills = ic.user_fills_by_time(
                addr, WINDOW_START_MS, max_fills=MAX_PAGES_DEEP * 500)
        except Exception:
            fills = []
        append_jsonl(FILLS_JSONL_V5, addr, fills)
        if (i + 1) % 50 == 0:
            el = time.time() - t0
            eta = el / (i + 1) * (len(to_fetch) - i - 1)
            log(f"  ...{i+1}/{len(to_fetch)}  elapsed={el:.0f}s  ETA={eta:.0f}s  "
                f"hours_left={eta/3600:.1f}h")

    log(f"\nFetch terminé : {len(universe)} wallets dans cache v5")

    # 3. Validate stream
    log("\nRe-validation stream sur cache v5...")
    valid = []
    n_scanned = 0
    with open(FILLS_JSONL_V5) as fh:
        for line in fh:
            try:
                obj = json.loads(line)
            except Exception:
                continue
            n_scanned += 1
            if n_scanned % 500 == 0:
                log(f"  ...{n_scanned}")
            m = validate(obj["wallet"], obj.get("fills", []))
            if m:
                valid.append(m)
    log(f"Wallets validés sub-window-consistent : {len(valid)}")

    valid.sort(key=lambda x: -x["sharpe"])
    with open(CSV_V5, "w") as fh:
        w = csv.DictWriter(fh, fieldnames=["addr", "n", "hold_med",
                                            "train_pnl", "hold_pnl",
                                            "sharpe", "t_stat", "top_coin"])
        w.writeheader()
        for m in valid:
            w.writerow(m)
    log(f"CSV final : {CSV_V5}")

    # Distribution
    buckets = [(0, 30, "<30m"), (30, 60, "30-60m"), (60, 240, "1-4h"),
               (240, 720, "4-12h"), (720, 2880, "12-48h")]
    log("\nDistribution par hold-band :")
    for lo, hi, lbl in buckets:
        sub = [v for v in valid if lo <= v["hold_med"] < hi]
        log(f"  {lbl:<10} : {len(sub)}")


if __name__ == "__main__":
    main()
