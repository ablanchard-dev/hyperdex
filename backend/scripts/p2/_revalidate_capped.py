"""Re-fetch deep (>10k cap) sur wallets cap-truncés du cache + re-apply
P1.5 Bonferroni z>4.34. Trouve les nouveaux Bonferroni-validés.

Pas de baisse de qualité — mêmes critères que P1.5 v4. La cohorte initiale
était limitée par le cap 10k fills du `user_fills_by_time`. Pour les wallets
qui ont >10k fills sur 90j, on n'avait que les 10k DERNIERS. Cette re-fetch
paginate plus profond pour capturer les 90j complets.
"""
from __future__ import annotations

import csv
import json
import math
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services.hl_api.info_client import InfoClient

CACHE_JSONL = Path(__file__).resolve().parents[2] / "data" / "p1" / "fills_raw_p1_5.jsonl"
CSV_96 = Path(__file__).resolve().parents[2] / "data" / "p1" / "detailed_97.csv"
OUT_DIR = Path(__file__).resolve().parents[2] / "data" / "p1"
DEEP_CACHE = OUT_DIR / "fills_raw_deep.jsonl"
RESULT_CSV = OUT_DIR / "new_bonferroni.csv"

CAP_THRESHOLD = 9990  # ≥9990 = considéré cap-truncated dans cache P1.5
MAX_PAGES_DEEP = 50   # 50 × 500 = 25 000 fills max par wallet

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


def log(*a):
    print(*a, flush=True)


def compute_hold_times_minutes(fills):
    sf = sorted(fills, key=lambda f: int(f.get("time", 0)))
    opens = {}
    holds = []
    for f in sf:
        coin = f.get("coin", "")
        d = (f.get("dir") or "").lower()
        ts = int(f.get("time", 0))
        if "open" in d:
            side = "long" if "long" in d else "short"
            opens.setdefault((coin, side), ts)
        elif "close" in d:
            side = "long" if "long" in d else "short"
            start = opens.pop((coin, side), None)
            if start is not None and ts > start:
                holds.append((ts - start) / 60000.0)
    return holds


def sharpe(pnls):
    if len(pnls) < 5:
        return 0
    m = statistics.mean(pnls)
    s = statistics.stdev(pnls)
    return m / s * math.sqrt(len(pnls)) if s else 0


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


def validate_wallet(addr: str, fills: list) -> dict | None:
    """Apply P1.5 Bonferroni criteria. Return metrics dict or None if doesn't pass."""
    if len(fills) < MIN_FILLS:
        return None
    holds = compute_hold_times_minutes(fills)
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
    if sum(hold_pnls) <= 0:
        return None
    m_h = statistics.mean(hold_pnls)
    s_h = statistics.stdev(hold_pnls) if len(hold_pnls) > 1 else 0
    t_stat = m_h / (s_h / math.sqrt(len(hold_pnls))) if s_h else 0
    sh = sharpe([p for _, p in ts_pnl])
    return dict(
        addr=addr, n=len(fills), hold_med=hold_med,
        train_pnl=sum(train_pnls), hold_pnl=sum(hold_pnls),
        sharpe=sh, t_stat=t_stat,
    )


