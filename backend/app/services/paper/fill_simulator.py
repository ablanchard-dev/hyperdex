"""FillSimulator — simulation de fill PURE depuis un orderbook snapshot.

Extrait de ExchangeClient pour être :
- Testable séparément (tests unitaires avec books synthétiques).
- Réutilisable par paper ET live (pour comparer expected VWAP vs actual fill).

Pas d'I/O — purement computationnel. La fraîcheur du book est aussi
checkée ici (leçon a prior project : stale book → reject, jamais de bypass).
"""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class SimulatedFill:
    """Résultat d'une simulation de fill."""
    success: bool
    vwap: float
    filled_size: float
    levels_walked: int
    notional_usd: float
    fee_usd: float
    book_age_ms: int
    error: str | None = None


class FillSimulator:
    """Walk-the-book → VWAP + slippage. Pure computation."""

    DEFAULT_FEE_RATE = 0.00025  # HL taker baseline (0.025%)

    def __init__(self, fee_rate: float | None = None,
                 max_book_age_s: float = 2.0):
        self.fee_rate = fee_rate if fee_rate is not None else self.DEFAULT_FEE_RATE
        self.max_book_age_s = max_book_age_s

    def book_age_ms(self, book: dict) -> int:
        """Retourne age du book en ms (-1 si pas de timestamp)."""
        ts = int(book.get("time", 0))
        if ts == 0:
            return -1
        return int(time.time() * 1000) - ts

    def check_book_fresh(self, book: dict) -> str | None:
        """None si book frais. Sinon string d'erreur."""
        age = self.book_age_ms(book)
        if age < 0:
            return None  # pas de ts, on tolère
        if age > self.max_book_age_s * 1000:
            return f"book stale: {age}ms > {self.max_book_age_s*1000:.0f}ms"
        return None

    def compute_vwap(self, book: dict, side: str, target_size: float
                     ) -> tuple[float, float, int]:
        """Walk the appropriate side of the book.

        BUY  ("B") → walk asks (lowest first)
        SELL ("A") → walk bids (highest first)

        Returns (vwap, filled_size, levels_walked).
        filled_size < target_size si book trop mince (partial fill).
        """
        levels = book.get("levels") or [[], []]
        if len(levels) < 2:
            return 0.0, 0.0, 0
        bids, asks = levels[0], levels[1]
        side_up = (side or "").upper()
        if side_up == "B":
            side_book = asks
        elif side_up == "A":
            side_book = bids
        else:
            return 0.0, 0.0, 0
        if not side_book:
            return 0.0, 0.0, 0

        remaining = target_size
        total_cost = 0.0
        total_size = 0.0
        levels_walked = 0
        for level in side_book:
            try:
                px = float(level.get("px", 0))
                sz = float(level.get("sz", 0))
            except Exception:
                continue
            if px <= 0 or sz <= 0:
                continue
            take = min(remaining, sz)
            total_cost += take * px
            total_size += take
            remaining -= take
            levels_walked += 1
            if remaining <= 1e-12:
                break
        if total_size <= 0:
            return 0.0, 0.0, levels_walked
        return total_cost / total_size, total_size, levels_walked

    # A5 — depth guard thresholds
    MIN_BOOK_LEVELS = 2  # Reject si orderbook side a moins de 2 niveaux
    MAX_LEVELS_WALKED = 5  # Reject si on a dû walker >5 niveaux (slippage trop)

    def simulate(self, book: dict, side: str, target_size: float
                 ) -> SimulatedFill:
        """One-shot : check fresh + compute VWAP + apply fee. Returns SimulatedFill.

        A5 : ajoute depth guard pour éviter slippage > 0.5% systématique :
        - Reject si side_book a < MIN_BOOK_LEVELS niveaux (book trop mince)
        - Reject si levels_walked > MAX_LEVELS_WALKED (signal de faible liquidité)
        """
        err = self.check_book_fresh(book)
        if err:
            return SimulatedFill(
                success=False, vwap=0.0, filled_size=0.0, levels_walked=0,
                notional_usd=0.0, fee_usd=0.0,
                book_age_ms=self.book_age_ms(book), error=err,
            )
        # A5 : depth guard préliminaire (côté book mince)
        levels = book.get("levels") or [[], []]
        if len(levels) >= 2:
            side_up = (side or "").upper()
            side_book = levels[1] if side_up == "B" else (
                levels[0] if side_up == "A" else [])
            if side_book is not None and len(side_book) < self.MIN_BOOK_LEVELS:
                return SimulatedFill(
                    success=False, vwap=0.0, filled_size=0.0, levels_walked=0,
                    notional_usd=0.0, fee_usd=0.0,
                    book_age_ms=self.book_age_ms(book),
                    error=f"INSUFFICIENT_DEPTH (side has {len(side_book)} levels)",
                )
        vwap, filled, n_levels = self.compute_vwap(book, side, target_size)
        if vwap == 0.0 or filled == 0.0:
            return SimulatedFill(
                success=False, vwap=0.0, filled_size=0.0,
                levels_walked=n_levels, notional_usd=0.0, fee_usd=0.0,
                book_age_ms=self.book_age_ms(book),
                error="VWAP/size nul (book vide ou trop mince)",
            )
        # A5 : depth guard post-walk (si on a dû walker trop de niveaux)
        if n_levels > self.MAX_LEVELS_WALKED:
            return SimulatedFill(
                success=False, vwap=0.0, filled_size=0.0,
                levels_walked=n_levels, notional_usd=0.0, fee_usd=0.0,
                book_age_ms=self.book_age_ms(book),
                error=f"DEEP_WALK ({n_levels} levels = high slippage risk)",
            )
        notional = filled * vwap
        fee = notional * self.fee_rate
        return SimulatedFill(
            success=True, vwap=vwap, filled_size=filled, levels_walked=n_levels,
            notional_usd=notional, fee_usd=fee,
            book_age_ms=self.book_age_ms(book),
        )
