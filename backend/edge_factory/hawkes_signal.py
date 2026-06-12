#!/usr/bin/env python3
"""Signal de trading Hawkes — détecte le pic de cascade → entrée mean-reversion.

Hypothèse (recherche groundée : V-shaped flush après cascade, snapback sur falling
OI = liquidity grab → mean-revert) : quand l'intensité Hawkes des liquidations
dépasse un seuil (cascade en cours) et que le prix a brutalement bougé, ENTRER
CONTRE la direction des liquidations (long si ce sont des longs liquidés = sell-off
forcé) pour capter le rebond. exec_lag=1 (no-look-ahead : décision à la barre i sur
l'intensité ≤ i, fill à i+1).

Le signal est PUR (events liq + barres prix → returns de stratégie) → testable sur
cascades synthétiques avant la vraie data Coinalyze. Jugé ensuite par le CRITIC.
"""
import bisect
from typing import Dict, List

import hawkes as hk
from adapter import Bar


def intensity_series(event_times: List[float], grid: List[float],
                     mu: float, alpha: float, beta: float) -> List[float]:
    """λ(t) évalué sur une grille temporelle (les bornes des barres prix)."""
    return [hk.intensity(t, event_times, mu, alpha, beta) for t in grid]


def cascade_flags(intensities: List[float], threshold: float) -> List[bool]:
    """True quand l'intensité dépasse le seuil = cascade en cours à cette barre."""
    return [x > threshold for x in intensities]


def mean_reversion_returns(bars: List[Bar], flags: List[bool],
                           signed_pressure: List[float],
                           taker_bps: float = 4.5, slippage_bps: float = 5.0,
                           exec_lag: int = 1) -> List[float]:
    """Returns de la stratégie : à chaque barre i en cascade, entrer CONTRE la
    pression de liquidation (signed_pressure[i] < 0 = longs liquidés/sell-off →
    on LONG le rebond), fill à i+exec_lag, sortie à la barre suivante.

    signed_pressure[i] : somme signée des notionals de liq attribués à la barre i
    (négatif = longs liquidés = vente forcée ; positif = shorts liquidés).
    No-look-ahead : flags/pressure à i n'utilisent que le passé ≤ i.
    """
    closes = [b.close for b in bars]
    n = len(closes)
    cost = (taker_bps + slippage_bps) / 1e4
    rets: List[float] = []
    for i in range(n - 1 - exec_lag):
        if not flags[i] or signed_pressure[i] == 0.0:
            rets.append(0.0)
            continue
        # contre la cascade : longs liquidés (pressure<0) -> on LONG (dir=+1)
        direction = 1.0 if signed_pressure[i] < 0 else -1.0
        p0 = closes[i + exec_lag]
        p1 = closes[i + 1 + exec_lag]
        raw = direction * (p1 - p0) / p0 if p0 else 0.0
        rets.append(raw - cost)  # coût d'un aller-retour (entrée+sortie ~ 2 legs)
    return rets


def attribute_pressure_to_bars(events: List[Dict], bar_ts_ms: List[int]) -> List[float]:
    """Somme signée des notionals de liq tombant dans chaque barre [t_i, t_{i+1}).
    long liquidé → −notional (sell-off) ; short liquidé → +notional."""
    pressure = [0.0] * len(bar_ts_ms)
    n = len(bar_ts_ms)
    if n < 2:
        return pressure
    last = bar_ts_ms[-1]
    for e in events:
        ts = e["ts"]
        if ts < bar_ts_ms[0] or ts >= last:
            continue  # hors de la plage des barres
        # bisect_right - 1 = index de la barre contenante (barres triées) → O(log m)
        i = bisect.bisect_right(bar_ts_ms, ts) - 1
        if 0 <= i < n - 1:
            sign = -1.0 if e["liquidated_side"] == "long" else 1.0
            pressure[i] += sign * e["notional"]
    return pressure
