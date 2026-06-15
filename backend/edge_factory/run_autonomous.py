#!/usr/bin/env python3
"""Boucle autonome LIVE complète : LLM propose -> CRITIC juge -> mémoire.

Le LLM (claude CLI) génère des hypothèses DSL ; le système les backteste OOS sur
les actions small-cap et les juge (DSR déflaté + beta-neutral). Personne n'écrit
de règle. Attendu : 0 survivant (DSL = TA prix = beta) mais boucle 100% autonome.
"""
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
import autonomous as au
import llm_hypothesis as lh
from equities_adapter import EquitiesAdapter, EQ_TAKER_BPS
from research_memory import ResearchMemory

TICKERS = ["PLUG", "FUBO", "SOFI", "RIOT", "MARA", "CLOV", "SPCE", "OPEN",
           "LMND", "RKLB", "IONQ", "DKNG", "AFRM", "UPST", "PATH", "SOUN",
           "ASTS", "ACHR", "JOBY", "CHPT", "RUN", "CLSK", "HUT", "BTBT"]
MEM = str(Path(__file__).resolve().parent / "_autonomous_research.json")


def main():
    print(f"[{time.strftime('%H:%M:%S')}] 1) LLM propose des hypothèses...",
          flush=True)
    specs = lh.generate_hypotheses(lh.call_llm_claude, n=10)
    print(f"   LLM a proposé {len(specs)} hypothèses valides :", flush=True)
    for s in specs:
        print(f"     {s['signal']['type']} {s['signal']['params']}", flush=True)
    if not specs:
        print("   aucune hypothèse valide — abandon.", flush=True)
        return

    print("2) fetch actions small-cap...", flush=True)
    a = EquitiesAdapter(TICKERS)
    end = int(time.time() * 1000)
    start = end - 2 * 365 * 24 * 3600 * 1000
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
    print(f"   symbols utilisables: {len(sb)} | bench IWM: {len(bench)}",
          flush=True)
    if len(sb) < 5:
        print("   pas assez de data — abandon.", flush=True)
        return

    print("3) CRITIC juge chaque hypothèse (OOS, DSR déflaté)...", flush=True)
    res = au.run_dsl_hypotheses(sb, bench, specs, taker_bps=EQ_TAKER_BPS)
    mem = ResearchMemory(MEM)
    for r in res:
        r["venue"] = "equities_llm"
        mem.record(r)
    mem.save()
    surv = au.survivors(res)
    print(f"\n=== BOUCLE AUTONOME — {len(res)} hypothèses LLM jugées ===",
          flush=True)
    print(f"SURVIVANTS : {len(surv)}", flush=True)
    res.sort(key=lambda r: r["gates"]["dsr"], reverse=True)
    for r in res[:6]:
        h = r["hypothesis"]["signal"]
        print(f"  {h['type']:<16} {h['params']} pass={r['pass']} "
              f"dsr={r['gates']['dsr']:.3f} sharpe={r['gates']['sharpe']:+.3f} "
              f"{r['reasons']}", flush=True)
    print(f"\nResearchMemory → {MEM}", flush=True)


if __name__ == "__main__":
    main()
