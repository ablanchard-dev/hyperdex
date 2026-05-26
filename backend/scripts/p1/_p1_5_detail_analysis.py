"""Analyse détaillée des 97 Bonferroni-validés P1.5.

Stream JSONL → applique mêmes filtres que P1.5 → enrichit avec :
- avg notional per fill, trades/jour, WR per-fill
- top coin tradé, ratio long/short
- distribution hold-times détaillée
- EV per $1 staké, EV par trade

Sortie : detailed_97.md + CSV.
"""
from __future__ import annotations
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT = Path("/home/dexter/hyperdex/backend/data/p1")
JSONL = OUT / "fills_raw_p1_5.jsonl"
DETAIL_MD = OUT / "detailed_97.md"
DETAIL_CSV = OUT / "detailed_97.csv"

NOW = datetime.now(timezone.utc)
WINDOW_DAYS = 90
HOLDOUT_DAYS = 30
WINDOW_START_MS = int((NOW - timedelta(days=WINDOW_DAYS)).timestamp() * 1000)
HOLDOUT_CUTOFF_MS = int((NOW - timedelta(days=HOLDOUT_DAYS)).timestamp() * 1000)
N_SUB_PERIODS = 3
MIN_FILLS = 50
HOLD_MIN_MIN = 5
HOLD_MAX_MIN = 48 * 60
MIN_HOLD_N = 20


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
        return 0.0
    m = statistics.mean(pnls)
    s = statistics.stdev(pnls)
    if s == 0:
        return 0.0
    return m / s * math.sqrt(len(pnls))


def max_dd(pnls_chrono):
    cum = peak = m = 0.0
    for p in pnls_chrono:
        cum += p
        peak = max(peak, cum)
        m = max(m, peak - cum)
    return m


def sub_window_pos(ts_pnl, n_sub, t_lo, t_hi):
    if t_hi <= t_lo:
        return False
    step = (t_hi - t_lo) // n_sub
    for i in range(n_sub):
        lo, hi = t_lo + i*step, t_lo + (i+1)*step
        s = sum(p for ts, p in ts_pnl if lo <= ts < hi)
        if s <= 0:
            return False
    return True


