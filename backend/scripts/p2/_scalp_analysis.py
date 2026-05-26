"""Investigation : le scalping (<10min) est-il définitivement mort ?

Méthode : pour chaque Bonferroni-validé, calculer 'edge_per_dollar_notional'
= ev_per_fill / avg_notional. Si > 0.0005 (= notre fee rate round-trip
0.05% notional), profitable pour nous. Sinon = bouffé par fees.

Grouper par hold-band, médiane edge_per_$, count profitables.
"""
import csv
import statistics
from collections import defaultdict
from pathlib import Path

CSV = Path("/opt/app/hyperdex/backend/data/p1/detailed_97.csv")

# Notre fee taker round-trip = 0.025% × 2 = 0.05%
FEE_RATE_RT = 0.0005

if not CSV.exists():
    raise SystemExit("detailed_97.csv absent")

rows = []
with open(CSV) as fh:
    for r in csv.DictReader(fh):
        try:
            r["hold_med"] = float(r["hold_med"])
            r["ev_per_fill"] = float(r["ev_per_fill"])
            r["avg_notional"] = float(r["avg_notional"])
            r["sharpe"] = float(r["sharpe"])
            r["wr_fill"] = float(r["wr_fill"])
            r["n"] = int(r["n"])
            r["total_pnl"] = float(r["total_pnl"])
            r["edge_per_dollar"] = (r["ev_per_fill"] / r["avg_notional"]
                                    if r["avg_notional"] else 0)
            rows.append(r)
        except Exception:
            continue

print(f"=== Edge per dollar notional (96 Bonferroni-validés) ===")
print(f"Notre fee taker round-trip = {FEE_RATE_RT*100:.3f}%")
print(f"Pour profit copy : edge_per_$_notional > {FEE_RATE_RT}\n")

buckets = [
    ("<10 min", 0, 10),
    ("10-30 min", 10, 30),
    ("30-60 min", 30, 60),
    ("1-2h", 60, 120),
    ("2-4h", 120, 240),
    ("4-12h", 240, 720),
    ("12-48h", 720, 2880),
]

print(f"{'bucket':<14}{'n_trd':>6}{'edge/$ med':>14}{'profit':>10}{'WR med':>10}{'Sharpe med':>14}")
print("-" * 72)
for label, lo, hi in buckets:
    sub = [r for r in rows if lo <= r["hold_med"] < hi]
    if not sub:
        print(f"{label:<14}{0:>6}{'—':>14}{'—':>10}{'—':>10}{'—':>14}")
        continue
    n = len(sub)
    edges = [r["edge_per_dollar"] for r in sub]
    median_edge = statistics.median(edges)
    n_profit = sum(1 for e in edges if e > FEE_RATE_RT)
    wr_med = statistics.median(r["wr_fill"] for r in sub)
    sh_med = statistics.median(r["sharpe"] for r in sub)
    print(f"{label:<14}{n:>6}{median_edge*100:>13.4f}%{n_profit:>4}/{n:<5}"
          f"{wr_med:>9.0f}%{sh_med:>14.2f}")

# Verdict scalping
scalp = [r for r in rows if r["hold_med"] < 30]
scalp_profit = [r for r in scalp if r["edge_per_dollar"] > FEE_RATE_RT]
print(f"\n=== VERDICT SCALPING (<30min) ===")
print(f"Total wallets <30min hold : {len(scalp)}")
print(f"Profitables pour NOUS (edge/$ > 0.05%) : {len(scalp_profit)}")
if scalp_profit:
    print("\nLes scalpers qui survivent à NOS fees :")
    for r in sorted(scalp_profit, key=lambda x: -x["edge_per_dollar"]):
        gap = (r["edge_per_dollar"] - FEE_RATE_RT) * 100
        print(f"  {r['addr'][:14]} hold={r['hold_med']:.0f}min "
              f"edge/$=+{r['edge_per_dollar']*100:.4f}% net=+{gap:.4f}% "
              f"top_coin={r['top_coin']}")
else:
    print("AUCUN. Scalping <30min = MORT pour nous au tier taker 0.025%.")

# Top edge_per_$ globaux (tout hold, qui sont les "winners cashflow")
print(f"\n=== Top 15 wallets par edge_per_$_notional (= les vraies vaches à lait) ===")
rows.sort(key=lambda r: -r["edge_per_dollar"])
print(f"{'wallet':<16}{'hold':>8}{'edge/$':>12}{'net_copy':>12}{'avg_not':>10}{'top_coin':>14}")
for r in rows[:15]:
    net = (r["edge_per_dollar"] - FEE_RATE_RT) * 100
    print(f"{r['addr'][:14]:<16}{r['hold_med']:>8.0f}{r['edge_per_dollar']*100:>11.4f}%"
          f"{net:>11.4f}%{r['avg_notional']:>10.0f}{r['top_coin']:>14}")
