#!/usr/bin/env python3
"""recency.py — détection des new-listings (la vraie niche inefficiente HL).

Proxy d'âge de listing = (now - timestamp de la 1ère candle disponible). Un perp
récemment listé a un historique court → small money pas encore arbitré.
"""
from typing import List, Tuple

DAY_MS = 86_400_000


def listing_age_days(adapter, symbol: str, now_ms: int,
                     lookback_days: int = 365) -> float:
    """Âge en jours depuis la 1ère candle dispo (0 si aucune donnée)."""
    start = now_ms - lookback_days * DAY_MS
    bars = adapter.history(symbol, start, now_ms)
    if not bars:
        return 0.0
    return (now_ms - bars[0].ts) / DAY_MS


def new_listings(adapter, now_ms: int, max_age_days: int = 90,
                 lookback_days: int = 365) -> List[Tuple[str, float]]:
    """(symbol, age_days) pour les perps listés depuis ≤ max_age_days."""
    out = []
    for s in adapter.universe():
        age = listing_age_days(adapter, s, now_ms, lookback_days)
        if 0 < age <= max_age_days:
            out.append((s, age))
    return out