def main():
    print("=== Analyse détaillée 97 Bonferroni P1.5 ===")
    print("Stream JSONL...")
    # Same filters as P1.5 v4
    all_metrics = []
    n_scanned = 0
    with open(JSONL) as fh:
        for line in fh:
            try:
                obj = json.loads(line)
            except Exception:
                continue
            addr = obj.get("wallet")
            fills = obj.get("fills", [])
            n_scanned += 1
            if n_scanned % 500 == 0:
                print(f"  ...scanned {n_scanned}")
            if not addr or len(fills) < MIN_FILLS:
                continue
            holds = compute_hold_times_minutes(fills)
            hold_med = statistics.median(holds) if holds else 0.0
            if not (HOLD_MIN_MIN <= hold_med <= HOLD_MAX_MIN):
                continue
            # PnL chrono
            fs = sorted(fills, key=lambda f: int(f.get("time", 0)))
            ts_pnl = [(int(f.get("time", 0)), float(f.get("closedPnl", 0))) for f in fs]
            train_tp = [x for x in ts_pnl if x[0] < HOLDOUT_CUTOFF_MS]
            hold_tp = [x for x in ts_pnl if x[0] >= HOLDOUT_CUTOFF_MS]
            train_pnls = [p for _, p in train_tp]
            hold_pnls = [p for _, p in hold_tp]
            sh = sharpe([p for _, p in ts_pnl])
            dd = max_dd([p for _, p in ts_pnl])
            sub_ok = sub_window_pos(train_tp, N_SUB_PERIODS,
                                     WINDOW_START_MS, HOLDOUT_CUTOFF_MS)
            hold_ok = sum(hold_pnls) > 0
            t_stat = None
            if len(hold_pnls) >= 5:
                m = statistics.mean(hold_pnls)
                s = statistics.stdev(hold_pnls) if len(hold_pnls) > 1 else 0
                if s > 0:
                    t_stat = m / (s / math.sqrt(len(hold_pnls)))
            # Enrichissements
            notionals = []
            coins = Counter()
            wins = 0
            losses = 0
            open_long = open_short = 0
            for f in fills:
                try:
                    notionals.append(float(f.get("sz", 0)) * float(f.get("px", 0)))
                except Exception:
                    pass
                coins[f.get("coin", "?")] += 1
                cp = float(f.get("closedPnl", 0))
                if cp > 0: wins += 1
                elif cp < 0: losses += 1
                d = (f.get("dir") or "").lower()
                if "open" in d:
                    if "long" in d: open_long += 1
                    else: open_short += 1
            avg_notional = statistics.mean(notionals) if notionals else 0
            wr_fill = 100.0 * wins / (wins + losses) if (wins+losses) else 0
            top_coin = coins.most_common(1)[0][0] if coins else "?"
            top_coin_pct = 100.0 * coins.most_common(1)[0][1] / len(fills) if fills else 0
            long_short_ratio = open_long / max(1, open_short)
            # span en jours réel
            if ts_pnl:
                span_days = (ts_pnl[-1][0] - ts_pnl[0][0]) / 86400000.0
                trades_per_day = len(fills) / max(1, span_days)
            else:
                span_days = 0
                trades_per_day = 0
            total_pnl = sum(p for _, p in ts_pnl)
            ev_per_fill = total_pnl / len(fills) if fills else 0
            all_metrics.append(dict(
                addr=addr, n=len(fills),
                hold_med=hold_med, hold_count=len(holds),
                train_n=len(train_pnls), train_pnl=sum(train_pnls),
                hold_n=len(hold_pnls), hold_pnl=sum(hold_pnls),
                total_pnl=total_pnl, ev_per_fill=ev_per_fill,
                sharpe=sh, max_dd=dd,
                wr_fill=wr_fill,
                avg_notional=avg_notional, ev_per_dollar=ev_per_fill/max(1,avg_notional),
                trades_per_day=trades_per_day, span_days=span_days,
                top_coin=top_coin, top_coin_pct=top_coin_pct,
                long_short_ratio=long_short_ratio,
                sub_ok=sub_ok, hold_ok=hold_ok, t_stat=t_stat,
            ))
    print(f"\nfiltered hold + n>=50 : {len(all_metrics)}")

    # Apply P1.5 filter chain
    copyables = [m for m in all_metrics
                 if m["train_pnl"] > 0 and m["hold_pnl"] > 0 and m["hold_n"] >= MIN_HOLD_N]
    consistent = [m for m in copyables if m["sub_ok"] and m["hold_ok"]]
    N = len(all_metrics)
    alpha = 0.05 / max(1, N)
    z_crit = statistics.NormalDist().inv_cdf(1 - alpha / 2)
    bonf = [m for m in consistent if m["t_stat"] is not None and m["t_stat"] > z_crit]
    print(f"copyables: {len(copyables)}  consistent: {len(consistent)}  Bonferroni: {len(bonf)}  z_crit={z_crit:.2f}")

    bonf.sort(key=lambda x: -x["sharpe"])

    # === Distributions aggrégées ===
    print("\n=== Distributions sur les Bonferroni-validés ===")
    print(f"Profile : intraday(<240min) = {sum(1 for m in bonf if m['hold_med']<240)} ; swing(240-2880min) = {sum(1 for m in bonf if 240<=m['hold_med']<=2880)}")
    print(f"Hold median (minutes) : min={min(m['hold_med'] for m in bonf):.0f}, p50={statistics.median(m['hold_med'] for m in bonf):.0f}, max={max(m['hold_med'] for m in bonf):.0f}")
    print(f"n fills : min={min(m['n'] for m in bonf)}, p50={statistics.median(m['n'] for m in bonf):.0f}, max={max(m['n'] for m in bonf)}")
    print(f"trades/jour : moy={statistics.mean(m['trades_per_day'] for m in bonf):.1f}, p50={statistics.median(m['trades_per_day'] for m in bonf):.1f}")
    print(f"avg notional (sz×px) : moy=${statistics.mean(m['avg_notional'] for m in bonf):,.0f}, p50=${statistics.median(m['avg_notional'] for m in bonf):,.0f}")
    print(f"WR fill : moy={statistics.mean(m['wr_fill'] for m in bonf):.1f}%, p50={statistics.median(m['wr_fill'] for m in bonf):.1f}%")
    print(f"Sharpe : min={min(m['sharpe'] for m in bonf):.2f}, p50={statistics.median(m['sharpe'] for m in bonf):.2f}, max={max(m['sharpe'] for m in bonf):.2f}")
    print(f"total cumulé PnL : ${sum(m['total_pnl'] for m in bonf):,.0f}")
    print(f"total holdout PnL : ${sum(m['hold_pnl'] for m in bonf):,.0f}")
    coin_dist = Counter(m["top_coin"] for m in bonf)
    print(f"top coins : {dict(coin_dist.most_common(8))}")
    ls_balanced = sum(1 for m in bonf if 0.5 <= m["long_short_ratio"] <= 2.0)
    print(f"balanced long/short (0.5-2.0 ratio) : {ls_balanced}/{len(bonf)}")

    # CSV
    cols = ["addr","n","hold_med","hold_count","train_n","train_pnl","hold_n","hold_pnl",
            "total_pnl","ev_per_fill","sharpe","max_dd","wr_fill","avg_notional",
            "ev_per_dollar","trades_per_day","span_days","top_coin","top_coin_pct",
            "long_short_ratio","t_stat"]
    with open(DETAIL_CSV, "w") as fh:
        fh.write(",".join(cols) + "\n")
        for m in bonf:
            fh.write(",".join(str(m.get(c, "")) for c in cols) + "\n")
    print(f"\nCSV : {DETAIL_CSV}")

    # MD report
    lines = [f"# Analyse détaillée — 97 Bonferroni-validés HyperDex P1.5\n",
             f"_Run : {NOW.isoformat()}_\n",
             "## Sommaire global"]
    lines.append(f"- N pop testée : {N} | copyables : {len(copyables)} | consistent : {len(consistent)} | **Bonferroni : {len(bonf)}**")
    lines.append(f"- Profile : intraday <4h = {sum(1 for m in bonf if m['hold_med']<240)} | swing 4-48h = {sum(1 for m in bonf if 240<=m['hold_med']<=2880)}")
    lines.append(f"- Hold-time médian (min) : min {min(m['hold_med'] for m in bonf):.0f} / p50 {statistics.median(m['hold_med'] for m in bonf):.0f} / max {max(m['hold_med'] for m in bonf):.0f}")
    lines.append(f"- Trades/jour : moy {statistics.mean(m['trades_per_day'] for m in bonf):.1f} / p50 {statistics.median(m['trades_per_day'] for m in bonf):.1f}")
    lines.append(f"- Avg notional/fill ($) : moy ${statistics.mean(m['avg_notional'] for m in bonf):,.0f} / p50 ${statistics.median(m['avg_notional'] for m in bonf):,.0f}")
    lines.append(f"- WR per-fill : moy {statistics.mean(m['wr_fill'] for m in bonf):.1f}%")
    lines.append(f"- Sharpe : p50 {statistics.median(m['sharpe'] for m in bonf):.2f} / max {max(m['sharpe'] for m in bonf):.2f}")
    lines.append(f"- **Total cumulé PnL des 97 (90j)** : **${sum(m['total_pnl'] for m in bonf):,.0f}**")
    lines.append(f"- **Total holdout (30j) cumulé** : **${sum(m['hold_pnl'] for m in bonf):,.0f}**")
    lines.append(f"- Top coins : {dict(coin_dist.most_common(8))}")
    lines.append(f"- Balanced long/short (ratio 0.5-2) : {ls_balanced}/{len(bonf)}")

    lines.append("\n## Les 97 traders — détail complet (par Sharpe DESC)")
    lines.append("| # | wallet | profile | hold(min) | n | trades/j | $notional | WR_fill | total$ | train$ | holdout$ | Sharpe | max_DD | t_stat | top_coin |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for i, m in enumerate(bonf):
        profile = "intraday" if m["hold_med"] < 240 else "swing"
        lines.append(f"| {i+1} | {m['addr'][:14]} | {profile} | {m['hold_med']:.0f} | {m['n']} | {m['trades_per_day']:.1f} | ${m['avg_notional']:,.0f} | {m['wr_fill']:.0f}% | ${m['total_pnl']:+,.0f} | ${m['train_pnl']:+,.0f} | ${m['hold_pnl']:+,.0f} | {m['sharpe']:.2f} | ${m['max_dd']:,.0f} | {m['t_stat']:.2f} | {m['top_coin']} |")

    DETAIL_MD.write_text("\n".join(lines))
    print(f"\nMD : {DETAIL_MD}")


if __name__ == "__main__":
    main()
