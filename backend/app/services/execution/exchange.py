"""ExchangeClient — le SEUL module qui distingue paper/live via dry_run flag.

Délègue la simulation de fill (walk-the-book + freshness check + fee) à
`paper.fill_simulator.FillSimulator` — testable séparément avec books mockés.

Architecture :
  1. Get l2Book mainnet RÉEL                  (paper ET live, via SDK Info)
  2. FillSimulator.simulate(book, side, size) (paper ET live)
  3. Latence simulée                           (paper uniquement)
  4. ↓ SEUL POINT DE DIVERGENCE ↓
     dry_run=True  → Fill PAPER au VWAP simulé
     dry_run=False → Exchange.market_open() → vrai fill HL
"""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any

try:
    from hyperliquid.exchange import Exchange  # noqa: F401
    from hyperliquid.info import Info
except ImportError:
    Exchange = None  # type: ignore
    Info = None  # type: ignore

from app.services.paper.fill_simulator import FillSimulator


# =====================================================================
# Types
# =====================================================================

@dataclass(frozen=True)
class Fill:
    success: bool
    side: str
    coin: str
    size: float
    avg_price: float
    notional_usd: float
    fee_usd: float
    ts_ms: int
    fill_id: str
    dry_run: bool
    levels_walked: int
    leverage: float = 1.0
    error: str | None = None

    def __repr__(self) -> str:
        if self.success:
            tag = "PAPER" if self.dry_run else "LIVE"
            return (f"<Fill {tag} {self.coin} {self.side} "
                    f"sz={self.size} @ ${self.avg_price:.4f} "
                    f"notional=${self.notional_usd:.2f} "
                    f"lev={self.leverage:g}x fee=${self.fee_usd:.4f}>")
        return f"<Fill FAILED {self.coin} {self.side} err={self.error}>"


@dataclass
class LatencyModel:
    min_ms: int = 200
    max_ms: int = 800

    def sample_seconds(self) -> float:
        return random.uniform(self.min_ms, self.max_ms) / 1000.0


# =====================================================================
# ExchangeClient
# =====================================================================

