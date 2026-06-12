#!/usr/bin/env python3
"""Signaux CROSS-SECTIONAL long-short market-neutral — l'échappatoire au beta.

À chaque période : ranker l'univers par une feature (calculée sur le passé only),
long le top top_frac / short le bottom top_frac. Le portefeuille long-short est
dollar-neutral → beta ≈ 0 par construction → un edge éventuel ressort en ALPHA
(seul moyen vu que la TA per-symbol = beta partout, prouvé).

Jugé par le même CRITIC (evaluate_edge). v0 : alignement par longueur commune
(daily equities = dates alignées). Features : xs_momentum, xs_reversion.
"""
import statistics
from typing import Dict, List

import costs as _costs
from adapter import Bar, returns_from_bars
from verdict import _stats, evaluate_edge


def _trailing_return(closes: List[float], lb: int):
    if len(closes) <= lb:
        return None
    p0 = closes[-1 - lb]
    return (closes[-1] - p0) / p0 if p0 else None


def _xs_momentum(closes, p):
    return _trailing_return(closes, p["lookback"])


def _xs_reversion(closes, p):
    tr = _trailing_return(closes, p["lookback"])  # calcul UNE fois
    return -tr if tr is not None else None


# feature de RANKING par symbole (passé only)
XS_FEATURES = {"xs_momentum": _xs_momentum, "xs_reversion": _xs_reversion}


def cross_sectional_backtest(symbol_bars: Dict[str, List[Bar]], feature: str,
                             params: dict, top_frac: float, taker_bps: float,
                             slippage_bps: float = 0.0,
                             borrow_bps_annual: float = 0.0,
                             exec_lag: int = 1) -> List[float]:
    """Returns long-short période-par-période (dollar-neutral, no-look-ahead).
    Coûts : transaction (taker+slippage sur turnover) + borrow sur la jambe short.
    exec_lag : PARITÉ backtest=live — décision à close i, remplissage à i+exec_lag
    (défaut 1 : on ne remplit JAMAIS au close qu'on a utilisé pour décider)."""
    feat_fn = XS_FEATURES[feature]
    closes = {s: [b.close for b in bars] for s, bars in symbol_bars.items()}
    n = min(len(c) for c in closes.values())
    syms = list(closes)
    rets, prev_l, prev_s = [], set(), set()
    for i in range(n - 1 - exec_lag):
        feats = {}
        for s in syms:
            f = feat_fn(closes[s][:i + 1], params)  # décidé à close i (passé only)
            if f is not None:
                feats[s] = f
        if len(feats) < 4:
            rets.append(0.0)
            continue
        ranked = sorted(feats, key=lambda s: feats[s])
        k = max(1, int(len(ranked) * top_frac))
        longs, shorts = set(ranked[-k:]), set(ranked[:k])

        def nret(s):
            p0 = closes[s][i + exec_lag]   # rempli à i+lag (jamais le close décisionnel)
            p1 = closes[s][i + 1 + exec_lag]
            return (p1 - p0) / p0 if p0 else 0.0

        long_r = sum(nret(s) for s in longs) / len(longs)
        short_r = sum(nret(s) for s in shorts) / len(shorts)
        gross = long_r - short_r  # dollar-neutral
        turnover = len(longs ^ prev_l) + len(shorts ^ prev_s)
        trans = turnover * ((taker_bps + slippage_bps) / 1e4) / max(1, len(longs) + len(shorts))
        borrow = _costs.borrow_cost(-1.0, borrow_bps_annual, period_days=1)  # jambe short = 1 notional
        rets.append(gross - trans - borrow)
        prev_l, prev_s = longs, shorts
    return rets


def _sharpe(xs_):
    if len(xs_) < 2:
        return 0.0
    sd = statistics.pstdev(xs_)
    return statistics.mean(xs_) / sd if sd > 0 else 0.0


def judge_cross_sectional(symbol_bars, bench_bars, specs, taker_bps,
                          train_frac=0.7, slippage_bps=0.0,
                          borrow_bps_annual=0.0, exec_lag=1) -> List[dict]:
    """Juge des hypothèses cross-sectional via le CRITIC (OOS, DSR déflaté)."""
    n = min(len(b) for b in symbol_bars.values())
    cut = int(n * train_frac)
    train_bars = {s: b[:cut] for s, b in symbol_bars.items()}
    test_bars = {s: b[cut:n] for s, b in symbol_bars.items()}
    bench_test = returns_from_bars(bench_bars[cut:n])
    n_trials = len(specs)

    train_sh = []
    for sp in specs:
        p = sp["signal"]["params"]
        r = cross_sectional_backtest(train_bars, sp["signal"]["type"], p,
                                     p.get("top_frac", 0.3), taker_bps,
                                     slippage_bps, borrow_bps_annual, exec_lag)
        train_sh.append(_sharpe(r))
    sr_var = max(statistics.pvariance(train_sh) if len(train_sh) > 1 else 0.05,
                 1e-4)

    results = []
    for sp, tsh in zip(specs, train_sh):
        p = sp["signal"]["params"]
        test = cross_sectional_backtest(test_bars, sp["signal"]["type"], p,
                                        p.get("top_frac", 0.3), taker_bps,
                                        slippage_bps, borrow_bps_annual, exec_lag)
        m = min(len(test), len(bench_test))
        v = evaluate_edge(test[:m], bench_test[:m],
                          n_trials=n_trials, sr_variance=sr_var)
        results.append({"hypothesis": sp, "pass": v["pass"],
                        "reasons": v["reasons"], "gates": v["gates"],
                        "train_sharpe": round(tsh, 4), "n_trials": n_trials})
    return results


def cross_sectional_pbo(symbol_bars, specs, taker_bps, train_frac=0.7, S=8,
                        slippage_bps=0.0, borrow_bps_annual=0.0, exec_lag=1):
    """PBO/CSCV sur la matrice [T × hypothèses] des returns long-short (période
    test). Complète le CRITIC : sélectionner la meilleure hypothèse overfit-t-il ?"""
    n = min(len(b) for b in symbol_bars.values())
    cut = int(n * train_frac)
    test_bars = {s: b[cut:n] for s, b in symbol_bars.items()}
    cols = []
    for sp in specs:
        p = sp["signal"]["params"]
        cols.append(cross_sectional_backtest(
            test_bars, sp["signal"]["type"], p, p.get("top_frac", 0.3),
            taker_bps, slippage_bps, borrow_bps_annual, exec_lag))
    if len(cols) < 2:
        return float("nan")
    m = min(len(c) for c in cols)
    matrix = [[cols[j][t] for j in range(len(cols))] for t in range(m)]
    pbo, _ = _stats.pbo_cscv(matrix, S=S)
    return pbo


def survivors(results):
    return [r for r in results if r["pass"]]
