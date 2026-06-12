"""CopyOrchestrator — bridge WS user_fills ↔ ExchangeClient.

Logique :
- Trader OPEN long/short  → mirror OPEN via ExchangeClient (dry_run=True paper)
- Trader CLOSE long/short → mirror CLOSE (reduce_only) si on a la position
- Coins HIP-4 / spot non perps → skip (xyz:* / @* / hyna:* / cash:*)
- Anti-HFT mute : trader dont avg hold_ms < HFT_MUTE_AVG_MS sur les
  HFT_MUTE_OBS_WINDOW derniers closes → blacklisté (skip futurs opens).
"""
from __future__ import annotations

import json
import os
import statistics
import time
from collections import deque
from pathlib import Path
from typing import Any

from app.services.copy.sizer import CopySizer
from app.services.execution import ExchangeClient
from app.services.paper.pnl_tracker import PnLTracker
from app.services.paper.position import PaperPosition
from app.services.paper.wallet_perf import compute_hold_ms_logical


HFT_MUTE_OBS_WINDOW = 5         # observations min avant de pouvoir muter
HFT_MUTE_MEDIAN_MS = 30_000     # median hold < 30s sur fenêtre → HFT non copiable
HFT_ROLLING_MAXLEN = 20         # taille de la deque par trader

# A4 fix 2026-05-27 : on bufferise les fills WS bruts per-trader pour
# recompute hold_ms logique (entry→exit) au lieu de delta inter-fills
# atomiques (qui mesure ~870 ms = la split orderbook d'1 ordre marché).
# Env `WALLET_PERF_LEGACY_HOLD=true` → conserve l'ancien comportement.
RAW_FILLS_BUFFER_MAXLEN = 500


# Ship 2026-05-27 post méta-audit : coins perdants empiriquement sur 102 closes.
# PURR -$2.84 (n=2, WR 0%, low lev maint margin 16.67% = liq tighter)
# STABLE -$1.45 (n=1, WR 0%, low lev similaire)
# ZEC -$1.22 (n=11, WR 55% mais sum négatif)
# Total = -$5.51 sur le run = 34% du PnL brut perdu.
BLOCKED_COINS = {"PURR", "STABLE", "ZEC"}


def is_skipped_coin(coin: str) -> bool:
    if not coin:
        return True
    if coin in BLOCKED_COINS:
        return True
    c = coin.lower()
    return (c.startswith("xyz:") or c.startswith("@") or
            c.startswith("hyna:") or c.startswith("cash:"))


