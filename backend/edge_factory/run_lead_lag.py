#!/usr/bin/env python3
"""Hunt LIVE : lead-lag BTC→alts cross-sectional sur HL perps (1h candles).

PRÉ-ENREGISTRÉ (lancé UNE fois) : univers top-50 HL par volume (figé, BTC exclu =
le lead), 60j × 1h, grille lookback {1,2,3,6}h × top_frac {0.2,0.3} = 8 specs,
beta_window=48h, taker 4.5 + slip 5bps, exec_lag=1. Jugé CRITIC (DSR déflaté ×8 +
beta-neutral + PBO). Prior BAS (le reversal-mort prédit que le rattrapage l'est aussi).
"""
import statistics
import sys
import time

sys.path.insert(0, "/opt/app/hyperdex/backend")
sys.path.insert(0, "/opt/app/hyperdex/backend/edge_factory")
import lead_lag as ll
from adapter import Bar, returns_from_bars
from verdict import _stats, evaluate_edge
from app.services.hl_api.info_client import InfoClient

HOUR = 3600_000
N_UNIV = 50
DAYS = 60
MIN_BARS = 1000
TAKER_BPS = 4.5
SLIPPAGE_BPS = 5.0
BETA_WINDOW = 48


def main():
    c = InfoClient(min_interval_s=1.0)
    meta, ctxs = c.meta_and_asset_ctxs()
    names = [a["name"] for a in meta["universe"]]
    vol = {names[i]: float(ctxs[i].get("dayNtlVlm", 0) or 0) for i in range(len(names))}
    coins = sorted(names, key=lambda s: -vol[s])[:N_UNIV]
    end = int(time.time() * 1000)
    start = end - DAYS * 24 * HOUR
    print(f"[{time.strftime('%H:%M:%S')}] fetch {len(coins)} perps × {DAYS}j candles 1h...",
          flush=True)
    raw = {}
    for s in coins:
        try:
            k = c.candles(s, "1h", start, end)
            if len(k) >= MIN_BARS:
                raw[s] = {int(x["t"]) // HOUR: float(x["c"]) for x in k}
        except Exception:
            pass
    common = sorted(set.intersection(*[set(m) for m in raw.values()]))
    bars = {s: [Bar(ts=h * HOUR, close=raw[s][h]) for h in common] for s in raw}
    btc = bars.pop("BTC", None) or bars.pop(next(iter(bars)))  # BTC = le lead, hors univers tradé
    print(f"   {len(bars)} alts + BTC | {len(common)} heures (~{len(common)/24:.0f}j)", flush=True)

    specs = [{"lookback": lb, "top_frac": tf} for lb in (1, 2, 3, 6) for tf in (0.2, 0.3)]
    n = min(len(b) for b in bars.values())
    cut = int(n * 0.7)

    def run(spec, lo, hi, slip):
        sb = {s: b[lo:hi] for s, b in bars.items()}
        return ll.lead_lag_backtest(sb, btc[lo:hi], spec["lookback"], spec["top_frac"],
                                    TAKER_BPS, slip, BETA_WINDOW, exec_lag=1)

    train_sh = [_stats._sharpe(run(sp, 0, cut, SLIPPAGE_BPS)) for sp in specs]
    sr_var = max(statistics.pvariance(train_sh) if len(train_sh) > 1 else 0.05, 1e-4)
    bench_te = returns_from_bars(btc[cut:n])

    cols = []
    print(f"\n=== LEAD-LAG BTC→alts HL — taker {TAKER_BPS}+slip {SLIPPAGE_BPS}bps "
          f"({len(specs)} specs) ===", flush=True)
    surv = 0
    for sp, tsh in zip(specs, train_sh):
        te = run(sp, cut, n, SLIPPAGE_BPS)
        cols.append(te)
        m = min(len(te), len(bench_te))
        v = evaluate_edge(te[:m], bench_te[:m], n_trials=len(specs), sr_variance=sr_var)
        surv += int(v["pass"])
        g = v["gates"]
        print(f"  lb={sp['lookback']}h tf={sp['top_frac']} pass={v['pass']} "
              f"train_sh={tsh:+.3f} dsr={g['dsr']:.3f} beta={g['beta_neutral']['beta']:+.2f} "
              f"t_alpha={g['beta_neutral']['t_alpha']:+.2f} {v['reasons']}", flush=True)
    mlen = min(len(x) for x in cols)
    matrix = [[cols[j][t] for j in range(len(cols))] for t in range(mlen)]
    pbo, _ = _stats.pbo_cscv(matrix, S=8)
    print(f"SURVIVANTS : {surv}/{len(specs)} | PBO={pbo:.3f}", flush=True)

    print("\n=== sensibilité coûts (best train_sharpe) ===", flush=True)
    for slip in (0.0, 5.0, 10.0):
        best = max(_stats._sharpe(run(sp, 0, cut, slip)) for sp in specs)
        print(f"  slip={slip:>4}bps | best train_sh={best:+.3f}", flush=True)


if __name__ == "__main__":
    main()
