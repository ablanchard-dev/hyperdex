#!/usr/bin/env python3
"""VenueAdapter — interface d'exécution/données venue-agnostic de l'edge-factory.

Le cœur (recherche + CRITIC) ne connaît PAS la venue. Chaque niche est une impl.
concrète : Polymarket-météo (infra Polyoracle), HL small-cap (infra HyperDex),
futures (costs IBKR Dexterio)… Ajouter une venue = écrire un adapter, PAS toucher
le cœur. Contrat minimal Phase 1 : universe / history / fees / benchmark.
Exécution live (place_order/positions) = ajoutée en phase exécution.
"""
from abc import ABC, abstractmethod
from typing import List, NamedTuple


class Bar(NamedTuple):
    ts: int
    close: float
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    volume: float = 0.0


class Fees(NamedTuple):
    taker_bps: float
    maker_bps: float  # négatif = rebate


class VenueAdapter(ABC):
    """Contrat venue-agnostic. Toute niche/venue l'implémente."""

    name: str = "abstract"

    @abstractmethod
    def universe(self) -> List[str]:
        """Symboles tradeables (pour la chasse : new-listings, small-caps…)."""

    @abstractmethod
    def history(self, symbol: str, start: int, end: int) -> List[Bar]:
        """Série historique (prix/outcome) pour le backtest."""

    @abstractmethod
    def fees(self, symbol: str) -> Fees:
        """Coûts réels (taker/maker bps) pour un backtest net de coût."""

    @abstractmethod
    def benchmark(self, start: int, end: int) -> List[Bar]:
        """Série du benchmark de la venue (ex: BTC) pour le test beta-neutral."""


def returns_from_bars(bars: List[Bar]) -> List[float]:
    """Returns pct entre closes consécutifs. [] si <2 barres ou prix nul."""
    out = []
    for i in range(1, len(bars)):
        p0, p1 = bars[i - 1].close, bars[i].close
        out.append((p1 - p0) / p0 if p0 else 0.0)
    return out
