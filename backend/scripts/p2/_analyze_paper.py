"""Analyse en temps réel du positions.jsonl par hold-band.

Groupe les closes par trader + par bucket hold-time, calcule PnL net moyen,
WR, fees totales. Répond empiriquement à "quel hold range a de l'edge ?"
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

JSONL = Path(__file__).resolve().parents[2] / "data" / "paper" / "positions.jsonl"

if not JSONL.exists():
    print("Aucun fichier positions.jsonl")
    raise SystemExit(0)

# Parse closes
closes = []
opens = {}  # key → open ts (for hold computation if event missing it)
with open(JSONL) as fh:
    for line in fh:
        try:
            ev = json.loads(line)
        except Exception:
            continue
        evt = ev.get("event")
        if evt == "open":
            key = (ev["trader"], ev["coin"], ev["is_long"], ev["open_ts_ms"])
            opens[(ev["trader"], ev["coin"], ev["is_long"])] = ev["open_ts_ms"]
        elif evt == "close":
            closes.append(ev)

print(f"Closes loggés : {len(closes)}")
if not closes:
    print("Aucun close — laisse tourner un peu plus.")
    raise SystemExit(0)

# Groupe par trader
by_trader = defaultdict(list)
for c in closes:
    by_trader[c["trader"]].append(c)

print(f"Traders avec au moins 1 close : {len(by_trader)}\n")

# Stats par trader
rows = []
for trader, evs in by_trader.items():
    n = len(evs)
    pnls = [float(e.get("net_pnl", 0)) for e in evs]
    holds_min = [float(e.get("hold_ms", 0)) / 60000.0 for e in evs]
    fees = [float(e.get("fees_total", 0)) for e in evs]
    sum_pnl = sum(pnls)
    mean_pnl = sum_pnl / n if n else 0
    mean_hold = statistics.median(holds_min) if holds_min else 0
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    wr = 100.0 * wins / (wins + losses) if (wins + losses) else 0
    rows.append(dict(
        trader=trader, n=n, sum_pnl=sum_pnl, mean_pnl=mean_pnl,
        median_hold_min=mean_hold, wins=wins, losses=losses, wr=wr,
        sum_fees=sum(fees),
    ))

rows.sort(key=lambda r: r["mean_pnl"])

# Bucket par hold_med actuel
buckets = [
    ("<10 min", 0, 10),
    ("10-30 min", 10, 30),
    ("30-60 min", 30, 60),
    ("1-2h", 60, 120),
    ("2-4h", 120, 240),
    ("4-12h", 240, 720),
    ("12-48h", 720, 2880),
]

print("\n=== PAR BUCKET HOLD-TIME (en live paper) ===")
print(f"{'Bucket':<14}{'#trd':>5}{'#cls':>6}{'Σ PnL':>10}{'avg PnL/cls':>13}{'avg fees':>10}{'WR%':>6}")
print("-" * 64)
for label, lo, hi in buckets:
    in_bucket = [r for r in rows if lo <= r["median_hold_min"] < hi]
    if not in_bucket:
        print(f"{label:<14}    0     0         —            —         —     —")
        continue
    n_traders = len(in_bucket)
    n_closes = sum(r["n"] for r in in_bucket)
    sum_pnl = sum(r["sum_pnl"] for r in in_bucket)
    avg_pnl = sum_pnl / n_closes if n_closes else 0
    avg_fees = sum(r["sum_fees"] for r in in_bucket) / n_closes if n_closes else 0
    sum_wins = sum(r["wins"] for r in in_bucket)
    sum_losses = sum(r["losses"] for r in in_bucket)
    wr = 100.0 * sum_wins / (sum_wins + sum_losses) if (sum_wins+sum_losses) else 0
    print(f"{label:<14}{n_traders:>5}{n_closes:>6}{sum_pnl:>+10.3f}"
          f"{avg_pnl:>+13.4f}{avg_fees:>10.4f}{wr:>5.0f}")

# Top et bot 5 traders par sum_pnl
print("\n=== TOP 8 TRADERS (par Σ PnL) ===")
print(f"{'Trader':<16}{'n':>4}{'hold_med':>10}{'Σ PnL':>10}{'avg/cls':>10}{'WR%':>6}")
print("-" * 56)
for r in sorted(rows, key=lambda x: -x["sum_pnl"])[:8]:
    print(f"{r['trader'][:14]:<16}{r['n']:>4}{r['median_hold_min']:>10.0f}"
          f"{r['sum_pnl']:>+10.3f}{r['mean_pnl']:>+10.4f}{r['wr']:>5.0f}")

print("\n=== BOT 8 TRADERS (saignements) ===")
for r in sorted(rows, key=lambda x: x["sum_pnl"])[:8]:
    print(f"{r['trader'][:14]:<16}{r['n']:>4}{r['median_hold_min']:>10.0f}"
          f"{r['sum_pnl']:>+10.3f}{r['mean_pnl']:>+10.4f}{r['wr']:>5.0f}")

# Overall
total_pnl = sum(r["sum_pnl"] for r in rows)
total_n = sum(r["n"] for r in rows)
total_fees = sum(r["sum_fees"] for r in rows)
print(f"\nTOTAL : {total_n} closes, Σ PnL = ${total_pnl:+.3f}, Σ fees = ${total_fees:.3f}")
print(f"Avg PnL/close = ${total_pnl/total_n:+.4f}" if total_n else "")
