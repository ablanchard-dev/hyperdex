#!/usr/bin/env python3
"""STRESS-TEST du survivant carry delta-neutral — essayer de le CASSER.

Sharpe annualisé réel (vs doc 3-6), max drawdown, worst-hour, % hours positifs,
décomposition funding vs base, et STRESS de coûts (fees 1.5 → 5 → 10 bps).
Si le Sharpe est ~15 annualisé, la vol est probablement sous-estimée.
"""
import math
import statistics
import sys
import time

sys.path.insert(0, "/home/dexter/hyperdex/backend")
sys.path.insert(0, "/home/dexter/hyperdex/backend/edge_factory")
import funding as fd
from app.services.hl_api.info_client import InfoClient

HOUR = 3600_000
PER_YEAR = 24 * 365


def main():
    c = InfoClient(min_interval_s=1.0)
    meta, _ = c.meta_and_asset_ctxs()
    coins = [a["name"] for a in meta["universe"]][:30]
    end = int(time.time() * 1000)
    start = end - 90 * 24 * HOUR
    print(f"[{time.strftime('%H:%M:%S')}] fetch {len(coins)} perps (paginé)...", flush=True)
    data = {}
    for s in coins:
        try:
            fh = c.funding_history_paged(s, start, end)
            hrs = [int(x["time"]) // HOUR for x in fh]
            data[s] = (hrs, {h: float(x["fundingRate"]) for h, x in zip(hrs, fh)},
                       {h: float(x["premium"]) for h, x in zip(hrs, fh)})
        except Exception:
            pass
    data = {s: v for s, v in data.items() if len(v[0]) >= 1000}
    common = sorted(set.intersection(*[set(v[0]) for v in data.values()]))
    funding = {s: [v[1][h] for h in common] for s, v in data.items()}
    premium = {s: [v[2][h] for h in common] for s, v in data.items()}
    print(f"   {len(data)} coins | {len(common)} heures (~{len(common)/24:.0f}j)", flush=True)

    def portfolio(fee):
        series = [fd.carry_neutral_backtest(funding[s], premium[s], fee_bps=fee, exec_lag=1)
                  for s in funding]
        m = min(len(x) for x in series)
        return [statistics.mean(series[j][t] for j in range(len(series))) for t in range(m)]

    def ann_sharpe(r):
        sd = statistics.pstdev(r)
        return (statistics.mean(r) / sd * math.sqrt(PER_YEAR)) if sd > 0 else 0.0

    def max_dd(r):
        cum, peak, dd = 0.0, 0.0, 0.0
        for x in r:
            cum += x
            peak = max(peak, cum)
            dd = min(dd, cum - peak)
        return dd, cum

    print("\n=== STRESS DE COÛTS (fees maker → taker) ===", flush=True)
    for fee in (1.5, 5.0, 10.0):
        r = portfolio(fee)
        dd, total = max_dd(r)
        print(f"  fee={fee:>4}bps | Sharpe_ann={ann_sharpe(r):+6.2f} | "
              f"ret_total={total*100:+.2f}% sur {len(r)/24:.0f}j | maxDD={dd*100:.2f}% | "
              f"%h+={sum(x>0 for x in r)/len(r)*100:.0f}% | worst_h={min(r)*100:.3f}%", flush=True)

    # décompo : funding collecté pur vs composante base (Δpremium)
    r = portfolio(1.5)
    print(f"\n=== sanity : Sharpe_ann={ann_sharpe(r):.1f} vs carry documenté 3-6 ===", flush=True)
    print(f"  vol horaire={statistics.pstdev(r)*100:.4f}% | mean horaire={statistics.mean(r)*100:.5f}%",
          flush=True)
    print("  => si Sharpe>>6, vol probablement sous-estimée (premium plus lisse que vraie base perp/spot)",
          flush=True)


if __name__ == "__main__":
    main()
