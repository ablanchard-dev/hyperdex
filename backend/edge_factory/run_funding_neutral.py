#!/usr/bin/env python3
"""Hunt LIVE carry DELTA-NEUTRAL sur HL — le vrai edge documenté (Sharpe 3-6).

Par coin : carry = funding − Δpremium (long spot/short perp, prix hedgé via le
premium de funding_history → pas de spot externe). Portefeuille equal-weight,
jugé par le CRITIC (beta≈0 attendu ; alpha = la récolte de funding nette).
"""
import statistics
import sys
import time

sys.path.insert(0, "/opt/app/hyperdex/backend")
sys.path.insert(0, "/opt/app/hyperdex/backend/edge_factory")
import funding as fd
from adapter import Bar, returns_from_bars
from verdict import evaluate_edge
from app.services.hl_api.info_client import InfoClient

N_CAP = 30
HOUR = 3600_000
MIN_PTS = 1000


def main():
    c = InfoClient(min_interval_s=1.0)
    meta, _ = c.meta_and_asset_ctxs()
    coins = [a["name"] for a in meta["universe"]][:N_CAP]
    end = int(time.time() * 1000)
    start = end - 90 * 24 * HOUR
    print(f"[{time.strftime('%H:%M:%S')}] fetch funding+premium {len(coins)} perps...",
          flush=True)
    data = {}
    for s in coins:
        try:
            fh = c.funding_history_paged(s, start, end)
            hrs = [int(x["time"]) // HOUR for x in fh]
            data[s] = (hrs,
                       {h: float(x["fundingRate"]) for h, x in zip(hrs, fh)},
                       {h: float(x["premium"]) for h, x in zip(hrs, fh)})
        except Exception:
            pass
    data = {s: v for s, v in data.items() if len(v[0]) >= MIN_PTS}
    if len(data) < 10:
        print(f"   trop peu de coins ({len(data)}).", flush=True)
        return
    common = sorted(set.intersection(*[set(v[0]) for v in data.values()]))
    print(f"   {len(data)} coins | {len(common)} heures communes", flush=True)
    if len(common) < MIN_PTS:
        print("   aligné trop court.", flush=True)
        return
    funding = {s: [v[1][h] for h in common] for s, v in data.items()}
    premium = {s: [v[2][h] for h in common] for s, v in data.items()}
    # benchmark : BTC perp (le carry doit avoir beta≈0)
    btc_c = c.candles("BTC", "1h", start, end)
    bmap = {int(k["T"]) // HOUR: float(k["c"]) for k in btc_c}
    btc = [Bar(ts=h * HOUR, close=bmap[h]) for h in common if h in bmap]

    # variantes (n_trials) : univers (tous / high-|funding|) × exec_lag
    avgf = {s: statistics.mean(abs(x) for x in funding[s]) for s in funding}
    hi = sorted(funding, key=lambda s: -avgf[s])[:max(5, len(funding) // 2)]
    universes = {"all": list(funding), "high_funding": hi}
    variants = [(u, lag) for u in universes for lag in (0, 1)]

    def portfolio(coin_set, lag, lo, hi_):
        series = [fd.carry_neutral_backtest(funding[s][lo:hi_], premium[s][lo:hi_],
                                            fee_bps=1.5, exec_lag=lag)
                  for s in coin_set]
        m = min(len(x) for x in series)
        return [statistics.mean(series[j][t] for j in range(len(series)))
                for t in range(m)]

    n = len(common)
    cut = int(n * 0.7)
    train_sh = [fd._sharpe(portfolio(universes[u], lag, 0, cut)) for u, lag in variants]
    sr_var = max(statistics.pvariance(train_sh) if len(train_sh) > 1 else 0.05, 1e-4)
    bench_te = returns_from_bars(btc[cut:])

    print(f"\n=== HUNT CARRY DELTA-NEUTRAL HL ({len(funding)} perps) — {len(variants)} variantes ===",
          flush=True)
    surv = 0
    for u, lag in variants:
        te = portfolio(universes[u], lag, cut, n)
        m = min(len(te), len(bench_te))
        v = evaluate_edge(te[:m], bench_te[:m], n_trials=len(variants),
                          sr_variance=sr_var)
        g = v["gates"]
        surv += int(v["pass"])
        print(f"  {u:<13} lag={lag} pass={v['pass']} dsr={g['dsr']:.3f} "
              f"sharpe={g['sharpe']:+.3f} beta={g['beta_neutral']['beta']:+.2f} "
              f"t_alpha={g['beta_neutral']['t_alpha']:+.2f} {v['reasons']}", flush=True)
    print(f"SURVIVANTS : {surv}", flush=True)


if __name__ == "__main__":
    main()
