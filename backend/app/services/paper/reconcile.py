"""Reconciliation périodique des positions trackées vs réalité HL.

Problème observé 2026-05-25 : sur 10 positions tracked "open", 4 confirmées
fermées côté wallet via API REST mais le WS n'a JAMAIS reçu les close events
(uniquement l'OPEN au démarrage). Cause : HL peut perdre silencieusement des
subscriptions individuelles au sein d'une Info instance, sans casser la
connexion entière. Le per-shard reconnect ne détecte pas ça (les autres
wallets du shard émettent encore).

Solution : toutes les RECONCILE_INTERVAL_S secondes, fetch user_state pour
chaque wallet avec position trackée. Si la position n'existe plus côté API
ou que szi a changé de signe → close manuel au VWAP du carnet actuel (côté sortie).

Coût API : N positions × 2 weight = N×2 weight par cycle. À 20 positions
max et cycle 5min = 8 weight/min. Budget HL 1200/min = négligeable.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from app.services.paper.fill_simulator import FillSimulator
from app.services.paper.pnl_tracker import PnLTracker


class PositionReconciler:
    """Loop async qui reconcile tracker vs API HL."""

    RECONCILE_INTERVAL_S = 300.0  # 5 min
    LIQ_WATCH_INTERVAL_S = 5.0
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
            liquidations=0,
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

    async def _book(self, coin: str) -> dict | None:
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(None, self.info.l2_snapshot, coin)
        except Exception:
            return None

    @staticmethod
    def _exit_vwap(book: dict | None, is_long: bool, size: float) -> float | None:
        """VWAP de sortie en marchant le carnet (None si carnet vide)."""
        vwap, filled, _ = FillSimulator().compute_vwap(book or {}, "A" if is_long else "B", size)
        return vwap if filled > 0 else None

    def _liquidate_if_crossed(self, trader: str, coin: str, is_long: bool, pos: Any,
                              book: dict | None) -> bool:
        """Ferme au prix de liquidation si le meilleur prix de sortie l'a franchi.

        Avant, liquidation_price n'etait que logue : une copie a 5x traversait en paper un
        mouvement qui la liquide en live, tant que le trader copie (moins leve) tenait.
        En continu, c'est run_liquidation_watch (mids toutes les 5 s) qui attrape les meches ;
        ce controle au carnet reste le filet du cycle de 5 min.
        """
        levels = (book or {}).get("levels") or [[], []]
        side = levels[0] if is_long else (levels[1] if len(levels) > 1 else [])
        if not side:
            return False
        try:
            best = float(side[0].get("px", 0))
        except Exception:
            return False
        return self._liquidate_at(trader, coin, is_long, pos, best)

    def _liquidate_at(self, trader: str, coin: str, is_long: bool, pos: Any, best: float) -> bool:
        liq = pos.liquidation_price
        if not best > 0 or not (best <= liq if is_long else best >= liq):
            return False
        ts_ms = int(time.time() * 1000)
        res = self.tracker.close(
            trader=trader, coin=coin, is_long=is_long,
            exit_price=liq, exit_ts_ms=ts_ms,
            exit_fee_usd=pos.size * liq * FillSimulator.DEFAULT_FEE_RATE,
            exit_fill_id=f"liquidation:{ts_ms}",
        )
        if res is not None:
            self.stats["liquidations"] += 1
            self._log(f"LIQUIDATION {trader[:14]} {coin} {'L' if is_long else 'S'} "
                      f"liq=${liq:.4f} best=${best:.4f} net=${res[0]:+.2f}")
        return True

    async def _reconcile_one(self, trader: str, coin: str, is_long: bool):
        """Vérifie 1 position : liquidation d'abord, puis wallet copié qui n'a plus la position."""
        pos = self.tracker.get(trader, coin, is_long)
        if pos is None:
            return
        book = await self._book(coin)
        if self._liquidate_if_crossed(trader, coin, is_long, pos, book):
            return
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
        # Sortie en marchant le carnet comme tout fill paper (long => vend dans les bids,
        # short => achete dans les asks), frais taker du simulateur. Le mid ignorait spread
        # et profondeur : chaque phantom close gonflait le PnL. compute_vwap et pas
        # simulate() : une position fantome doit sortir meme sur un carnet mince.
        exit_price = self._exit_vwap(book, is_long, pos.size)
        if exit_price is None or exit_price <= 0:
            self._log(f"PHANTOM_CLOSE {trader[:14]} {coin} "
                      f"{'LONG' if is_long else 'SHORT'} : no book, skip cycle")
            return
        ts_ms = int(time.time() * 1000)
        fee_estimate = pos.size * exit_price * FillSimulator.DEFAULT_FEE_RATE
        res = self.tracker.close(
            trader=trader, coin=coin, is_long=is_long,
            exit_price=exit_price, exit_ts_ms=ts_ms,
            exit_fee_usd=fee_estimate,
            exit_fill_id=f"phantom_close:{ts_ms}",
        )
        if res is not None:
            net_pnl, gross_pnl, total_fees = res
            self.stats["phantom_closes"] += 1
            tag = "WIN " if net_pnl > 0 else "LOSS"
            self._log(f"PHANTOM_CLOSE {tag} {trader[:14]} {coin} "
                      f"{'L' if is_long else 'S'} exit=${exit_price:.4f} "
                      f"net=${net_pnl:+.2f} (total=${self.tracker.total_pnl:+.2f})")

    def check_liquidations(self, mids: dict) -> int:
        """Liquide toute position dont le mid a franchi le prix de liquidation. Rend le nombre."""
        n = 0
        for (trader, coin, is_long), pos in list(self.tracker.open_positions.items()):
            try:
                px = float(mids[coin])
            except (KeyError, TypeError, ValueError):
                continue
            if self._liquidate_at(trader, coin, is_long, pos, px):
                n += 1
        return n

    async def run_liquidation_watch(self):
        """HL liquide sur le mark : all_mids (1 appel, poids 2, toutes les coins) toutes les
        LIQ_WATCH_INTERVAL_S. Avant, une meche entre deux cycles de 5 min ne liquidait rien.
        ponytail: mid ~ mark ; flux WS allMids si 5 s laisse encore passer des meches."""
        loop = asyncio.get_event_loop()
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.LIQ_WATCH_INTERVAL_S)
                break
            except asyncio.TimeoutError:
                pass
            if not self.tracker.open_positions:
                continue
            try:
                mids = await loop.run_in_executor(None, self.info.all_mids)
                self.check_liquidations(mids or {})
            except Exception as e:
                self.stats["errors"] += 1
                self._log(f"liquidation watch: {type(e).__name__}: {e}")

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
