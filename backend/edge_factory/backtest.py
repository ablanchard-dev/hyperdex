#!/usr/bin/env python3
"""Backtest de signal SANS look-ahead + runner OOS jugé par le CRITIC.

Le signal à la barre i n'utilise QUE les barres 0..i (passé), et la position
gagne le return [i, i+1]. Garanti sans fuite (test no-look-ahead prefix).
Backtest minimal Phase 1 (un moteur plus complet reste à construire).
"""
from pathlib import Path
import statistics
from typing import List

from adapter import Bar, returns_from_bars
from verdict import evaluate_edge


def ts_momentum_signal(closes: List[float], lookback: int) -> int:
    """+1 si close > close il y a `lookback` barres, -1 si <, 0 si hist insuffisant."""
    if len(closes) <= lookback:
        return 0
    return 1 if closes[-1] > closes[-1 - lookback] else -1


def backtest_symbol(bars: List[Bar], lookback: int,
                    taker_bps: float) -> List[float]:
    """Returns période-par-période d'un momentum time-series, net de coût, no-LA."""
    closes = [b.close for b in bars]
    rets, pos = [], 0
    for i in range(len(bars) - 1):
        sig = ts_momentum_signal(closes[:i + 1], lookback)  # passé seulement
        p0 = bars[i].close
        mkt = (bars[i + 1].close - p0) / p0 if p0 else 0.0
        cost = abs(sig - pos) * (taker_bps / 1e4)
        rets.append(sig * mkt - cost)  # position sig tenue sur [i, i+1]
        pos = sig
    return rets


def _sharpe(xs):
    if len(xs) < 2:
        return 0.0
    sd = statistics.pstdev(xs)
    return (statistics.mean(xs) / sd) if sd > 0 else 0.0


def oos_edge_test(symbol_bars, bench_bars, lookbacks, taker_bps,
                  train_frac=0.7):
    """Sélection lookback sur TRAIN, jugement OOS sur TEST via le CRITIC.

    symbol_bars : dict {symbol: [Bar]} (portefeuille small-cap equal-weight).
    Sélectionne le meilleur lookback par Sharpe train, puis evaluate_edge sur
    le test, en déflatant le DSR par n_trials = nb de lookbacks essayés.
    """
    # portefeuille equal-weight : moyenne des returns par période, par lookback
    def portfolio_rets(lb, lo, hi):
        per_period = {}
        for bars in symbol_bars.values():
            seg = bars[lo:hi]
            r = backtest_symbol(seg, lb, taker_bps)
            for t, val in enumerate(r):
                per_period.setdefault(t, []).append(val)
        return [statistics.mean(per_period[t]) for t in sorted(per_period)]

    n = min(len(b) for b in symbol_bars.values())
    cut = int(n * train_frac)
    train_sharpes = {lb: _sharpe(portfolio_rets(lb, 0, cut)) for lb in lookbacks}
    best_lb = max(lookbacks, key=lambda lb: train_sharpes[lb])
    sr_var = statistics.pvariance(list(train_sharpes.values())) if len(lookbacks) > 1 else 0.05

    test_rets = portfolio_rets(best_lb, cut, n)
    bench_test = returns_from_bars(bench_bars[cut:n])
    m = min(len(test_rets), len(bench_test))
    v = evaluate_edge(test_rets[:m], bench_test[:m],
                      n_trials=len(lookbacks), sr_variance=max(sr_var, 1e-4))
    v["best_lookback"] = best_lb
    v["train_sharpes"] = {k: round(val, 3) for k, val in train_sharpes.items()}
    v["test_periods"] = m
    return v


if __name__ == "__main__":
    import sys
    import time
    if "--live" in sys.argv:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from app.services.hl_api.info_client import InfoClient
        from hl_adapter import HLSmallCapAdapter
        a = HLSmallCapAdapter(InfoClient(), vol_max_usd=5_000_000)  # seuil bas
        end = int(time.time() * 1000)
        start = end - 60 * 24 * 3600 * 1000  # 60j
        uni = a.universe()[:12]  # échantillon de small-caps
        print(f"small-caps testées ({len(uni)}): {uni}", flush=True)
        sb = {}
        for s in uni:
            b = a.history(s, start, end)
            if len(b) > 200:
                sb[s] = b
        bench = a.benchmark(start, end)
        print(f"symbols avec >200 barres: {len(sb)}, bench BTC barres: {len(bench)}",
              flush=True)
        if sb:
            v = oos_edge_test(sb, bench, lookbacks=[6, 12, 24, 48],
                              taker_bps=9.32)
            print("\n=== 1er VRAI test d'edge (momentum small-cap HL, OOS) ===")
            print("best_lookback:", v["best_lookback"], "| train_sharpes:",
                  v["train_sharpes"])
            print("VERDICT:", "PASS" if v["pass"] else "FAIL", "| reasons:",
                  v["reasons"])
            print("gates:", v["gates"])
    else:
        print("usage: python backtest.py --live")
