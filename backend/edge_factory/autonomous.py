#!/usr/bin/env python3
"""Boucle autonome complète : LLM -> DSL -> backtest OOS -> CRITIC -> mémoire.

Exécute des specs DSL (proposées par l'agent LLM) via le juge incorruptible :
backtest no-look-ahead, DSR déflaté par #specs + Var(SR) cross-trials, beta-neutral.
C'est la Phase 2 « B » assemblée : le système propose, juge, et logge, sans que
l'opérateur n'écrive de règle.
"""
import statistics
from typing import Callable, List

from adapter import Bar, returns_from_bars
from hypothesis_dsl import build_signal
from verdict import evaluate_edge


def _bt(bars: List[Bar], posfn: Callable[[List[float]], int],
        taker_bps: float) -> List[float]:
    """Backtest 1 symbole, no-look-ahead (posfn ne voit que le passé)."""
    closes = [b.close for b in bars]
    rets, pos = [], 0
    for i in range(len(bars) - 1):
        sig = posfn(closes[:i + 1])
        p0 = bars[i].close
        mkt = (bars[i + 1].close - p0) / p0 if p0 else 0.0
        cost = abs(sig - pos) * (taker_bps / 1e4)
        rets.append(sig * mkt - cost)
        pos = sig
    return rets


def _portfolio(symbol_bars, posfn, taker_bps, lo, hi) -> List[float]:
    per = {}
    for bars in symbol_bars.values():
        for t, v in enumerate(_bt(bars[lo:hi], posfn, taker_bps)):
            per.setdefault(t, []).append(v)
    return [statistics.mean(per[t]) for t in sorted(per)]


def _sharpe(xs):
    if len(xs) < 2:
        return 0.0
    sd = statistics.pstdev(xs)
    return statistics.mean(xs) / sd if sd > 0 else 0.0


def run_dsl_hypotheses(symbol_bars, bench_bars, specs, taker_bps,
                       train_frac=0.7) -> List[dict]:
    """Juge une liste de specs DSL ; DSR déflaté par len(specs) + Var(SR train)."""
    posfns = [build_signal(s) for s in specs]
    n = min(len(b) for b in symbol_bars.values())
    cut = int(n * train_frac)
    bench_test = returns_from_bars(bench_bars[cut:n])
    n_trials = len(specs)
    train_sh = [_sharpe(_portfolio(symbol_bars, pf, taker_bps, 0, cut))
                for pf in posfns]
    sr_var = max(statistics.pvariance(train_sh) if len(train_sh) > 1 else 0.05,
                 1e-4)
    results = []
    for spec, pf, tsh in zip(specs, posfns, train_sh):
        test = _portfolio(symbol_bars, pf, taker_bps, cut, n)
        m = min(len(test), len(bench_test))
        v = evaluate_edge(test[:m], bench_test[:m],
                          n_trials=n_trials, sr_variance=sr_var)
        results.append({"hypothesis": spec, "pass": v["pass"],
                        "reasons": v["reasons"], "gates": v["gates"],
                        "train_sharpe": round(tsh, 4), "n_trials": n_trials})
    return results


def survivors(results: List[dict]) -> List[dict]:
    return [r for r in results if r["pass"]]
