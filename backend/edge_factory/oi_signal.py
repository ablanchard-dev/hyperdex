#!/usr/bin/env python3
"""Divergence open-interest / prix — signal de POSITIONNEMENT crowded (P2).

Hypothèse (recherche groundée : OI spike sans move prix = buildup crowded → réversion ;
divergence OI-prix = indicateur de retournement) : on mesure
  divergence[i] = z(ΔOI)[i] − z(Δprix)[i]   (z rolling PASSÉ-only)
Quand |divergence| > seuil : l'OI bouge bien plus que le prix = positions s'accumulent
sans conviction directionnelle → entrer CONTRE le côté crowded (OI monte + prix mou →
SHORT ; OI monte côté short → LONG). Distinct de momentum/reversion-prix et de liq-spike.

No-look-ahead : z[i] n'utilise que [i-window, i). Réutilise liq_spike.rolling_zscore.
"""
from typing import List

from adapter import Bar
from liq_spike import rolling_zscore


def pct_change(series: List[float]) -> List[float]:
    """Variation relative période-à-période (len-1)."""
    return [(series[i] - series[i - 1]) / series[i - 1] if series[i - 1] else 0.0
            for i in range(1, len(series))]


def oi_price_divergence(bars: List[Bar], oi: List[float],
                        window: int = 48) -> List[float]:
    """divergence[i] = z(ΔOI)[i] − z(Δprix)[i], alignée sur les barres (len(bars)).

    Index 0 = pas de variation calculable → 0. Le reste : z-score rolling passé-only
    de la variation d'OI moins celui de la variation de prix."""
    n = len(bars)
    closes = [b.close for b in bars]
    m = min(n, len(oi))
    d_px = [0.0] + pct_change(closes[:m])       # len m
    d_oi = [0.0] + pct_change(oi[:m])           # len m
    z_px = rolling_zscore(d_px, window)
    z_oi = rolling_zscore(d_oi, window)
    div = [z_oi[i] - z_px[i] for i in range(m)]
    # pad à la longueur des barres si besoin
    return div + [0.0] * (n - m)


def oi_divergence_returns(bars: List[Bar], oi: List[float], window: int = 48,
                          threshold: float = 2.0, taker_bps: float = 4.5,
                          slippage_bps: float = 5.0, exec_lag: int = 1) -> List[float]:
    """Contrarian sur divergence OI-prix. Sur |divergence|>seuil à la barre i :
    divergence>0 (OI monte >> prix = longs crowded) → SHORT ; <0 → LONG. Fill i+lag."""
    closes = [b.close for b in bars]
    n = len(closes)
    div = oi_price_divergence(bars, oi, window)
    cost = (taker_bps + slippage_bps) / 1e4
    rets: List[float] = []
    for i in range(n - 1 - exec_lag):
        if abs(div[i]) < threshold:
            rets.append(0.0)
            continue
        direction = -1.0 if div[i] > 0 else 1.0  # contre le côté crowded
        p0 = closes[i + exec_lag]
        p1 = closes[i + 1 + exec_lag]
        raw = direction * (p1 - p0) / p0 if p0 else 0.0
        rets.append(raw - cost)
    return rets
