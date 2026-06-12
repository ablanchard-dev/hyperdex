#!/usr/bin/env python3
"""HypothesisGenerator v0 — recherche systématique d'edge au-dessus du CRITIC.

Énumère un espace d'hypothèses (familles de signaux × params), backteste chaque
OOS (no-look-ahead), passe au CRITIC `evaluate_edge` en DÉFLATANT le DSR par :
  - n_trials  = nombre TOTAL d'hypothèses testées (anti-snoop non-négociable),
  - sr_variance = variance des Sharpe TRAIN à travers toutes les hypothèses.
Collecte les survivants. v0 systématique ; agents LLM (Pattern/Hypothesis/
Mutation) = couche suivante AU-DESSUS de ce socle.
"""
import statistics
from typing import Dict, List

from adapter import Bar, returns_from_bars
from verdict import evaluate_edge


# --- familles de signaux : (closes_jusqu'à_t, params) -> position {-1,0,1} ---
def _momentum(closes, p):
    lb = p["lookback"]
    if len(closes) <= lb:
        return 0
    return 1 if closes[-1] > closes[-1 - lb] else -1


def _mean_reversion(closes, p):
    lb = p["lookback"]
    if len(closes) <= lb:
        return 0
    return -1 if closes[-1] > closes[-1 - lb] else 1  # fade le mouvement


def _breakout(closes, p):
    lb = p["lookback"]
    if len(closes) <= lb:
        return 0
    window = closes[-1 - lb:-1]
    if closes[-1] > max(window):
        return 1
    if closes[-1] < min(window):
        return -1
    return 0


SIGNALS = {"momentum": _momentum, "mean_reversion": _mean_reversion,
           "breakout": _breakout}


def generate_space(families: List[str], lookbacks: List[int]) -> List[dict]:
    return [{"family": f, "params": {"lookback": lb}}
            for f in families for lb in lookbacks]


def _bt_symbol(bars: List[Bar], fn, params, taker_bps: float) -> List[float]:
    """Backtest 1 symbole, no-look-ahead (signal à i n'utilise que 0..i)."""
    closes = [b.close for b in bars]
    rets, pos = [], 0
    for i in range(len(bars) - 1):
        sig = fn(closes[:i + 1], params)
        p0 = bars[i].close
        mkt = (bars[i + 1].close - p0) / p0 if p0 else 0.0
        cost = abs(sig - pos) * (taker_bps / 1e4)
        rets.append(sig * mkt - cost)
        pos = sig
    return rets


def _portfolio(symbol_bars, fn, params, taker_bps, lo, hi) -> List[float]:
    per: Dict[int, list] = {}
    for bars in symbol_bars.values():
        for t, v in enumerate(_bt_symbol(bars[lo:hi], fn, params, taker_bps)):
            per.setdefault(t, []).append(v)
    return [statistics.mean(per[t]) for t in sorted(per)]


def _sharpe(xs):
    if len(xs) < 2:
        return 0.0
    sd = statistics.pstdev(xs)
    return statistics.mean(xs) / sd if sd > 0 else 0.0


def run_generator(symbol_bars, bench_bars, space, taker_bps,
                  train_frac=0.7) -> List[dict]:
    """Évalue tout l'espace ; DSR déflaté par len(space) + Var(SR train)."""
    n = min(len(b) for b in symbol_bars.values())
    cut = int(n * train_frac)
    bench_test = returns_from_bars(bench_bars[cut:n])
    n_trials = len(space)

    # 1) Sharpe TRAIN de chaque hypothèse -> variance cross-trials (pour DSR)
    train_sh = []
    fns = [SIGNALS[h["family"]] for h in space]
    for h, fn in zip(space, fns):
        train_sh.append(_sharpe(_portfolio(symbol_bars, fn, h["params"],
                                           taker_bps, 0, cut)))
    sr_var = statistics.pvariance(train_sh) if len(train_sh) > 1 else 0.05
    sr_var = max(sr_var, 1e-4)

    # 2) jugement OOS de chaque hypothèse, déflaté
    results = []
    for h, fn, tsh in zip(space, fns, train_sh):
        test = _portfolio(symbol_bars, fn, h["params"], taker_bps, cut, n)
        m = min(len(test), len(bench_test))
        v = evaluate_edge(test[:m], bench_test[:m],
                          n_trials=n_trials, sr_variance=sr_var)
        results.append({"hypothesis": h, "pass": v["pass"],
                        "reasons": v["reasons"], "gates": v["gates"],
                        "train_sharpe": round(tsh, 4), "n_trials": n_trials})
    return results


def survivors(results: List[dict]) -> List[dict]:
    return [r for r in results if r["pass"]]
