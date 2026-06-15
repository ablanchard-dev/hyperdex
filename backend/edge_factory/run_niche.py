#!/usr/bin/env python3
"""Runner live : test d'edge sur la VRAIE niche = new-listings HL.

Scanne l'univers, garde les perps listés depuis <max_age jours, teste le momentum
réflexif OOS via le CRITIC. Lancé détaché (scan ~172 perps pacé 1.5s = qq min).
"""
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import recency
import backtest
from hl_adapter import HLSmallCapAdapter
from app.services.hl_api.info_client import InfoClient

DAY = 86_400_000


def main():
    # pré-filtre low-volume (1 appel meta) = la vraie niche → scan récence léger
    a = HLSmallCapAdapter(InfoClient(min_interval_s=2.0), vol_max_usd=10_000_000)
    now = int(time.time() * 1000)
    print(f"[{time.strftime('%H:%M:%S')}] univers low-vol (<$10M/j) : "
          f"{len(a.universe())} perps → scan récence...", flush=True)
    nl = recency.new_listings(a, now, max_age_days=120, lookback_days=400)
    nl.sort(key=lambda x: x[1])
    print(f"new-listings (<120j) : {len(nl)}", flush=True)
    for s, age in nl:
        print(f"   {s}: {age:.0f}j", flush=True)
    if not nl:
        print("AUCUNE new-listing détectée — niche vide sur cette fenêtre.",
              flush=True)
        return
    start = now - 120 * DAY
    sb = {}
    for s, age in nl:
        b = a.history(s, start, now)
        if len(b) > 150:
            sb[s] = b
    bench = a.benchmark(start, now)
    print(f"\nsymbols avec >150 barres : {len(sb)} | bench BTC barres : {len(bench)}",
          flush=True)
    if len(sb) < 2:
        print("pas assez de new-listings exploitables (<2) pour un portefeuille.",
              flush=True)
        return
    v = backtest.oos_edge_test(sb, bench, lookbacks=[6, 12, 24, 48],
                               taker_bps=9.32)
    print("\n=== VERDICT new-listing momentum (OOS, CRITIC) ===", flush=True)
    print("best_lookback:", v["best_lookback"], "| train_sharpes:",
          v["train_sharpes"], flush=True)
    print("VERDICT:", "PASS" if v["pass"] else "FAIL", "| reasons:",
          v["reasons"], flush=True)
    print("gates:", v["gates"], flush=True)


if __name__ == "__main__":
    main()