def main():
    log("=" * 70)
    log("Re-validation deep — cap-truncés du cache P1.5")
    log("=" * 70)

    # Charge les 96 (existants)
    existing = set()
    with open(CSV_96) as fh:
        for r in csv.DictReader(fh):
            existing.add(r["addr"].lower())
    log(f"Cohorte existante : {len(existing)} adresses")

    # Identifier les cap-truncés
    log(f"\n[1] Scan cache P1.5 → identifier cap-truncés (n>={CAP_THRESHOLD})...")
    capped = []
    n_scanned = 0
    with open(CACHE_JSONL) as fh:
        for line in fh:
            try:
                obj = json.loads(line)
            except Exception:
                continue
            addr = obj.get("wallet")
            n_fills = len(obj.get("fills", []))
            n_scanned += 1
            if n_scanned % 1000 == 0:
                log(f"  ...{n_scanned}")
            if not addr:
                continue
            if n_fills >= CAP_THRESHOLD:
                capped.append(addr)
    log(f"Cap-truncés trouvés : {len(capped)} / {n_scanned} scannés")
    log(f"  Dont déjà dans 96 : {sum(1 for a in capped if a in existing)}")
    log(f"  Hors 96 : {sum(1 for a in capped if a not in existing)}")

    # Fetch deep
    log(f"\n[2] Fetch deep ({MAX_PAGES_DEEP}p × 500 = max {MAX_PAGES_DEEP*500} fills)...")
    ic = InfoClient(mainnet=True, min_interval_s=1.1)
    deep_cache = {}
    if DEEP_CACHE.exists():
        # reprise si run interrompu
        with open(DEEP_CACHE) as fh:
            for line in fh:
                try:
                    obj = json.loads(line)
                    deep_cache[obj["wallet"]] = obj["fills"]
                except Exception:
                    continue
        log(f"  Reprise : {len(deep_cache)} déjà fetched")

    to_fetch = [a for a in capped if a not in deep_cache]
    log(f"  À fetcher : {len(to_fetch)}")

    t0 = time.time()
    for i, addr in enumerate(to_fetch):
        try:
            fills = ic.user_fills_by_time(addr, WINDOW_START_MS,
                                          max_fills=MAX_PAGES_DEEP * 500)
        except Exception:
            fills = []
        deep_cache[addr] = fills
        # append JSONL incrémental (pas de re-dump)
        with open(DEEP_CACHE, "a") as fh:
            fh.write(json.dumps({"wallet": addr, "fills": fills}) + "\n")
        if (i + 1) % 25 == 0:
            el = time.time() - t0
            eta = el / (i + 1) * (len(to_fetch) - i - 1)
            log(f"  ...{i+1}/{len(to_fetch)} elapsed={el:.0f}s ETA={eta:.0f}s")

    log(f"\n[3] Re-validation avec données deep...")
    new_bonf = []
    for addr in capped:
        fills = deep_cache.get(addr, [])
        m = validate_wallet(addr, fills)
        if m:
            new_bonf.append(m)
    log(f"Passé filtres préliminaires : {len(new_bonf)}")

    # Bonferroni z_crit recalculé sur N = nombre testé
    # On garde le z_crit DU SCAN P1.5 ORIGINAL (z=4.34, N=3479) pour cohérence
    z_crit = 4.34
    log(f"Bonferroni z_crit = {z_crit}")
    valid = [m for m in new_bonf if m["t_stat"] > z_crit]
    new_in_valid = [m for m in valid if m["addr"] not in existing]
    log(f"Bonferroni-validés (re-vérifiés) : {len(valid)}")
    log(f"  Déjà dans cohorte : {len(valid) - len(new_in_valid)}")
    log(f"  ★ NOUVEAUX : {len(new_in_valid)}")

    new_in_valid.sort(key=lambda x: -x["sharpe"])

    log(f"\n=== Top 30 NOUVEAUX Bonferroni-validés (par Sharpe) ===")
    log(f"{'wallet':<16}{'n':>7}{'hold':>8}{'train':>10}{'hold':>10}{'Sharpe':>8}{'t_stat':>8}")
    for m in new_in_valid[:30]:
        log(f"{m['addr'][:14]:<16}{m['n']:>7}{m['hold_med']:>8.0f}"
            f"{m['train_pnl']:>+10.0f}{m['hold_pnl']:>+10.0f}"
            f"{m['sharpe']:>8.2f}{m['t_stat']:>8.2f}")

    # Save CSV
    with open(RESULT_CSV, "w") as fh:
        w = csv.DictWriter(fh, fieldnames=["addr", "n", "hold_med",
                                            "train_pnl", "hold_pnl",
                                            "sharpe", "t_stat"])
        w.writeheader()
        for m in new_in_valid:
            w.writerow(m)
    log(f"\nCSV : {RESULT_CSV}")
    log(f"Cohorte totale après ajout : {len(existing) + len(new_in_valid)}")


if __name__ == "__main__":
    main()
