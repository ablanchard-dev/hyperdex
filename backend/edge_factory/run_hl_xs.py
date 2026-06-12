#!/usr/bin/env python3
"""Hunt cross-sectional market-neutral sur Hyperliquid (perps, daily).

Multi-venue : la crypto est moins efficiente que les actions, et sur les PERPS le
short est natif (PAS de borrow stock — funding séparé non modélisé ici). Donc
borrow_bps=0, taker 4.5, slippage ~10 (alts HL). Si le cross-sectional momentum
crypto survit au CRITIC (beta-neutral + DSR déflaté), c'est un lead réel.
"""
import sys
import time

sys.path.insert(0, "/home/dexter/hyperdex/backend")
sys.path.insert(0, "/home/dexter/hyperdex/backend/edge_factory")
import cross_sectional as xs
from hl_adapter import HLSmallCapAdapter
from run_cross_sectional import align
from app.services.hl_api.info_client import InfoClient

MIN_BARS = 300        # ~ historique daily suffisant
N_CAP = 110           # cap fetch (rate-limit HL)


def main():
    a = HLSmallCapAdapter(InfoClient(min_interval_s=1.0), vol_max_usd=1e15,
                          interval="1d", benchmark="BTC")
    uni = a.universe()
    print(f"[{time.strftime('%H:%M:%S')}] perps HL: {len(uni)} ; fetch daily (cap {N_CAP})...",
          flush=True)
    end = int(time.time() * 1000)
    start = end - 2 * 365 * 24 * 3600 * 1000
    sb = {}
    for s in uni[:N_CAP]:
        try:
            b = a.history(s, start, end)
            if len(b) >= MIN_BARS:
                sb[s] = b
        except Exception:
            pass
    bench = a.benchmark(start, end)
    sb, bench = align(sb, bench)
    print(f"   usables (≥{MIN_BARS} barres): {len(sb)} | dates communes: {len(bench)}",
          flush=True)
    if len(sb) < 20 or len(bench) < 200:
        print("   univers HL aligné trop petit — non concluant.", flush=True)
        return
    specs = [{"name": f"{f}_{lb}_{tf}", "rationale": "xs crypto",
              "signal": {"type": f, "params": {"lookback": lb, "top_frac": tf}}}
             for f in ("xs_momentum", "xs_reversion")
             for lb in (14, 30, 90) for tf in (0.2, 0.33)]
    # PERPS : borrow=0 (short natif), taker 4.5, slippage 10 (alts), lag 1
    res = xs.judge_cross_sectional(sb, bench, specs, taker_bps=4.5,
                                   slippage_bps=10.0, borrow_bps_annual=0.0,
                                   exec_lag=1)
    surv = xs.survivors(res)
    print(f"\n=== HUNT CROSS-SECTIONAL HL ({len(sb)} perps) — {len(res)} hyp ===",
          flush=True)
    print(f"SURVIVANTS : {len(surv)}", flush=True)
    res.sort(key=lambda r: r["gates"]["dsr"], reverse=True)
    for r in res[:8]:
        h = r["hypothesis"]["signal"]
        g = r["gates"]
        print(f"  {h['type']:<13} lb={h['params']['lookback']:<3} tf={h['params']['top_frac']} "
              f"pass={r['pass']} dsr={g['dsr']:.3f} sharpe={g['sharpe']:+.3f} "
              f"beta={g['beta_neutral']['beta']:+.2f} t_alpha={g['beta_neutral']['t_alpha']:+.2f} "
              f"{r['reasons']}", flush=True)


if __name__ == "__main__":
    main()
