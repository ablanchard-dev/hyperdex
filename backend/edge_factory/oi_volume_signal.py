#!/usr/bin/env python3
"""Ratio ΔOI / volume — accumulation passive vs participation active (angle distinct).

Hypothèse (recherche groundée : OI montant + volume bas = accumulation passive tenue
→ continuation ; breakout sur OI montant 70% succès vs 40% sur OI baissant) :
  ratio[i] = |ΔOI[i]| / volume[i]   (positionnement par unité d'activité)
Un z-score rolling passé-only élevé du ratio = OI bouge anormalement sans volume =
accumulation passive → SUIVRE la direction de l'OI (momentum de positionnement, distinct
de la divergence OI-prix qui est contrarian). No-look-ahead ; fill i+exec_lag.

Réutilise liq_spike.rolling_zscore + oi_signal.pct_change.
"""
from typing import List

from adapter import Bar
from liq_spike import rolling_zscore
from oi_signal import pct_change


def oi_vol_ratio(oi: List[float], volume: List[float]) -> List[float]:
    """|ΔOI relatif| / volume, len = len(oi)-1. Volume nul → 0 (pas de signal)."""
    d_oi = pct_change(oi)            # len-1
    vol = volume[1:len(d_oi) + 1] if len(volume) > len(d_oi) else volume[:len(d_oi)]
    out = []
    for i in range(len(d_oi)):
        v = vol[i] if i < len(vol) else 0.0
        out.append(abs(d_oi[i]) / v if v > 0 else 0.0)
    return out


def oi_volume_returns(bars: List[Bar], oi: List[float], volume: List[float],
                      window: int = 48, threshold: float = 2.0, taker_bps: float = 4.5,
                      slippage_bps: float = 5.0, exec_lag: int = 1) -> List[float]:
    """Sur z(ratio ΔOI/vol) > seuil = accumulation passive : SUIVRE la direction de l'OI
    (ΔOI>0 → LONG, ΔOI<0 → SHORT). Momentum de positionnement. Fill i+exec_lag."""
    closes = [b.close for b in bars]
    n = len(closes)
    ratio = oi_vol_ratio(oi, volume)              # len ~ n-1
    d_oi = pct_change(oi)
    z = rolling_zscore(ratio, window)             # passé-only
    cost = (taker_bps + slippage_bps) / 1e4
    rets: List[float] = []
    for i in range(n - 1 - exec_lag):
        zi = z[i - 1] if 0 < i <= len(z) else 0.0  # ratio[i-1] décrit la barre i (ΔOI sur [i-1,i])
        doi = d_oi[i - 1] if 0 < i <= len(d_oi) else 0.0
        if abs(zi) < threshold or doi == 0.0:
            rets.append(0.0)
            continue
        direction = 1.0 if doi > 0 else -1.0       # suivre l'OI (accumulation passive)
        p0 = closes[i + exec_lag]
        p1 = closes[i + 1 + exec_lag]
        raw = direction * (p1 - p0) / p0 if p0 else 0.0
        rets.append(raw - cost)
    return rets
