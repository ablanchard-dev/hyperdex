#!/usr/bin/env python3
"""Hunt LIVE funding carry sur HL — l'edge documenté (carry) jamais testé.

Fetch ~50 perps (candles 1h + funding 1h), aligne par heure, short high-funding /
long low-funding, juge par le CRITIC (beta-neutral + DSR déflaté). Carry = le plus
robuste de la littérature ; voyons s'il survit net de coûts sur HL.
"""
import statistics
import sys
import time

sys.path.insert(0, "/home/dexter/hyperdex/backend")
sys.path.insert(0, "/home/dexter/hyperdex/backend/edge_factory")
import funding as fd
from adapter import Bar, returns_from_bars
from verdict import evaluate_edge
from app.services.hl_api.info_client import InfoClient

N_CAP = 50
HOUR = 3600_000
MIN_PTS = 400   # HL cape funding_history a 500 pts (~21j) ; court mais 1er regard


def main():
    c = InfoClient(min_interval_s=1.0)
    meta, ctxs = c.meta_and_asset_ctxs()
    coins = [a["name"] for a in meta["universe"]][:N_CAP]
    end = int(time.time() * 1000)
    start = end - 90 * 24 * HOUR
    print(f"[{time.strftime('%H:%M:%S')}] fetch {len(coins)} perps (candles+funding 1h)...",
          flush=True)
    px, fund = {}, {}
    for s in coins:
        try:
            cd = c.candles(s, "1h", start, end)
            fh = c.funding_history(s, start, end)
            pmap = {int(k["T"]) // HOUR: float(k["c"]) for k in cd}
            fmap = {int(x["time"]) // HOUR: float(x["fundingRate"]) for x in fh}
            common = sorted(set(pmap) & set(fmap))
            if len(common) >= MIN_PTS:
                px[s] = (common, pmap, fmap)
        except Exception:
            pass
    # univers commun = heures présentes dans TOUS les coins retenus
    if len(px) < 10:
        print(f"   trop peu de coins ({len(px)}) — non concluant.", flush=True)
        return
    common_hours = sorted(set.intersection(*[set(v[0]) for v in px.values()]))
    print(f"   {len(px)} coins | {len(common_hours)} heures communes", flush=True)
    if len(common_hours) < MIN_PTS or len(px) < 10:
        print("   aligné trop petit.", flush=True)
        return
    price_bars = {s: [Bar(ts=h * HOUR, close=v[1][h]) for h in common_hours]
                  for s, v in px.items()}
    funding = {s: [v[2][h] for h in common_hours] for s, v in px.items()}
    btc = price_bars.get("BTC") or next(iter(price_bars.values()))

    variants = [0.15, 0.25, 0.40]   # top_frac (n_trials=3)
    n = len(common_hours)
    cut = int(n * 0.7)
    pb_tr = {s: b[:cut] for s, b in price_bars.items()}
    pb_te = {s: b[cut:] for s, b in price_bars.items()}
    fu_tr = {s: f[:cut] for s, f in funding.items()}
    fu_te = {s: f[cut:] for s, f in funding.items()}
    train_sh = [fd._sharpe(fd.funding_carry_backtest(pb_tr, fu_tr, tf, 4.5, 10.0))
                for tf in variants]
    sr_var = max(statistics.pvariance(train_sh) if len(train_sh) > 1 else 0.05, 1e-4)
    bench_te = returns_from_bars(btc[cut:])

    print(f"\n=== HUNT FUNDING CARRY HL ({len(px)} perps) — {len(variants)} variantes ===",
          flush=True)
    surv = 0
    for tf in variants:
        te = fd.funding_carry_backtest(pb_te, fu_te, tf, 4.5, 10.0)
        m = min(len(te), len(bench_te))
        v = evaluate_edge(te[:m], bench_te[:m], n_trials=len(variants),
                          sr_variance=sr_var)
        g = v["gates"]
        surv += int(v["pass"])
        print(f"  top_frac={tf} pass={v['pass']} dsr={g['dsr']:.3f} "
              f"sharpe={g['sharpe']:+.3f} beta={g['beta_neutral']['beta']:+.2f} "
              f"t_alpha={g['beta_neutral']['t_alpha']:+.2f} {v['reasons']}", flush=True)
    print(f"SURVIVANTS : {surv}", flush=True)


if __name__ == "__main__":
    main()
