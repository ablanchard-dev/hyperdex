"""PnLTracker — state des paper positions + JSONL log append-only.

Utilise PaperPosition (modèle perp riche). JSONL append-only (leçon polyoracle :
pas de re-dump monolithique → pas d'OOM).
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from app.services.paper.position import PaperPosition


class PnLTracker:
    """State + log JSONL des positions paper."""

    def __init__(self, log_path: Path):
        self.log_path = log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.open_positions: dict[tuple, PaperPosition] = {}
        self.total_pnl: float = 0.0
        self.total_funding_paid: float = 0.0
        self.n_opens: int = 0
        self.n_closes: int = 0
        self.n_wins: int = 0
        self.n_losses: int = 0
        self.per_trader_pnl: dict[str, float] = {}
        self.per_coin_pnl: dict[str, float] = {}
        # A2 : drawdown circuit breaker — tracker HWM sur realized PnL
        # HWM (high water mark) = max(total_pnl) atteint depuis le start
        self.hwm: float = 0.0


    # --- queries ---

    def has_open(self, trader: str, coin: str, is_long: bool) -> bool:
        return (trader.lower(), coin, is_long) in self.open_positions

    def get(self, trader: str, coin: str, is_long: bool) -> PaperPosition | None:
        return self.open_positions.get((trader.lower(), coin, is_long))

    def all_open(self) -> dict[tuple, PaperPosition]:
        return self.open_positions

    def active_total(self) -> int:
        return len(self.open_positions)

    def active_per_asset(self) -> Counter[str]:
        c: Counter[str] = Counter()
        for k in self.open_positions:
            c[k[1]] += 1
        return c

    # --- mutations ---

    def open(self, position: PaperPosition) -> bool:
        """Idempotent : si position déjà ouverte pour (trader,coin,is_long), False."""
        key = (position.trader.lower(), position.coin, position.is_long)
        if key in self.open_positions:
            return False
        self.open_positions[key] = position
        self.n_opens += 1
        self._log({"event": "open", **position.to_dict()})
        return True

    def close(self, trader: str, coin: str, is_long: bool, exit_price: float,
              exit_ts_ms: int, exit_fee_usd: float, exit_fill_id: str = ""
              ) -> tuple[float, float, float] | None:
        """Ferme position. Retourne (net_pnl, gross_pnl, total_fees) ou None si pas trouvée."""
        key = (trader.lower(), coin, is_long)
        pos = self.open_positions.pop(key, None)
        if pos is None:
            return None
        net_pnl, gross_pnl, total_fees = pos.realized_pnl(exit_price, exit_fee_usd)
        self.total_pnl += net_pnl
        self.total_funding_paid += pos.funding_accrued_usd
        # A2 : update HWM si total_pnl monte
        if self.total_pnl > self.hwm:
            self.hwm = self.total_pnl
        self.per_trader_pnl[pos.trader] = self.per_trader_pnl.get(pos.trader, 0) + net_pnl
        self.per_coin_pnl[coin] = self.per_coin_pnl.get(coin, 0) + net_pnl
        self.n_closes += 1
        if net_pnl > 0:
            self.n_wins += 1
        elif net_pnl < 0:
            self.n_losses += 1
        self._log({
            "event": "close", "trader": pos.trader, "coin": coin,
            "was_long": is_long, "size": pos.size,
            "entry_price": pos.entry_price, "exit_price": exit_price,
            "hold_ms": exit_ts_ms - pos.open_ts_ms,
            "gross_pnl": gross_pnl, "fees_total": total_fees,
            "funding_accrued": pos.funding_accrued_usd,
            "net_pnl": net_pnl, "exit_ts_ms": exit_ts_ms,
            "exit_fill_id": exit_fill_id,
        })
        return net_pnl, gross_pnl, total_fees

    def apply_funding(self, position: PaperPosition, hourly_rate: float,
                      ts_ms: int) -> float:
        """Apply funding to a single position. Log the event."""
        delta = position.apply_funding(hourly_rate, ts_ms)
        self._log({
            "event": "funding", "trader": position.trader,
            "coin": position.coin, "is_long": position.is_long,
            "hourly_rate": hourly_rate, "delta_usd": delta,
            "funding_accrued_total": position.funding_accrued_usd,
            "ts_ms": ts_ms,
        })
        return delta

    def _log(self, obj: dict[str, Any]):
        with open(self.log_path, "a") as fh:
            fh.write(json.dumps(obj) + "\n")

    # --- state recovery (overnight robustness) ---

    def restore_from_jsonl(self) -> dict:
        """Replay JSONL events pour reconstruire open_positions + stats.

        Idempotent : peut être appelé même si la DB est vide.
        Tolère lignes corrompues (les compte mais skip).
        """
        if not self.log_path.exists():
            return dict(events=0, opens=0, closes=0, funding=0,
                        bad_lines=0, open_restored=0)
        n_events = n_open = n_close = n_funding = bad = 0
        with open(self.log_path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    bad += 1
                    continue
                n_events += 1
                ev_type = ev.get("event")
                try:
                    if ev_type == "open":
                        self._replay_open(ev)
                        n_open += 1
                    elif ev_type == "close":
                        self._replay_close(ev)
                        n_close += 1
                    elif ev_type == "funding":
                        self._replay_funding(ev)
                        n_funding += 1
                except (KeyError, TypeError, ValueError):
                    bad += 1
        return dict(
            events=n_events, opens=n_open, closes=n_close,
            funding=n_funding, bad_lines=bad,
            open_restored=len(self.open_positions),
        )

    def _replay_open(self, ev: dict):
        pos = PaperPosition(
            trader=ev["trader"], coin=ev["coin"], is_long=ev["is_long"],
            size=float(ev["size"]),
            entry_price=float(ev["entry_price"]),
            leverage=float(ev.get("leverage", 1.0)),
            open_ts_ms=int(ev["open_ts_ms"]),
            open_fee_usd=float(ev["open_fee_usd"]),
            open_fill_id=ev.get("open_fill_id", ""),
            funding_accrued_usd=float(ev.get("funding_accrued_usd", 0.0)),
            last_funding_ts_ms=int(ev.get("last_funding_ts_ms", 0)),
        )
        key = (pos.trader.lower(), pos.coin, pos.is_long)
        self.open_positions[key] = pos
        self.n_opens += 1

    def _replay_close(self, ev: dict):
        key = (ev["trader"].lower(), ev["coin"], ev["was_long"])
        self.open_positions.pop(key, None)
        net_pnl = float(ev.get("net_pnl", 0.0))
        funding = float(ev.get("funding_accrued", 0.0))
        self.total_pnl += net_pnl
        self.total_funding_paid += funding
        # A2 fix : update HWM aussi pendant le replay JSONL (sinon DD circuit
        # ne sait pas que le PnL a atteint un peak avant restart).
        if self.total_pnl > self.hwm:
            self.hwm = self.total_pnl
        trader = ev["trader"]
        coin = ev["coin"]
        self.per_trader_pnl[trader] = self.per_trader_pnl.get(trader, 0) + net_pnl
        self.per_coin_pnl[coin] = self.per_coin_pnl.get(coin, 0) + net_pnl
        self.n_closes += 1
        if net_pnl > 0:
            self.n_wins += 1
        elif net_pnl < 0:
            self.n_losses += 1

    def _replay_funding(self, ev: dict):
        key = (ev["trader"].lower(), ev["coin"], ev["is_long"])
        pos = self.open_positions.get(key)
        if pos is not None:
            pos.funding_accrued_usd = float(
                ev.get("funding_accrued_total", pos.funding_accrued_usd))
            pos.last_funding_ts_ms = int(
                ev.get("ts_ms", pos.last_funding_ts_ms))

    # --- drawdown circuit breaker (A2) ---

    def current_drawdown_pct(self) -> float:
        """Retourne drawdown actuel en % du HWM.
        DD = (hwm - total_pnl) / max(hwm, |total_pnl|, 1).
        Ratio absolu sur capital de référence (denominator capital nominal).
        Capital ref hard-codé $300 NANO pour V1 — TODO param plus tard.
        """
        capital_ref = 300.0  # NANO baseline
        if self.hwm <= 0 and self.total_pnl >= 0:
            return 0.0  # pas encore de HWM positif
        # DD = drawdown absolu / capital
        dd_abs = max(0.0, self.hwm - self.total_pnl)
        return dd_abs / capital_ref

    # --- summary ---

    def summary(self) -> dict:
        wr = (100.0 * self.n_wins / (self.n_wins + self.n_losses)
              if (self.n_wins + self.n_losses) else 0.0)
        return dict(
            open=self.active_total(),
            n_opens=self.n_opens,
            n_closes=self.n_closes,
            wins=self.n_wins,
            losses=self.n_losses,
            wr_pct=wr,
            total_pnl=self.total_pnl,
            total_funding_paid=self.total_funding_paid,
            top_trader=max(self.per_trader_pnl.items(), key=lambda x: x[1])
            if self.per_trader_pnl else None,
            bot_trader=min(self.per_trader_pnl.items(), key=lambda x: x[1])
            if self.per_trader_pnl else None,
        )