class CopyOrchestrator:

    def __init__(
        self,
        exchange: ExchangeClient,
        sizer: CopySizer,
        tracker: PnLTracker,
        default_leverage: float = 5.0,
        verbose: bool = True,
        muted_path: Path | None = None,
        wallet_perf: Any = None,
        maint_margin_map: dict[str, float] | None = None,
    ) -> None:
        self.exchange = exchange
        self.sizer = sizer
        self.tracker = tracker
        self.default_leverage = default_leverage
        self.verbose = verbose
        self.wallet_perf = wallet_perf  # A4 — WalletPerformanceTracker
        # Fix paper=live parity 2026-05-27 : maint_margin per-coin = (1/maxLev) / 2
        # Source HL : https://hyperliquid.gitbook.io/hyperliquid-docs/trading/liquidations
        # Ex BTC maxLev=40 → maint=1.25% / PURR maxLev=3 → maint=16.67%
        # Avant ce fix : tous les coins utilisaient 5% par défaut → liq_price faux
        # sur low-lev coins (PURR/VVV/STABLE optimiste 11pts) et high-lev coins
        # (BTC/ETH pessimiste 3.75pts).
        self.maint_margin_map = maint_margin_map or {}
        self.stats: dict[str, int] = dict(
            opens_attempted=0, opens_done=0,
            opens_skipped_coin=0, opens_rejected_sizer=0,
            opens_rejected_fill=0, opens_idempotent_dup=0,
            opens_skipped_muted=0,
            closes_attempted=0, closes_done=0,
            closes_no_position=0, closes_rejected_fill=0,
            liquidations_detected=0,
            flips_handled=0,
            hft_mutes=0,
            wallet_perf_mutes=0,
        )
        self._trader_holds: dict[str, deque[int]] = {}
        # A4 fix : buffer fills WS bruts per-trader pour recompute hold logique.
        # Clé = trader.lower() ; valeur = deque de fill dicts.
        self._trader_raw_fills: dict[str, deque[dict[str, Any]]] = {}
        self._legacy_hold_mode = os.environ.get(
            "WALLET_PERF_LEGACY_HOLD", "false"
        ).strip().lower() in ("1", "true", "yes")
        self._muted: dict[str, dict] = {}
        self._muted_path = muted_path
        if muted_path and muted_path.exists():
            try:
                self._muted = json.loads(muted_path.read_text())
                if self.verbose:
                    self._log(f"MUTED loaded {len(self._muted)} from {muted_path}")
            except Exception as e:
                self._log(f"MUTED load fail {type(e).__name__}: {e}")

    def _log(self, *a):
        if self.verbose:
            print("[ORCH]", *a, flush=True)

    async def on_trader_fill(self, trader_addr: str, fill: dict):
        try:
            coin = fill.get("coin", "")
            side = fill.get("side", "")
            dir_str = (fill.get("dir") or "").lower()
            try:
                price = float(fill.get("px", 0))
                size = float(fill.get("sz", 0))
            except Exception:
                return
            if not coin or not side or price <= 0 or size <= 0:
                return
            # A4 fix : buffer raw fill (avant skip_coin) pour hold logique.
            # On bufferise même les coins skipped — la médiane porte sur tous
            # les fills observés du trader (signal HFT global, pas per-coin).
            if not self._legacy_hold_mode:
                key = trader_addr.lower()
                dq = self._trader_raw_fills.setdefault(
                    key, deque(maxlen=RAW_FILLS_BUFFER_MAXLEN))
                dq.append(fill)
            if is_skipped_coin(coin):
                if "open" in dir_str:
                    self.stats["opens_skipped_coin"] += 1
                return

            # detect open / close / liquidation / flip
            is_liq = ("liquidat" in dir_str) or ("adl" in dir_str)
            is_flip = ">" in dir_str  # ex: "long > short" = close long + open short
            is_open = "open" in dir_str and not is_liq and not is_flip
            is_close = ("close" in dir_str or is_liq) and not is_flip
            side_is_long = "long" in dir_str  # ambigu pour flip — résolu plus bas

            if is_liq:
                self.stats["liquidations_detected"] += 1

            if is_flip:
                # "long > short" : trader était LONG, passe SHORT
                # "short > long" : trader était SHORT, passe LONG
                # côté HL "side" du fill = direction du NOUVEAU côté (B=buy=long, A=sell=short)
                # = on close l'ancien (opposite) + on open le nouveau
                was_long = "long >" in dir_str  # ce qui se ferme
                new_is_long = "> long" in dir_str
                await self._mirror_close(trader_addr, coin, was_long, price)
                await self._mirror_open(
                    trader_addr, coin, side, new_is_long, price)
                self.stats.setdefault("flips_handled", 0)
                self.stats["flips_handled"] += 1
            elif is_open:
                await self._mirror_open(
                    trader_addr, coin, side, side_is_long, price)
            elif is_close:
                await self._mirror_close(trader_addr, coin, side_is_long, price)
            # else : ni open/close/flip — ignore (TWAP segments intra, etc.)
        except Exception as e:
            print(f"[ORCH err] {trader_addr[:14]} {type(e).__name__}: {e}",
                  flush=True)

    async def _mirror_open(self, trader: str, coin: str, side: str,
                            side_is_long: bool, price: float):
        self.stats["opens_attempted"] += 1

        # anti-HFT mute : skip si trader blacklisté
        if trader.lower() in self._muted:
            self.stats["opens_skipped_muted"] += 1
            return

        # idempotence : déjà open pour (trader,coin,side) ?
        if self.tracker.has_open(trader, coin, side_is_long):
            self.stats["opens_idempotent_dup"] += 1
            return

        # sizer (A1 : pass side_is_long pour funding gate)
        decision = self.sizer.decide(
            coin=coin, current_price=price,
            active_total=self.tracker.active_total(),
            active_per_asset=self.tracker.active_per_asset(),
            side_is_long=side_is_long,
        )
        if not decision.accept:
            self.stats["opens_rejected_sizer"] += 1
            self._log(f"REJECT-SIZER {trader[:10]} {coin} side={side} "
                      f"reason={decision.reason}")
            return

        # exchange (dry_run=True → paper)
        fill_result = await self.exchange.submit_market_order(
            coin=coin, side=side, size=decision.size,
            leverage=self.default_leverage, reduce_only=False,
        )
        if not fill_result.success:
            self.stats["opens_rejected_fill"] += 1
            self._log(f"REJECT-FILL  {trader[:10]} {coin} {side} "
                      f"err={fill_result.error}")
            return

        # build position avec maint_margin per-coin (fix paper=live 2026-05-27)
        maint = self.maint_margin_map.get(coin, 0.05)  # 5% fallback safe
        position = PaperPosition(
            trader=trader.lower(), coin=coin, is_long=side_is_long,
            size=fill_result.size, entry_price=fill_result.avg_price,
            leverage=fill_result.leverage, open_ts_ms=fill_result.ts_ms,
            open_fee_usd=fill_result.fee_usd, open_fill_id=fill_result.fill_id,
            maint_margin_pct=maint,
        )
        opened = self.tracker.open(position)
        if opened:
            self.stats["opens_done"] += 1
            # A3 : log liq distance % du prix entry. Warn si trop tight (<12%).
            # Seuil 12% car lev=5 donne liq_dist nominale ~15% → warn à 12%
            # signale vraiment un cas serré (volatile coin + lev>5 → tight).
            entry = fill_result.avg_price
            liq = position.liquidation_price
            liq_pct = abs(entry - liq) / entry * 100 if entry > 0 else 0.0
            liq_warn = " ⚠️LIQ_TIGHT" if liq_pct < 12.0 else ""
            self._log(f"OPEN  {trader[:10]} {coin} "
                      f"{'LONG' if side_is_long else 'SHORT'} "
                      f"sz={fill_result.size:.6f} @${entry:.4f} "
                      f"lev={fill_result.leverage:g}x "
                      f"notional=${fill_result.notional_usd:.2f} "
                      f"liq=${liq:.4f} liq_dist={liq_pct:.1f}%{liq_warn} "
                      f"(active={self.tracker.active_total()})")

    async def _mirror_close(self, trader: str, coin: str, was_long: bool,
                             price: float):
        self.stats["closes_attempted"] += 1
        pos = self.tracker.get(trader, coin, was_long)
        if pos is None:
            self.stats["closes_no_position"] += 1
            return
        close_side = "A" if was_long else "B"

        fill_result = await self.exchange.submit_market_order(
            coin=coin, side=close_side, size=pos.size,
            leverage=pos.leverage, reduce_only=True,
        )
        if not fill_result.success:
            self.stats["closes_rejected_fill"] += 1
            self._log(f"CLOSE-FAIL {trader[:10]} {coin} err={fill_result.error}")
            return

        res = self.tracker.close(
            trader=trader, coin=coin, is_long=was_long,
            exit_price=fill_result.avg_price,
            exit_ts_ms=fill_result.ts_ms,
            exit_fee_usd=fill_result.fee_usd,
            exit_fill_id=fill_result.fill_id,
        )
        if res is not None:
            net_pnl, gross_pnl, total_fees = res
            self.stats["closes_done"] += 1
            hold_ms = fill_result.ts_ms - pos.open_ts_ms
            self._observe_hold(trader, hold_ms)
            tag = "WIN " if net_pnl > 0 else "LOSS"
            self._log(f"CLOSE {tag} {trader[:10]} {coin} "
                      f"{'L' if was_long else 'S'} "
                      f"entry=${pos.entry_price:.4f} exit=${fill_result.avg_price:.4f} "
                      f"funding=${pos.funding_accrued_usd:+.4f} "
                      f"net=${net_pnl:+.2f} "
                      f"(total=${self.tracker.total_pnl:+.2f})")
            # A4 : update per-wallet perf + check auto-mute négatif
            if self.wallet_perf is not None:
                self.wallet_perf.update(trader, net_pnl)
                should_mute, mute_reason = self.wallet_perf.should_auto_mute(trader)
                if should_mute and trader.lower() not in self._muted:
                    self._mute_trader(trader.lower(), 0, 0, reason=mute_reason)
                    self.stats["wallet_perf_mutes"] += 1
                # Persist tous les 10 closes pour ne pas spam I/O
                if self.tracker.n_closes % 10 == 0:
                    self.wallet_perf.persist()

    # ---- anti-HFT runtime ----

    def _observe_hold(self, trader: str, hold_ms: int):
        """Observe un hold_ms post-close.

        - Legacy mode : append `hold_ms` brut, check mute si médiane <30s.
          Problème : le delta `exit_ts_ms - open_ts_ms` du paper-tracker se
          réinitialise à chaque fill atomique d'un ordre marché HL splittant
          l'orderbook (cf header wallet_perf.py).
        - Default mode (`WALLET_PERF_LEGACY_HOLD=false`) : recompute la médiane
          via `compute_hold_ms_logical()` sur le buffer fills WS bruts, qui
          agrège les fragments d'un même ordre via passage flat→non-flat.
        """
        key = trader.lower()
        if key in self._muted:
            return
        dq = self._trader_holds.setdefault(
            key, deque(maxlen=HFT_ROLLING_MAXLEN))
        dq.append(hold_ms)
        if self._legacy_hold_mode:
            if len(dq) < HFT_MUTE_OBS_WINDOW:
                return
            med = statistics.median(dq)
            if med < HFT_MUTE_MEDIAN_MS:
                self._mute_trader(key, med, len(dq), reason="hft_runtime")
            return
        # New mode : utilise fills bruts pour calcul logique.
        raw_fills = list(self._trader_raw_fills.get(key, ()))
        closures = compute_hold_ms_logical(raw_fills)
        if len(closures) < HFT_MUTE_OBS_WINDOW:
            return
        holds = sorted(c["hold_ms"] for c in closures)
        med = holds[len(holds) // 2]
        if med < HFT_MUTE_MEDIAN_MS:
            self._mute_trader(key, med, len(closures),
                              reason="hft_runtime_logical")

    def _mute_trader(self, trader: str, median_hold_ms: float, n_obs: int,
                      reason: str):
        self._muted[trader] = dict(
            reason=reason, median_hold_ms=int(median_hold_ms),
            n_observations=n_obs,
            muted_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        self.stats["hft_mutes"] += 1
        self._log(f"MUTE  {trader[:14]} reason={reason} "
                  f"median_hold={int(median_hold_ms)}ms obs={n_obs} "
                  f"(total_muted={len(self._muted)})")
        self._persist_muted()

    def _persist_muted(self):
        if self._muted_path is None:
            return
        try:
            self._muted_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._muted_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._muted, indent=2))
            tmp.replace(self._muted_path)
        except Exception as e:
            self._log(f"MUTED persist fail {type(e).__name__}: {e}")

    def bootstrap_holds_from_jsonl(self, log_path: Path):
        """Bootstrap depuis le positions.jsonl : pré-remplit les rolling deques
        + déclenche mute pour les wallets HFT déjà observés. Idempotent.

        A4 fix : si `WALLET_PERF_LEGACY_HOLD=false` (défaut), on SKIP le replay
        des hold_ms du JSONL car ces valeurs ont été calculées avec le bug
        delta-inter-fills-atomiques (donc ~870 ms pour les scalpers HYPE).
        Le hold logique sera reconstruit live depuis les fills WS bruts.
        """
        if not self._legacy_hold_mode:
            self._log("BOOTSTRAP hold_logical=ON → skip replay JSONL holds "
                      "(legacy values buggy, will rebuild from WS fills)")
            return
        if not log_path.exists():
            return
        n_close = 0
        with open(log_path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if ev.get("event") != "close":
                    continue
                trader = ev.get("trader", "").lower()
                hold_ms = ev.get("hold_ms")
                if not trader or hold_ms is None:
                    continue
                if trader in self._muted:
                    continue
                dq = self._trader_holds.setdefault(
                    trader, deque(maxlen=HFT_ROLLING_MAXLEN))
                dq.append(int(hold_ms))
                n_close += 1
        # post-pass : check mute pour chaque trader avec assez d'obs
        for trader, dq in list(self._trader_holds.items()):
            if trader in self._muted:
                continue
            if len(dq) < HFT_MUTE_OBS_WINDOW:
                continue
            med = statistics.median(dq)
            if med < HFT_MUTE_MEDIAN_MS:
                self._mute_trader(
                    trader, med, len(dq), reason="hft_bootstrap")
        self._log(f"BOOTSTRAP closes_replayed={n_close} "
                  f"traders_tracked={len(self._trader_holds)} "
                  f"muted_after_bootstrap={len(self._muted)}")
