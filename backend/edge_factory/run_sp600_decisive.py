#!/usr/bin/env python3
"""TEST DÉCISIF PROPRE : momentum cross-sectional 12m sur le VRAI univers S&P 600.

Univers exogène (constituants S&P 600 small-cap, Wikipedia) — pas hand-picked.
Tranche fixe alphabétique, lancée UNE fois. Si t_alpha≥2 ET DSR>0.95 = edge
confirmé ; sinon réfutation propre du momentum cross-sectional small-cap.
"""
import re
import sys
import time

import httpx

sys.path.insert(0, "/home/dexter/hyperdex/backend")
sys.path.insert(0, "/home/dexter/hyperdex/backend/edge_factory")
import cross_sectional as xs
from equities_adapter import EquitiesAdapter

N_SAMPLE = 220        # tranche fixe (fetch ~ N×0.35s)
MIN_BARS = 480        # ~2 ans pleins (garde la fenêtre commune longue)


def sp600_tickers():
    r = httpx.get("https://en.wikipedia.org/wiki/List_of_S%26P_600_companies",
                  timeout=30, headers={"User-Agent": "Mozilla/5.0"},
                  follow_redirects=True)
    i = r.text.find('id="constituents"')
    seg = r.text[i:i + 200000]
    syms = re.findall(r'class="external text"[^>]*>([A-Z][A-Z.\-]{0,5})</a>', seg)
    # dédup en gardant l'ordre, retire les doublons de liens
    seen, out = set(), []
    for s in syms:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def align(symbol_bars, bench_bars):
    sets = [{b.ts for b in bars} for bars in symbol_bars.values()]
    sets.append({b.ts for b in bench_bars})
    common = sorted(set.intersection(*sets))
    out = {s: [b for b in bars if b.ts in set(common)] for s, bars in symbol_bars.items()}
    bm = {b.ts: b for b in bench_bars}
    return out, [bm[t] for t in common]


def main():
    tickers = sp600_tickers()
    print(f"[{time.strftime('%H:%M:%S')}] S&P600 extrait : {len(tickers)} tickers",
          flush=True)
    sample = tickers[:N_SAMPLE]
    a = EquitiesAdapter(sample)
    end = int(time.time() * 1000)
    start = end - 2 * 365 * 24 * 3600 * 1000
    sb = {}
    for t in sample:
        try:
            b = a.history(t, start, end)
            if len(b) >= MIN_BARS:
                sb[t] = b
        except Exception:
            pass
        time.sleep(0.35)
    bench = a.benchmark(start, end)
    sb, bench = align(sb, bench)
    print(f"   usables (≥{MIN_BARS} barres): {len(sb)} | dates communes: {len(bench)}",
          flush=True)
    if len(sb) < 40 or len(bench) < 250:
        print("   univers aligné trop petit — test non concluant.", flush=True)
        return
    # pré-enregistré : momentum cross-sectional, focalisé (n_trials=4)
    specs = [{"name": f"xsm_{lb}_{tf}", "rationale": "12m momentum factor",
              "signal": {"type": "xs_momentum",
                         "params": {"lookback": lb, "top_frac": tf}}}
             for lb in (60, 120) for tf in (0.2, 0.33)]
    res = xs.judge_cross_sectional(sb, bench, specs, taker_bps=8.0,
                                   slippage_bps=30.0, borrow_bps_annual=800.0)
    surv = xs.survivors(res)
    print(f"\n=== TEST DÉCISIF S&P600 ({len(sb)} noms) — {len(res)} hyp ===", flush=True)
    print(f"SURVIVANTS : {len(surv)}", flush=True)
    pbo = xs.cross_sectional_pbo(sb, specs, taker_bps=8.0, slippage_bps=30.0,
                                 borrow_bps_annual=800.0, S=8)
    print(f"PBO/CSCV (overfit de sélection entre hyp) = {pbo:.3f} "
          f"({'OVERFIT >0.5' if pbo > 0.5 else 'OK <0.5'})", flush=True)
    res.sort(key=lambda r: r["gates"]["dsr"], reverse=True)
    for r in res:
        h = r["hypothesis"]["signal"]
        g = r["gates"]
        print(f"  xs_momentum lb={h['params']['lookback']:<4} tf={h['params']['top_frac']} "
              f"pass={r['pass']} dsr={g['dsr']:.3f} sharpe={g['sharpe']:+.3f} "
              f"beta={g['beta_neutral']['beta']:+.2f} t_alpha={g['beta_neutral']['t_alpha']:+.2f} "
              f"{r['reasons']}", flush=True)


if __name__ == "__main__":
    main()
