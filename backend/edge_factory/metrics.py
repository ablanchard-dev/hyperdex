#!/usr/bin/env python3
"""Métriques de performance pur-python — portées de a prior project + littérature quant.

Toutes opèrent sur une liste de returns par-période (pas de prix). Définitions
canoniques, zéro dépendance (statistics stdlib uniquement) → tourne Paris & Tokyo.
maxDD reprend la math de a prior project backtest/metrics.py (max(peak-equity) sur l'equity
cumulée). Sortino/Calmar = définitions standard (López de Prado / Bacon).
"""
import math
import statistics
from typing import List

PERIODS_PER_YEAR = 252


def sharpe(returns: List[float]) -> float:
    if len(returns) < 2:
        return 0.0
    sd = statistics.pstdev(returns)
    return statistics.mean(returns) / sd if sd > 0 else 0.0


def max_drawdown(returns: List[float]) -> float:
    """max(peak - equity) sur l'equity cumulée (somme des returns). ≥ 0."""
    equity = peak = mdd = 0.0
    for r in returns:
        equity += r
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > mdd:
            mdd = dd
    return mdd


def profit_factor(returns: List[float]) -> float:
    """somme des gains / |somme des pertes|. inf si aucune perte."""
    gains = sum(r for r in returns if r > 0)
    losses = -sum(r for r in returns if r < 0)
    if losses == 0:
        return float("inf") if gains > 0 else 0.0
    return gains / losses


def expectancy(returns: List[float]) -> float:
    return statistics.mean(returns) if returns else 0.0


def downside_deviation(returns: List[float], mar: float = 0.0) -> float:
    """Écart-type des seuls returns sous le MAR (minimum acceptable return)."""
    downs = [(r - mar) ** 2 for r in returns if r < mar]
    if not downs:
        return 0.0
    return math.sqrt(sum(downs) / len(returns))


def sortino(returns: List[float], mar: float = 0.0) -> float:
    """mean excess / downside-deviation. inf si aucun downside (et mean>0)."""
    if not returns:
        return 0.0
    dd = downside_deviation(returns, mar)
    mean_excess = statistics.mean(returns) - mar
    if dd == 0:
        return float("inf") if mean_excess > 0 else 0.0
    return mean_excess / dd


def calmar(returns: List[float], periods_per_year: int = PERIODS_PER_YEAR) -> float:
    """Return annualisé / |maxDD|. inf si maxDD nul (et perf>0).

    Return annualisé en convention additive (somme des returns × an/échantillon) —
    cohérent avec maxDD défini sur l'equity cumulée additive ci-dessus.
    """
    mdd = max_drawdown(returns)
    if not returns:
        return 0.0
    total = sum(returns)
    annualized = total * periods_per_year / len(returns)
    if mdd == 0:
        return float("inf") if annualized > 0 else 0.0
    return annualized / mdd


def summary(returns: List[float],
            periods_per_year: int = PERIODS_PER_YEAR) -> dict:
    """Tous les diagnostics en un appel (pour les verdicts / Research Memory)."""
    return {
        "sharpe": sharpe(returns),
        "sortino": sortino(returns),
        "calmar": calmar(returns, periods_per_year),
        "max_drawdown": max_drawdown(returns),
        "profit_factor": profit_factor(returns),
        "expectancy": expectancy(returns),
        "n": len(returns),
    }
