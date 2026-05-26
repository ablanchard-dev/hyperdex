"""CopySizer — sizing proportionnel des copies avec caps + min HL.

Phase A (2026-05-25) ajoute :
- A1 funding-sign gate : reject LONG sur funding > +0.01%/h (drag PnL constant)
- A2 drawdown circuit breaker : si tracker DD>10% → target × 0.5

Sources :
- Funding gate : standard perps quant practice (paie le funding side = drag)
- DD circuit : Kelly 1956 reducing dans drawdown pour preserve growth
"""
from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass
from typing import Any


@dataclass
class SizingDecision:
    accept: bool
    size: float          # base coin units
    notional_usd: float
    reason: str = ""


class CopySizer:
    """Décide la taille de notre copy + accept/reject."""

    HL_MIN_NOTIONAL = 10.0  # $10 strict HL

    # A1 : seuil funding hourly considéré "négatif pour LONG"
    # 0.01%/h = 0.24%/j = ~88%/an drag. Conservateur, on rejette si funding
    # ≥ ce seuil et qu'on copy un LONG (= on paierait l'autre side).
    # Pour SHORT on inverse : reject si funding <= -0.0001 (on paierait shorts).
    FUNDING_HOURLY_DRAG_THRESHOLD = 0.0001  # 0.01%/h

    # A2 : DD threshold pour circuit breaker
    DD_BREAKER_THRESHOLD = 0.10  # 10%
    DD_BREAKER_SIZE_MULT = 0.5   # /2 si DD breaker actif

    # Cache funding rates pour éviter spam API
    FUNDING_CACHE_TTL_S = 30.0

    def __init__(
        self,
        target_notional: float = 25.0,
        min_notional: float = 10.0,
        max_concurrent: int = 20,
        max_per_asset: int | dict[str, int] = 3,
        info: Any = None,
        tracker: Any = None,
    ) -> None:
        self.target = target_notional
        self.min_notional = max(min_notional, self.HL_MIN_NOTIONAL)
        self.max_concurrent = max_concurrent
        # max_per_asset peut être int (uniforme) ou dict {coin: int, "_default": int}.
        # Permet de relâcher le cap sur coins liquides (HYPE/ETH/BTC) où la cohorte
        # converge naturellement, tout en gardant la prudence sur alts.
        self.max_per_asset = max_per_asset
        self.info = info  # A1 — HL SDK Info pour funding rate
        self.tracker = tracker  # A2 — pour current_drawdown_pct()
        # Cache funding rates {coin: (rate_hourly, fetched_at_ts)}
        self._funding_cache: dict[str, tuple[float, float]] = {}

    def _max_per_asset_for(self, coin: str) -> int:
        """Retourne le cap par coin. Si dict, lookup coin sinon '_default'."""
        if isinstance(self.max_per_asset, dict):
            return self.max_per_asset.get(coin, self.max_per_asset.get("_default", 3))
        return int(self.max_per_asset)

    def _get_funding_rate_hourly(self, coin: str) -> float | None:
        """Retourne le funding rate hourly du coin, cached 30s.
        Positif = longs paient shorts. Négatif = shorts paient longs."""
        if self.info is None:
            return None
        now = time.time()
        cached = self._funding_cache.get(coin)
        if cached is not None:
            rate, ts = cached
            if now - ts < self.FUNDING_CACHE_TTL_S:
                return rate
        # Refresh via meta_and_asset_ctxs (renvoie funding pour tous coins en 1 call)
        try:
            meta, ctxs = self.info.meta_and_asset_ctxs()
            universe = (meta or {}).get("universe", []) or []
            for i, asset in enumerate(universe):
                name = asset.get("name")
                if name != coin:
                    continue
                if i >= len(ctxs):
                    continue
                ctx = ctxs[i] or {}
                # funding est typiquement renvoyé en taux par 1h (rate) ou par funding interval
                # Hyperliquid : "funding" field = hourly rate
                try:
                    rate = float(ctx.get("funding", 0.0))
                except (TypeError, ValueError):
                    rate = 0.0
                self._funding_cache[coin] = (rate, now)
                return rate
        except Exception:
            pass
        return None

    def decide(
        self,
        coin: str,
        current_price: float,
        active_total: int,
        active_per_asset: Counter[str],
        side_is_long: bool | None = None,
    ) -> SizingDecision:
        if current_price <= 0:
            return SizingDecision(False, 0, 0, "prix invalide")
        if active_total >= self.max_concurrent:
            return SizingDecision(
                False, 0, 0,
                f"cap concurrent ({active_total}/{self.max_concurrent})")
        cap_coin = self._max_per_asset_for(coin)
        if active_per_asset.get(coin, 0) >= cap_coin:
            return SizingDecision(
                False, 0, 0,
                f"cap {coin} ({active_per_asset[coin]}/{cap_coin})")

        # A1 funding-sign gate
        # Seulement si on a info ET on a la direction (side_is_long).
        # Évite paying funding side : LONG sur funding+ = on paie ; SHORT sur funding- = on paie.
        if self.info is not None and side_is_long is not None:
            funding_rate = self._get_funding_rate_hourly(coin)
            if funding_rate is not None:
                if side_is_long and funding_rate >= self.FUNDING_HOURLY_DRAG_THRESHOLD:
                    return SizingDecision(
                        False, 0, 0,
                        f"FUNDING_DRAG_LONG (rate={funding_rate*100:.4f}%/h)")
                if (not side_is_long) and funding_rate <= -self.FUNDING_HOURLY_DRAG_THRESHOLD:
                    return SizingDecision(
                        False, 0, 0,
                        f"FUNDING_DRAG_SHORT (rate={funding_rate*100:.4f}%/h)")

        # A2 drawdown circuit breaker
        effective_target = self.target
        if self.tracker is not None:
            try:
                dd_pct = self.tracker.current_drawdown_pct()
                if dd_pct >= self.DD_BREAKER_THRESHOLD:
                    effective_target = self.target * self.DD_BREAKER_SIZE_MULT
            except Exception:
                pass  # tracker error → use full target (fail open)

        size = effective_target / current_price
        notional = size * current_price
        if notional < self.min_notional:
            return SizingDecision(
                False, 0, 0,
                f"notional ${notional:.2f} < min ${self.min_notional}")
        return SizingDecision(True, size, notional, "")
