#!/usr/bin/env python3
"""Signal contrarian sur spike de liquidation horaire — adapté à la data agrégée.

La data Coinalyze est bucketée par heure (long-liq + short-liq sommés/1h), pas des
events ponctuels → le Hawkes ponctuel est inadapté (cf run_hawkes_cascade : β fuit au
plafond). À cette granularité, l'hypothèse correcte (recherche groundée : liquidations
= indicateur contrarian, snapback post-extrême) se teste en DISCRET : détecter un SPIKE
de liquidation nette via z-score rolling PASSÉ-ONLY, puis entrer CONTRE.

No-look-ahead : z-score à la barre i n'utilise que [i-window, i) ; fill à i+exec_lag.
Pur & testable. Le moteur Hawkes (hawkes.py) reste valide pour de la vraie data tick.
"""
import statistics
from typing import List

from adapter import Bar


def rolling_zscore(series: List[float], window: int) -> List[float]:
    """z-score de chaque point vs sa fenêtre PASSÉE [i-window, i). z[i]=0 si i<window
    ou variance nulle (pas d'historique → pas de signal)."""
    out = [0.0] * len(series)
    for i in range(window, len(series)):
        past = series[i - window:i]
        mu = statistics.mean(past)
        sd = statistics.pstdev(past)
        out[i] = (series[i] - mu) / sd if sd > 0 else 0.0
    return out


def liq_spike_returns(bars: List[Bar], net_liq: List[float], z_window: int = 24,
                      z_threshold: float = 2.0, taker_bps: float = 4.5,
                      slippage_bps: float = 5.0, exec_lag: int = 1) -> List[float]:
    """net_liq[i] = liquidation nette signée à la barre i (longs liquidés négatif =
    sell-off ; shorts liquidés positif). Sur un SPIKE (|z|>seuil), entrer CONTRE :
    spike de longs liquidés (z très négatif) → LONG le rebond. Fill à i+exec_lag."""
    closes = [b.close for b in bars]
    n = len(closes)
    z = rolling_zscore(net_liq, z_window)
    cost = (taker_bps + slippage_bps) / 1e4
    rets: List[float] = []
    for i in range(n - 1 - exec_lag):
        if abs(z[i]) < z_threshold:
            rets.append(0.0)
            continue
        # contrarian : net_liq négatif (longs liquidés) -> LONG (+1) ; positif -> SHORT
        direction = 1.0 if net_liq[i] < 0 else -1.0
        p0 = closes[i + exec_lag]
        p1 = closes[i + 1 + exec_lag]
        raw = direction * (p1 - p0) / p0 if p0 else 0.0
        rets.append(raw - cost)
    return rets


def net_liquidation_per_bar(events: List[dict], bar_ts_ms: List[int]) -> List[float]:
    """Agrège les events liq en pression nette signée par barre (réutilise la
    convention : long liquidé = −notional, short = +notional)."""
    import bisect
    net = [0.0] * len(bar_ts_ms)
    if len(bar_ts_ms) < 2:
        return net
    last = bar_ts_ms[-1]
    for e in events:
        ts = e["ts"]
        if ts < bar_ts_ms[0] or ts >= last:
            continue
        i = bisect.bisect_right(bar_ts_ms, ts) - 1
        if 0 <= i < len(bar_ts_ms) - 1:
            sign = -1.0 if e["liquidated_side"] == "long" else 1.0
            net[i] += sign * e["notional"]
    return net
