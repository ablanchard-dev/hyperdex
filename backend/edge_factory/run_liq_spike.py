#!/usr/bin/env python3
"""TEST D'EDGE SUR VRAIE DATA — liquidation spike contrarian (data horaire Coinalyze).

Version DISCRÈTE adaptée à la data bucketée (le Hawkes ponctuel était inadapté).
Pipeline : fetch liq + prix horaires BTC → liq nette signée par barre → z-score
rolling passé-only → sur spike |z|>seuil, entrer CONTRE → juger CRITIC (DSR+beta+perm).

PRÉ-ENREGISTRÉ : BTC perp Binance, 30j × 1h, z_window=48, z_threshold=2.0, coûts
taker 4.5 + slip 5 bps, exec_lag=1. Un seul run, verdict accepté tel quel (pas de tune).
"""
import os
import statistics
import sys
import time

sys.path.insert(0, "/opt/app/hyperdex/backend")
sys.path.insert(0, "/opt/app/hyperdex/backend/edge_factory")
import liq_spike as ls
import permutation as pm
from adapter import Bar, returns_from_bars
from verdict import evaluate_edge

import httpx

KEY = open(os.path.expanduser("~/.coinalyze_key")).read().strip()
SYMBOL = os.environ.get("LIQ_SYMBOL", "BTCUSDT_PERP.A")
INTERVAL = os.environ.get("LIQ_INTERVAL", "1hour")
DAYS = int(os.environ.get("LIQ_DAYS", "30"))
Z_WINDOW = int(os.environ.get("LIQ_ZWIN", "48"))
Z_THRESHOLD = float(os.environ.get("LIQ_ZTHR", "2.0"))
TAKER_BPS = 4.5
SLIP_BPS = 5.0


def _get(path, params):
    r = httpx.get(f"https://api.coinalyze.net/v1/{path}",
                  headers={"api_key": KEY}, params=dict(params), timeout=30)
    r.raise_for_status()
    return r.json()


def main():
    to = int(time.time())
    frm = to - DAYS * 24 * 3600
    print(f"[{time.strftime('%H:%M:%S')}] fetch liq + prix {SYMBOL} {DAYS}j {INTERVAL}...",
          flush=True)
    liq = _get("liquidation-history", {"symbols": SYMBOL, "interval": INTERVAL,
                                       "from": frm, "to": to, "convert_to_usd": "true"})
    px = _get("ohlcv-history", {"symbols": SYMBOL, "interval": INTERVAL,
                                "from": frm, "to": to})
    # aligner liq et prix par timestamp horaire
    lh = {int(b["t"]): (float(b["l"]), float(b["s"])) for b in liq[0]["history"]}
    ph = {int(c["t"]): float(c["c"]) for c in px[0]["history"]}
    common = sorted(set(lh) & set(ph))
    bars = [Bar(ts=t * 1000, close=ph[t]) for t in common]
    # net liq par barre : long-liq = sell-off (négatif), short-liq = positif
    net_liq = [lh[t][1] - lh[t][0] for t in common]  # s - l : short>long => positif
    print(f"   {len(bars)} barres alignées (~{len(bars)/24:.0f}j)", flush=True)
    if len(bars) < 200:
        print("   data insuffisante.", flush=True)
        return

    strat = ls.liq_spike_returns(bars, net_liq, z_window=Z_WINDOW,
                                 z_threshold=Z_THRESHOLD, taker_bps=TAKER_BPS,
                                 slippage_bps=SLIP_BPS, exec_lag=1)
    active = [r for r in strat if r != 0.0]
    print(f"   trades actifs (|z|>{Z_THRESHOLD}) : {len(active)}", flush=True)
    if len(active) < 10:
        print("   trop peu de trades — non concluant (data 30j courte).", flush=True)
        return

    bench = returns_from_bars(bars)
    m = min(len(strat), len(bench))

    def strat_fn(bbs):
        bb = bbs["BTC"]
        return ls.liq_spike_returns(bb, net_liq[:len(bb)], z_window=Z_WINDOW,
                                    z_threshold=Z_THRESHOLD, taker_bps=TAKER_BPS,
                                    slippage_bps=SLIP_BPS, exec_lag=1)
    perm = pm.permutation_test(strat_fn, {"BTC": bars}, n_permutations=200, seed=42)
    v = evaluate_edge(strat[:m], bench[:m], n_trials=4, sr_variance=0.05,
                      permutation=perm)

    print(f"\n=== VERDICT CRITIC — liquidation spike contrarian BTC (vraie data) ===",
          flush=True)
    print(f"   trades : {len(active)} | mean ret/trade : {statistics.mean(active)*100:+.3f}% "
          f"| total : {sum(active)*100:+.2f}% | sharpe : {v['gates']['sharpe']:+.3f}", flush=True)
    print(f"   gates : dsr={v['gates']['dsr']:.3f} beta={v['gates']['beta_neutral']['beta']:+.2f} "
          f"t_alpha={v['gates']['beta_neutral']['t_alpha']:+.2f} perm_p={v['gates']['permutation']:.3f}",
          flush=True)
    print(f"   PASS={v['pass']} | reasons={v['reasons']}", flush=True)
    print("   (30j = court ; si prometteur → étendre l'historique avant de croire)", flush=True)


if __name__ == "__main__":
    main()
