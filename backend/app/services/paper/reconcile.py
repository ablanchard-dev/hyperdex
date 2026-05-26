"""Reconciliation périodique des positions trackées vs réalité HL.

Problème observé 2026-05-25 : sur 10 positions tracked "open", 4 confirmées
fermées côté wallet via API REST mais le WS n'a JAMAIS reçu les close events
(uniquement l'OPEN au démarrage). Cause : HL peut perdre silencieusement des
subscriptions individuelles au sein d'une Info instance, sans casser la
connexion entière. Le per-shard reconnect ne détecte pas ça (les autres
wallets du shard émettent encore).

Solution : toutes les RECONCILE_INTERVAL_S secondes, fetch user_state pour
chaque wallet avec position trackée. Si la position n'existe plus côté API
ou que szi a changé de signe → close manuel avec mark price actuel.

Coût API : N positions × 2 weight = N×2 weight par cycle. À 20 positions
max et cycle 5min = 8 weight/min. Budget HL 1200/min = négligeable.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from app.services.paper.pnl_tracker import PnLTracker


class PositionReconciler:
    """Loop async qui reconcile tracker vs API HL."""

    RECONCILE_INTERVAL_S = 300.0  # 5 min
    POSITION_TOLERANCE_SZ = 1e-8

    def __init__(self, tracker: PnLTracker, info: Any, verbose: bool = True):
        self.tracker = tracker
        self.info = info
        self.verbose = verbose
        self._stop = asyncio.Event()
        self.stats = dict(
            reconcile_cycles=0,
            wallets_checked=0,
            phantom_closes=0,
            errors=0,
        )

    def _log(self, *a):
        if self.verbose:
            print("[RECONCILE]", *a, flush=True)

    async def _fetch_wallet_positions(self, addr: str) -> dict[str, float] | None:
        """Retourne {coin: szi_signed} ou None si erreur.
        szi positif = long, négatif = short, 0 = no position."""
        loop = asyncio.get_event_loop()
        try:
            state = await loop.run_in_executor(
                None, self.info.user_state, addr)
        except Exception as e:
            self._log(f"user_state fail {addr[:14]}: {type(e).__name__}: {e}")
            return None
        positions = {}
        for ap in (state or {}).get("assetPositions", []):
            p = ap.get("position", {})
            coin = p.get("coin")
            szi = p.get("szi")
            if coin and szi is not None:
                try:
                    positions[coin] = float(szi)
                except Exception:
                    continue
        return positions

    async def _mid_price(self, coin: str) -> float | None:
        """Best-effort mid price via l2_snapshot."""
        loop = asyncio.get_event_loop()
        try:
            book = await loop.run_in_executor(
                None, self.info.l2_snapshot, coin)
        except Exception:
            return None
        levels = book.get("levels") or [[], []]
        if len(levels) < 2:
            return None
        bids, asks = levels[0], levels[1]
        if not bids or not asks:
            return None
        try:
            best_bid = float(bids[0].get("px", 0))
            best_ask = float(asks[0].get("px", 0))
            if best_bid > 0 and best_ask > 0:
                return (best_bid + best_ask) / 2.0
        except Exception:
            pass
        return None

    async def _reconcile_one(self, trader: str, coin: str, is_long: bool):
        """Vérifie 1 position. Si wallet n'a plus la position côté API → close."""
        api_pos = await self._fetch_wallet_positions(trader)
        if api_pos is None:
            self.stats["errors"] += 1
            return
        self.stats["wallets_checked"] += 1
        szi = api_pos.get(coin, 0.0)
        wallet_still_long = szi > self.POSITION_TOLERANCE_SZ
        wallet_still_short = szi < -self.POSITION_TOLERANCE_SZ
        wallet_still_has_same_side = (is_long and wallet_still_long) or \
                                      (not is_long and wallet_still_short)
        if wallet_still_has_same_side:
            return  # position toujours ouverte côté wallet, on garde
        # Wallet n'a plus cette position → close phantom
        mid = await self._mid_price(coin)
        if mid is None or mid <= 0:
            self._log(f"PHANTOM_CLOSE {trader[:14]} {coin} "
                      f"{'LONG' if is_long else 'SHORT'} : no mid price, skip cycle")
            return
        # close au mid + simul fee 0.025% taker
        # Le tracker.close marque le PnL avec ce prix.
        ts_ms = int(time.time() * 1000)
        pos = self.tracker.get(trader, coin, is_long)
        if pos is None:
            return
        fee_estimate = pos.size * mid * 0.00025
        res = self.tracker.close(
            trader=trader, coin=coin, is_long=is_long,
            exit_price=mid, exit_ts_ms=ts_ms,
            exit_fee_usd=fee_estimate,
            exit_fill_id=f"phantom_close:{ts_ms}",
        )
        if res is not None:
            net_pnl, gross_pnl, total_fees = res
            self.stats["phantom_closes"] += 1
            tag = "WIN " if net_pnl > 0 else "LOSS"
            self._log(f"PHANTOM_CLOSE {tag} {trader[:14]} {coin} "
                      f"{'L' if is_long else 'S'} mid=${mid:.4f} "
                      f"net=${net_pnl:+.2f} (total=${self.tracker.total_pnl:+.2f})")

    async def run(self):
        self._log(f"started, interval={self.RECONCILE_INTERVAL_S:.0f}s")
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self.RECONCILE_INTERVAL_S)
                break  # stop signal
            except asyncio.TimeoutError:
                pass
            # cycle reconciliation
            self.stats["reconcile_cycles"] += 1
            open_positions = dict(self.tracker.open_positions)
            if not open_positions:
                continue
            self._log(f"cycle {self.stats['reconcile_cycles']} : "
                      f"check {len(open_positions)} positions")
            for key in list(open_positions.keys()):
                trader, coin, is_long = key
                try:
                    await self._reconcile_one(trader, coin, is_long)
                except Exception as e:
                    print(f"[RECONCILE err] {trader[:14]} {coin} "
                          f"{type(e).__name__}: {e}", flush=True)
                    self.stats["errors"] += 1
                # rate-limit-friendly : 100ms entre wallets
                await asyncio.sleep(0.1)
            self._log(f"cycle done. stats: {self.stats}")

    def stop(self):
        self._stop.set()
