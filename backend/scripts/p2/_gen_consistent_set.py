"""Génère le set sub-window-consistent (~335 wallets) depuis cache P1.5.

Critères :
- hold_med entre 5min et 48h
- n_fills >= 50
- train_pnl > 0 ET holdout_pnl > 0
- hold_n >= 20
- sub-window 3/3 train positives
- holdout sum > 0

PAS de filtre Bonferroni strict. Le paper triera les false positives.
"""
from __future__ import annotations

import csv
import json
import math
import statistics
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

JSONL = Path("/home/dexter/hyperdex/backend/data/p1/fills_raw_p1_5.jsonl")
DEEP_JSONL = Path("/home/dexter/hyperdex/backend/data/p1/fills_raw_deep.jsonl")
OUT_CSV = Path("/home/dexter/hyperdex/backend/data/p1/consistent_set.csv")

NOW = datetime.now(timezone.utc)
WINDOW_START_MS = int((NOW - timedelta(days=90)).timestamp() * 1000)
HOLDOUT_CUTOFF_MS = int((NOW - timedelta(days=30)).timestamp() * 1000)
N_SUB = 3
MIN_FILLS = 50
HOLD_MIN_MIN = 5
HOLD_MAX_MIN = 48 * 60
MIN_HOLD_N = 20


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


def process_wallet(addr, fills) -> dict | None:
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
    # passes all
    m_h = statistics.mean(hold_pnls)
    s_h = statistics.stdev(hold_pnls) if len(hold_pnls) > 1 else 0
    t_stat = m_h / (s_h / math.sqrt(len(hold_pnls))) if s_h else 0
    return dict(
        addr=addr, n=len(fills), hold_med=hold_med,
        train_pnl=sum(train_pnls), hold_pnl=sum(hold_pnls),
        sharpe=sharpe([p for _, p in ts_pnl]),
        t_stat=t_stat,
        top_coin=top_coin(fills),
    )


print("=== Génération sub-window-consistent set ===")

# Build deep cache override (les 513 cap-truncés ont été re-fetched avec deeper data)
deep_override = {}
if DEEP_JSONL.exists():
    with open(DEEP_JSONL) as fh:
        for line in fh:
            try:
                obj = json.loads(line)
                deep_override[obj["wallet"]] = obj["fills"]
            except Exception:
                continue
    print(f"Deep override : {len(deep_override)} wallets ré-fetched avec >10k cap")

# Scan cache P1.5 principal
results = []
n_scanned = 0
with open(JSONL) as fh:
    for line in fh:
        try:
            obj = json.loads(line)
        except Exception:
            continue
        addr = obj.get("wallet")
        if not addr:
            continue
        n_scanned += 1
        if n_scanned % 1000 == 0:
            print(f"  ...{n_scanned}")
        # Use deep override if exists
        fills = deep_override.get(addr) or obj.get("fills", [])
        m = process_wallet(addr, fills)
        if m:
            results.append(m)

print(f"\nScanned {n_scanned} wallets")
print(f"Passed all criteria : {len(results)}")

# Sort by Sharpe desc
results.sort(key=lambda x: -x["sharpe"])

with open(OUT_CSV, "w") as fh:
    w = csv.DictWriter(fh, fieldnames=["addr", "n", "hold_med", "train_pnl",
                                        "hold_pnl", "sharpe", "t_stat", "top_coin"])
    w.writeheader()
    for r in results:
        w.writerow(r)
print(f"\nCSV : {OUT_CSV}")

# Stats
hold_buckets = [(0, 30, "<30m"), (30, 60, "30-60m"), (60, 240, "1-4h"),
                (240, 720, "4-12h"), (720, 2880, "12-48h")]
print(f"\nDistribution par hold-band :")
for lo, hi, lbl in hold_buckets:
    sub = [r for r in results if lo <= r["hold_med"] < hi]
    print(f"  {lbl:<10} : {len(sub)} wallets")
# Distribution top_coin
print(f"\nTop coins :")
cc = Counter(r["top_coin"] for r in results)
for c, n in cc.most_common(15):
    print(f"  {c:<14} : {n}")
