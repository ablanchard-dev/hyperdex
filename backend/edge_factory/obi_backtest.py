#!/usr/bin/env python3
"""Backtest OBI→move — l'order-book imbalance prédit-il le prochain mouvement de mid ?

Sur une série [(time, obi, mid)] (issue du recorder, triée) : à chaque t où |OBI[t]|>seuil,
on prend position dans le sens de l'OBI et on capture le move mid[t+lag]→mid[t+1+lag].
exec_lag = latence retail (lag=1 réaliste : on n'exécute pas au snapshot qu'on observe).
cost_bps = coût round-trip (2×taker + spread). Renvoie les returns NETS par trade.

⚠️ prior faible : à 10s, le move typique (~2 bps) est souvent < coût (~9 bps) → mur
structurel. On mesure gross (cost=0) ET net pour le quantifier honnêtement.
"""
from typing import List, Tuple


def obi_backtest(series: List[Tuple[int, float, float]], threshold: float = 0.2,
                 cost_bps: float = 9.3, exec_lag: int = 1) -> List[float]:
    """series = [(time, obi, mid)] triée. Retourne les returns par snapshot (0 si pas
    de trade). Position au snapshot i (|obi|>seuil) → move mid[i+lag]→mid[i+1+lag]."""
    n = len(series)
    cost = cost_bps / 1e4
    rets: List[float] = []
    for i in range(n - 1 - exec_lag):
        obi = series[i][1]
        if abs(obi) < threshold:
            rets.append(0.0)
            continue
        direction = 1.0 if obi > 0 else -1.0
        m0 = series[i + exec_lag][2]
        m1 = series[i + 1 + exec_lag][2]
        raw = direction * (m1 - m0) / m0 if m0 else 0.0
        rets.append(raw - cost)
    return rets
