#!/usr/bin/env python3
"""Fabriques de chasseurs — branchent les VRAIES familles sur hunt.Registry.

Chaque make_*_hunter(data, ...) retourne un Hunter (callable ()->dict) qui :
  1. fait le split temporel train/test (le CRITIC juge OOS),
  2. lance le backtest de la famille sur la portion TEST (coûts réels, exec_lag=1),
  3. renvoie {strat, bench, n_trials, sr_variance} pour evaluate_edge.

La data est PRÉ-FETCHÉE (passée en argument) → testable sans réseau. Le fetch live
des vraies données est dans run_hunt.py. n_trials = largeur de la grille de la famille
(déflation DSR honnête) ; sr_variance = variance des Sharpe cross-essais (défaut 0.05).
"""
from typing import Callable, Dict, List

import cross_sectional as xs
import funding as fd
import lead_lag as ll
import liq_spike as ls
import oi_signal as oi
from adapter import Bar, returns_from_bars

Hunter = Callable[[], Dict]


def make_cross_sectional_hunter(symbol_bars: Dict[str, List[Bar]],
                                bench_bars: List[Bar], feature: str, params: dict,
                                top_frac: float = 0.3, taker_bps: float = 4.5,
                                slippage_bps: float = 5.0, n_trials: int = 1,
                                sr_variance: float = 0.05, train_frac: float = 0.7,
                                exec_lag: int = 1) -> Hunter:
    def hunter() -> Dict:
        n = min(len(b) for b in symbol_bars.values())
        cut = int(n * train_frac)
        test_bars = {s: b[cut:n] for s, b in symbol_bars.items()}
        strat = xs.cross_sectional_backtest(test_bars, feature, params, top_frac,
                                            taker_bps, slippage_bps, exec_lag=exec_lag)
        bench = returns_from_bars(bench_bars[cut:n])
        m = min(len(strat), len(bench))
        return {"strat": strat[:m], "bench": bench[:m],
                "n_trials": n_trials, "sr_variance": sr_variance}
    return hunter


def make_funding_carry_hunter(funding_by_coin: Dict[str, List[float]],
                              premium_by_coin: Dict[str, List[float]],
                              bench_bars: List[Bar], fee_bps: float = 1.5,
                              n_trials: int = 1, sr_variance: float = 0.05,
                              train_frac: float = 0.7, exec_lag: int = 1,
                              smooth: int = 24) -> Hunter:
    def hunter() -> Dict:
        coins = list(funding_by_coin)
        n = min(len(funding_by_coin[c]) for c in coins)
        cut = int(n * train_frac)
        series = [fd.carry_neutral_backtest(funding_by_coin[c][cut:n],
                                            premium_by_coin[c][cut:n],
                                            fee_bps=fee_bps, exec_lag=exec_lag,
                                            smooth=smooth) for c in coins]
        m = min(len(s) for s in series)
        strat = [sum(series[j][t] for j in range(len(series))) / len(series)
                 for t in range(m)]
        bench = returns_from_bars(bench_bars[cut:n])
        mm = min(len(strat), len(bench))
        return {"strat": strat[:mm], "bench": bench[:mm],
                "n_trials": n_trials, "sr_variance": sr_variance}
    return hunter


def make_liq_spike_hunter(bars: List[Bar], net_liq: List[float],
                          z_window: int = 48, z_threshold: float = 2.0,
                          taker_bps: float = 4.5, slippage_bps: float = 5.0,
                          n_trials: int = 1, sr_variance: float = 0.05,
                          exec_lag: int = 1) -> Hunter:
    def hunter() -> Dict:
        strat = ls.liq_spike_returns(bars, net_liq, z_window=z_window,
                                     z_threshold=z_threshold, taker_bps=taker_bps,
                                     slippage_bps=slippage_bps, exec_lag=exec_lag)
        bench = returns_from_bars(bars)
        m = min(len(strat), len(bench))
        return {"strat": strat[:m], "bench": bench[:m],
                "n_trials": n_trials, "sr_variance": sr_variance}
    return hunter


def make_oi_divergence_hunter(bars: List[Bar], oi_series: List[float],
                              window: int = 48, threshold: float = 2.0,
                              taker_bps: float = 4.5, slippage_bps: float = 5.0,
                              n_trials: int = 1, sr_variance: float = 0.05,
                              exec_lag: int = 1) -> Hunter:
    """Famille divergence OI-prix (positionnement crowded → contrarian). bench = le coin
    lui-même (returns prix) → le CRITIC mesure l'alpha du signal de positionnement."""
    def hunter() -> Dict:
        strat = oi.oi_divergence_returns(bars, oi_series, window=window,
                                         threshold=threshold, taker_bps=taker_bps,
                                         slippage_bps=slippage_bps, exec_lag=exec_lag)
        bench = returns_from_bars(bars)
        m = min(len(strat), len(bench))
        return {"strat": strat[:m], "bench": bench[:m],
                "n_trials": n_trials, "sr_variance": sr_variance}
    return hunter


def make_lead_lag_hunter(symbol_bars: Dict[str, List[Bar]], btc_bars: List[Bar],
                         lookback: int = 2, top_frac: float = 0.3,
                         taker_bps: float = 4.5, slippage_bps: float = 5.0,
                         beta_window: int = 48, n_trials: int = 1,
                         sr_variance: float = 0.05, train_frac: float = 0.7,
                         exec_lag: int = 1) -> Hunter:
    def hunter() -> Dict:
        n = min(min(len(b) for b in symbol_bars.values()), len(btc_bars))
        cut = int(n * train_frac)
        sb = {s: b[cut:n] for s, b in symbol_bars.items()}
        strat = ll.lead_lag_backtest(sb, btc_bars[cut:n], lookback, top_frac,
                                     taker_bps, slippage_bps, beta_window, exec_lag)
        bench = returns_from_bars(btc_bars[cut:n])
        m = min(len(strat), len(bench))
        return {"strat": strat[:m], "bench": bench[:m],
                "n_trials": n_trials, "sr_variance": sr_variance}
    return hunter
