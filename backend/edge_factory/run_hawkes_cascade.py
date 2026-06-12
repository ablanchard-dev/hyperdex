#!/usr/bin/env python3
"""PREMIER TEST D'EDGE PROBABILISTE SUR VRAIE DATA — Hawkes cascades de liquidation.

Pipeline bout-en-bout :
  1. fetch liquidations réelles (Coinalyze) + candles prix (Coinalyze) pour BTC perp.
  2. calibre le Hawkes des liquidations (branching ratio réel des cascades).
  3. évalue l'intensité λ sur chaque barre → flags de cascade (seuil = quantile haut).
  4. signal mean-reversion : entrer CONTRE la cascade (V-shape snapback), coûts réels.
  5. juge via CRITIC : DSR déflaté + beta-neutral (vs BTC) + permutation p<0.05.

PRÉ-ENREGISTRÉ (lancé honnêtement) : BTC perp Binance, 30j × 1h, seuil cascade =
quantile 0.90 de l'intensité, coûts taker 4.5 + slip 5 bps, exec_lag=1. On ne TUNE
pas le seuil pour faire passer : un seul run, verdict accepté tel quel.
"""
import os
import statistics
import sys
import time

sys.path.insert(0, "/opt/app/hyperdex/backend")
sys.path.insert(0, "/opt/app/hyperdex/backend/edge_factory")
import coinalyze as cz
import hawkes as hk
import hawkes_signal as hs
import permutation as pm
from adapter import Bar, returns_from_bars
from verdict import evaluate_edge

import httpx

KEY = open(os.path.expanduser("~/.coinalyze_key")).read().strip()
SYMBOL = "BTCUSDT_PERP.A"
DAYS = 30
INTERVAL = "1hour"
HOUR_S = 3600
CASCADE_Q = 0.90
TAKER_BPS = 4.5
SLIP_BPS = 5.0


def _get(path, params):
    params = dict(params)
    r = httpx.get(f"https://api.coinalyze.net/v1/{path}",
                  headers={"api_key": KEY}, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def quantile(xs, q):
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))]


def main():
    to = int(time.time())
    frm = to - DAYS * 24 * HOUR_S
    print(f"[{time.strftime('%H:%M:%S')}] fetch liquidations + prix {SYMBOL} {DAYS}j...",
          flush=True)
    liq_raw = _get("liquidation-history", {"symbols": SYMBOL, "interval": INTERVAL,
                                           "from": frm, "to": to, "convert_to_usd": "true"})
    px_raw = _get("ohlcv-history", {"symbols": SYMBOL, "interval": INTERVAL,
                                    "from": frm, "to": to})
    events = cz.parse_liquidation_history(liq_raw)
    px_hist = px_raw[0]["history"] if px_raw else []
    bars = [Bar(ts=int(c["t"]) * 1000, close=float(c["c"])) for c in px_hist]
    print(f"   {len(events)} events liq | {len(bars)} barres prix", flush=True)
    if len(bars) < 100 or len(events) < 50:
        print("   data insuffisante.", flush=True)
        return

    # calibration Hawkes sur les temps d'événements (en secondes)
    times_s = sorted((e["ts"] - bars[0].ts) / 1000.0 for e in events
                     if e["ts"] >= bars[0].ts)
    T = (bars[-1].ts - bars[0].ts) / 1000.0
    fit = hk.fit_mle(times_s, T=T)
    print(f"   Hawkes : mu={fit['mu']:.4e} alpha={fit['alpha']:.4e} "
          f"beta={fit['beta']:.4e} | branching ratio ρ={fit['branching_ratio']:.3f} "
          f"({'sous-critique' if fit['branching_ratio'] < 1 else 'SUPERCRITIQUE'})", flush=True)

    # intensité sur la grille des barres (bornes temporelles)
    grid = [(b.ts - bars[0].ts) / 1000.0 for b in bars]
    inten = hs.intensity_series(times_s, grid, fit["mu"], fit["alpha"], fit["beta"])
    thr = quantile(inten, CASCADE_Q)
    flags = hs.cascade_flags(inten, thr)
    bar_ts = [b.ts for b in bars]
    pressure = hs.attribute_pressure_to_bars(events, bar_ts)
    n_casc = sum(flags)
    print(f"   seuil cascade (q{CASCADE_Q})={thr:.4f} | barres en cascade : {n_casc}", flush=True)

    strat = hs.mean_reversion_returns(bars, flags, pressure,
                                      taker_bps=TAKER_BPS, slippage_bps=SLIP_BPS,
                                      exec_lag=1)
    active = [r for r in strat if r != 0.0]
    if len(active) < 10:
        print(f"   trop peu de trades actifs ({len(active)}) — non concluant.", flush=True)
        return

    bench = returns_from_bars(bars)
    m = min(len(strat), len(bench))

    # CRITIC : permutation sur la stratégie complète (la structure cascade compte-t-elle ?)
    def strat_fn(bbs):
        bb = bbs["BTC"]
        pr = hs.attribute_pressure_to_bars(events, [b.ts for b in bb])
        return hs.mean_reversion_returns(bb, flags, pr, taker_bps=TAKER_BPS,
                                         slippage_bps=SLIP_BPS, exec_lag=1)
    perm = pm.permutation_test(strat_fn, {"BTC": bars}, n_permutations=200, seed=42)

    v = evaluate_edge(strat[:m], bench[:m], n_trials=4, sr_variance=0.05,
                      permutation=perm)
    print(f"\n=== VERDICT CRITIC — Hawkes cascade mean-reversion BTC ===", flush=True)
    print(f"   trades actifs : {len(active)} | mean ret/trade : "
          f"{statistics.mean(active)*100:+.3f}% | sharpe : {v['gates']['sharpe']:+.3f}",
          flush=True)
    print(f"   gates : dsr={v['gates']['dsr']:.3f} beta={v['gates']['beta_neutral']['beta']:+.2f} "
          f"t_alpha={v['gates']['beta_neutral']['t_alpha']:+.2f} "
          f"perm_p={v['gates']['permutation']:.3f}", flush=True)
    print(f"   PASS={v['pass']} | reasons={v['reasons']}", flush=True)


if __name__ == "__main__":
    main()
