#!/usr/bin/env python3
"""Hunt live : le générateur autonome chasse sur les actions small-cap US.

Fetch ~30 small-caps (Yahoo, pacé), backteste tout l'espace d'hypothèses OOS,
juge via le CRITIC (DSR déflaté par #hypothèses), logge en ResearchMemory.
Lancé détaché. Attendu : peu/pas de survivants (normal) ; succès = verdict honnête.
"""
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
import generator as g
from equities_adapter import EquitiesAdapter, EQ_TAKER_BPS
from research_memory import ResearchMemory

# échantillon small/mid-cap US (univers riche vs 2 new-listings HL)
TICKERS = ["PLUG", "FUBO", "SOFI", "RIOT", "MARA", "CLOV", "SPCE", "OPEN",
           "LMND", "RKLB", "IONQ", "DKNG", "AFRM", "UPST", "FSLY", "PATH",
           "GTLB", "BBAI", "SOUN", "ASTS", "ACHR", "JOBY", "CHPT", "RUN",
           "ENPH", "DNA", "WKHS", "CLSK", "HUT", "BTBT"]
MEM = str(Path(__file__).resolve().parent / "_equities_research.json")


def main():
    a = EquitiesAdapter(TICKERS)
    end = int(time.time() * 1000)
    start = end - 2 * 365 * 24 * 3600 * 1000  # 2 ans daily
    print(f"[{time.strftime('%H:%M:%S')}] fetch {len(TICKERS)} small-caps...",
          flush=True)
    sb = {}
    for t in TICKERS:
        try:
            b = a.history(t, start, end)
            if len(b) > 250:
                sb[t] = b
        except Exception as e:
            print(f"  skip {t}: {type(e).__name__}", flush=True)
        time.sleep(0.4)  # poli vs Yahoo
    bench = a.benchmark(start, end)
    print(f"symbols utilisables (>250 barres): {len(sb)} | bench IWM: {len(bench)}",
          flush=True)
    if len(sb) < 5:
        print("pas assez de data — abandon.", flush=True)
        return

    space = g.generate_space(["momentum", "mean_reversion", "breakout"],
                             [5, 10, 20, 40])
    print(f"espace : {len(space)} hypothèses (n_trials déflation DSR)", flush=True)
    res = g.run_generator(sb, bench, space, taker_bps=EQ_TAKER_BPS)

    mem = ResearchMemory(MEM)
    for r in res:
        r["venue"] = "equities"
        mem.record(r)
    mem.save()

    surv = g.survivors(res)
    print(f"\n=== HUNT ACTIONS SMALL-CAP — {len(res)} hypothèses testées ===",
          flush=True)
    print(f"SURVIVANTS : {len(surv)}", flush=True)
    res.sort(key=lambda r: r["gates"]["dsr"], reverse=True)
    for r in res[:8]:
        h = r["hypothesis"]
        print(f"  {h['family']:<14} lb={h['params']['lookback']:<3} "
              f"pass={r['pass']} dsr={r['gates']['dsr']:.3f} "
              f"sharpe={r['gates']['sharpe']:+.3f} reasons={r['reasons']}",
              flush=True)
    print(f"\nResearchMemory → {MEM}", flush=True)


if __name__ == "__main__":
    main()