class ExchangeClient:
    """Submission d'ordres avec dry_run flag.

    Paper : ExchangeClient(dry_run=True, info=info_client)
    Live  : ExchangeClient(dry_run=False, info=info_client, exchange=hl_exchange)
    """

    def __init__(
        self,
        dry_run: bool,
        info: Any,
        exchange: Any = None,
        latency_model: LatencyModel | None = None,
        fee_rate: float | None = None,
        max_book_age_s: float = 2.0,
    ) -> None:
        if not dry_run and exchange is None:
            raise ValueError(
                "Exchange client (SDK authentifié) requis quand dry_run=False")
        self.dry_run = dry_run
        self._info = info
        self._exchange = exchange
        self._latency = latency_model or LatencyModel()
        self._simulator = FillSimulator(
            fee_rate=fee_rate, max_book_age_s=max_book_age_s,
        )
        self._meta_cache: dict | None = None

    # --- meta (sz_decimals) ---

    def _meta(self) -> dict:
        if self._meta_cache is None:
            self._meta_cache = self._info.meta()
        return self._meta_cache or {}

    def _sz_decimals(self, coin: str) -> int:
        for u in self._meta().get("universe", []):
            if u.get("name") == coin:
                return int(u.get("szDecimals", 0))
        return 0

    def _round_size(self, coin: str, size: float) -> float:
        return round(size, self._sz_decimals(coin))

    # --- helpers ---

    def _fail(self, coin: str, side: str, size: float, error: str,
              levels_walked: int = 0, leverage: float = 1.0) -> Fill:
        return Fill(
            success=False, side=side, coin=coin, size=size,
            avg_price=0.0, notional_usd=0.0, fee_usd=0.0,
            ts_ms=int(time.time() * 1000), fill_id="",
            dry_run=self.dry_run, levels_walked=levels_walked,
            leverage=leverage, error=error,
        )

    # --- API publique ---

    async def submit_market_order(
        self,
        coin: str,
        side: str,                # "B" ou "A"
        size: float,              # base coin units
        leverage: float = 1.0,
        reduce_only: bool = False,
        slippage_tolerance: float = 0.01,  # 1% (live)
    ) -> Fill:
        """Submit market order. dry_run flag bascule paper/live au DERNIER point.

        Identique paper/live JUSQU'À la dernière ligne.
        """
        if not isinstance(coin, str) or not coin:
            return self._fail(coin or "", side, size, "coin invalide")
        if (side or "").upper() not in ("B", "A"):
            return self._fail(coin, side, size,
                              f"side invalide '{side}' (attendu 'B' ou 'A')")
        if size <= 0:
            return self._fail(coin, side, size, f"size invalide {size}")
        if leverage <= 0:
            return self._fail(coin, side, size, f"leverage invalide {leverage}")

        # 1. ROUND size
        size = self._round_size(coin, size)
        if size <= 0:
            return self._fail(coin, side, 0,
                              "size arrondie à 0 (sz_decimals trop strict)",
                              leverage=leverage)

        # 2. Latence simulée (paper). En live, latence réseau naturelle.
        if self.dry_run:
            await asyncio.sleep(self._latency.sample_seconds())

        # 3. l2Book snapshot — IDENTIQUE paper et live
        try:
            book = await asyncio.get_event_loop().run_in_executor(
                None, self._info.l2_snapshot, coin)
        except Exception as e:
            return self._fail(coin, side, size,
                              f"l2_snapshot error: {type(e).__name__}: {e}",
                              leverage=leverage)

        # 4. Simulator (freshness + VWAP walk + fee)
        sim = self._simulator.simulate(book, side, size)
        if not sim.success:
            return self._fail(coin, side, size, sim.error or "sim failed",
                              levels_walked=sim.levels_walked, leverage=leverage)

        ts_now = int(time.time() * 1000)

        # 5. ↓↓↓ DIVERGENCE PAPER / LIVE ↓↓↓
        if self.dry_run:
            return Fill(
                success=True, side=side, coin=coin, size=sim.filled_size,
                avg_price=sim.vwap, notional_usd=sim.notional_usd,
                fee_usd=sim.fee_usd, ts_ms=ts_now,
                fill_id=f"paper:{coin}:{ts_now}",
                dry_run=True, levels_walked=sim.levels_walked,
                leverage=leverage,
            )

        # LIVE : signe + submit via SDK Exchange
        is_buy = side.upper() == "B"
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._exchange.market_open(
                    coin, is_buy, size,
                    slippage=slippage_tolerance,
                    reduce_only=reduce_only,
                ),
            )
        except Exception as e:
            return self._fail(coin, side, size,
                              f"market_open exception: {type(e).__name__}: {e}",
                              levels_walked=sim.levels_walked, leverage=leverage)
        if not isinstance(result, dict) or result.get("status") != "ok":
            err_msg = str(result)[:200] if result else "no response"
            return self._fail(coin, side, size, f"order rejected: {err_msg}",
                              levels_walked=sim.levels_walked, leverage=leverage)
        try:
            statuses = result["response"]["data"]["statuses"]
            if statuses and "filled" in statuses[0]:
                f = statuses[0]["filled"]
                actual_avg = float(f["avgPx"])
                actual_size = float(f["totalSz"])
                actual_notional = actual_size * actual_avg
                actual_fee = actual_notional * self._simulator.fee_rate
                return Fill(
                    success=True, side=side, coin=coin, size=actual_size,
                    avg_price=actual_avg, notional_usd=actual_notional,
                    fee_usd=actual_fee, ts_ms=ts_now,
                    fill_id=str(statuses[0].get("oid", "")),
                    dry_run=False, levels_walked=sim.levels_walked,
                    leverage=leverage,
                )
            elif statuses and "resting" in statuses[0]:
                return self._fail(coin, side, size,
                                  "live: order resting (non fillé immédiat)",
                                  levels_walked=sim.levels_walked,
                                  leverage=leverage)
            return self._fail(coin, side, size,
                              f"live response inattendue: {statuses}",
                              levels_walked=sim.levels_walked, leverage=leverage)
        except (KeyError, IndexError, ValueError) as e:
            return self._fail(coin, side, size,
                              f"parse live response: {type(e).__name__}: {e}",
                              levels_walked=sim.levels_walked, leverage=leverage)
