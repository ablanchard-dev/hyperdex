#!/usr/bin/env python3
"""Hunt LIVE cross-sectional market-neutral sur actions small-cap US.

Long-short par classement → beta≈0 → si rendement OOS positif net = VRAI ALPHA
(pas du beta). C'est le seul angle non encore réfuté. Jugé par le CRITIC.
Alignement par dates communes (requis pour ranker l'univers à chaque date).
"""
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
import cross_sectional as xs
from equities_adapter import EquitiesAdapter
from research_memory import ResearchMemory

# univers FIXE large (~88 growth small/mid-cap US, défini UNE fois = pas de p-hacking)
TICKERS = ["PLUG", "FUBO", "SOFI", "RIOT", "MARA", "CLOV", "SPCE", "OPEN",
           "LMND", "RKLB", "IONQ", "DKNG", "AFRM", "UPST", "PATH", "SOUN",
           "ASTS", "ACHR", "JOBY", "CHPT", "RUN", "CLSK", "HUT", "BTBT",
           "FSLY", "GTLB", "DNA", "QS", "FCEL", "BLNK", "ENPH", "HOOD",
           "RBLX", "DOCN", "COIN", "ROKU", "PINS", "SNAP", "LYFT", "DASH",
           "RUM", "WULF", "CIFR", "IREN", "GRAB", "BBAI", "EVGO", "LAZR",
           "NKLA", "LCID", "CVNA", "W", "CHWY", "PTON", "BYND", "OKTA",
           "TWLO", "DOCU", "ZM", "PLTR", "U", "PD", "MDB", "S",
           "NET", "DDOG", "CRWD", "ZS", "SNOW", "ESTC", "FROG", "AMPL",
           "BRZE", "BIGC", "RNG", "FIVN", "MGNI", "PUBM", "CRTO", "DV",
           "SKLZ", "PENN", "RSI", "RXRX", "SAVA", "VKTX", "CRSP", "NTLA"]
MEM = str(Path(__file__).resolve().parent / "_xs_research.json")


def align(symbol_bars, bench_bars):
    """Restreint aux timestamps présents dans TOUS les symboles + le bench."""
    sets = [{b.ts for b in bars} for bars in symbol_bars.values()]
    sets.append({b.ts for b in bench_bars})
    common = sorted(set.intersection(*sets))
    out = {}
    for s, bars in symbol_bars.items():
        m = {b.ts: b for b in bars}
        out[s] = [m[t] for t in common]
    bm = {b.ts: b for b in bench_bars}
    return out, [bm[t] for t in common]


def main():
    a = EquitiesAdapter(TICKERS)
    end = int(time.time() * 1000)
    start = end - 2 * 365 * 24 * 3600 * 1000
    print(f"[{time.strftime('%H:%M:%S')}] fetch {len(TICKERS)} small-caps...",
          flush=True)
    sb = {}
    for t in TICKERS:
        try:
            b = a.history(t, start, end)
            if len(b) > 250:
                sb[t] = b
        except Exception:
            pass
        time.sleep(0.4)
    bench = a.benchmark(start, end)
    sb, bench = align(sb, bench)
    nbars = len(bench)
    print(f"   {len(sb)} symbols alignés sur {nbars} dates communes | bench IWM",
          flush=True)
    if len(sb) < 6 or nbars < 250:
        print("   pas assez de data alignée — abandon.", flush=True)
        return

    # espace FOCALISÉ : facteur académique pré-enregistré = momentum cross-sectional
    # 6m/12m (pas de data-mining ; n_trials petit → DSR moins déflaté).
    specs = []
    for lb in (60, 120):           # ~3m, ~6m de barres journalières
        for tf in (0.2, 0.33):
            specs.append({"name": f"xsm_{lb}_{tf}", "rationale": "12m momentum factor",
                          "signal": {"type": "xs_momentum",
                                     "params": {"lookback": lb, "top_frac": tf}}})
    print(f"espace : {len(specs)} hypothèses cross-sectional (DSR déflaté)",
          flush=True)
    res = xs.judge_cross_sectional(sb, bench, specs, taker_bps=8.0)
    surv = xs.survivors(res)
    # PRINT D'ABORD (le verdict ne doit jamais être perdu par un souci de record)
    print(f"\n=== HUNT CROSS-SECTIONAL (market-neutral) — {len(res)} hypothèses ===",
          flush=True)
    print(f"SURVIVANTS : {len(surv)}", flush=True)
    res.sort(key=lambda r: r["gates"]["dsr"], reverse=True)
    for r in res[:8]:
        h = r["hypothesis"]["signal"]
        g = r["gates"]
        print(f"  {h['type']:<13} lb={h['params']['lookback']:<4} "
              f"tf={h['params']['top_frac']} pass={r['pass']} "
              f"dsr={g['dsr']:.3f} sharpe={g['sharpe']:+.3f} "
              f"beta={g['beta_neutral']['beta']:+.2f} "
              f"t_alpha={g['beta_neutral']['t_alpha']:+.2f} {r['reasons']}",
              flush=True)
    try:
        mem = ResearchMemory(MEM)
        for r in res:
            r["venue"] = "equities_xs"
            mem.record(r)
        mem.save()
        print(f"ResearchMemory → {MEM}", flush=True)
    except Exception as e:
        print(f"(record non bloquant échoué: {type(e).__name__})", flush=True)


if __name__ == "__main__":
    main()
