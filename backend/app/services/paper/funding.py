"""FundingAccrual — loop hourly snapshot funding sur positions ouvertes.

HL paye le funding **hourly** (snapshot à HH:00 UTC). Chaque snapshot :
- fetch `meta_and_asset_ctxs()` → rate horaire par actif (champ `funding`).
- pour chaque position ouverte, applique le delta selon (long/short, rate).

Le rate HL `funding` est le taux HORAIRE signed decimal.
- rate > 0  : longs paient (cost positif), shorts reçoivent (cost négatif).
- rate < 0  : inverse.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

from app.services.paper.pnl_tracker import PnLTracker


def _log(*a):
    print("[FUNDING]", *a, flush=True)


class FundingAccrual:
    """Hourly funding loop sur positions ouvertes."""

    def __init__(self, info, tracker: PnLTracker, dry_run: bool = True):
        self.info = info
        self.tracker = tracker
        self.dry_run = dry_run
        self._stop = False

    def stop(self):
        self._stop = True

    async def run(self):
        """Loop forever : sleep jusqu'à next HH:00 UTC + 10s, apply funding."""
        _log("loop started")
        while not self._stop:
            try:
                wait_s = self._seconds_to_next_hour() + 10  # 10s buffer post-snapshot
                _log(f"next funding apply in {wait_s:.0f}s")
                await asyncio.sleep(wait_s)
                if self._stop:
                    break
                await self._apply_one_snapshot()
            except asyncio.CancelledError:
                break
            except Exception as e:
                _log(f"loop error: {type(e).__name__}: {e} — retry dans 60s")
                await asyncio.sleep(60)
        _log("loop stopped")

    def _seconds_to_next_hour(self) -> float:
        now = datetime.now(timezone.utc)
        nxt = (now.replace(minute=0, second=0, microsecond=0)
               + timedelta(hours=1))
        return max(1.0, (nxt - now).total_seconds())

    async def _apply_one_snapshot(self):
        if not self.tracker.open_positions:
            _log("0 position ouverte — skip")
            return
        # fetch ctx (rates)
        try:
            ctx = await asyncio.get_event_loop().run_in_executor(
                None, self.info.meta_and_asset_ctxs)
        except Exception as e:
            _log(f"fetch meta_and_asset_ctxs failed: {e}")
            return
        rates = self._extract_hourly_rates(ctx)
        if not rates:
            _log("no rates parsed")
            return
        ts_ms = int(time.time() * 1000)
        applied = 0
        cumul_delta = 0.0
        for key, pos in list(self.tracker.open_positions.items()):
            rate_h = rates.get(pos.coin)
            if rate_h is None:
                continue
            delta = self.tracker.apply_funding(pos, rate_h, ts_ms)
            cumul_delta += delta
            applied += 1
        _log(f"applied to {applied} positions, cumul_delta=${cumul_delta:+.4f}")

    def _extract_hourly_rates(self, ctx) -> dict[str, float]:
        """meta_and_asset_ctxs() returns [meta, asset_ctxs_list].
        Each asset_ctx has a `funding` field (hourly decimal rate).
        """
        if not isinstance(ctx, list) or len(ctx) < 2:
            return {}
        meta, asset_ctxs = ctx[0], ctx[1]
        universe = meta.get("universe", []) if isinstance(meta, dict) else []
        if not isinstance(asset_ctxs, list):
            return {}
        rates: dict[str, float] = {}
        for i, ac in enumerate(asset_ctxs):
            if i >= len(universe) or not isinstance(ac, dict):
                continue
            coin = universe[i].get("name") if isinstance(universe[i], dict) else None
            if not coin:
                continue
            try:
                rates[coin] = float(ac.get("funding", 0))
            except Exception:
                continue
        return rates
