"""Phantom close must walk the book like every other paper fill.

Before: when the WebSocket missed a close, the reconciler closed the position at the
MID price with a 0.025% fee. The mid ignores the spread and the depth, and the fee was
below the taker rate the fill simulator was recalibrated to, so every phantom close
booked an optimistic PnL, against the project's own "never mid-price" rule.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.paper.fill_simulator import FillSimulator
from app.services.paper.reconcile import PositionReconciler

# bids 99 x1, 98 x1 ; asks 101 x1, 102 x1  -> mid = 100
BOOK = {"levels": [[{"px": "99", "sz": "1"}, {"px": "98", "sz": "1"}],
                   [{"px": "101", "sz": "1"}, {"px": "102", "sz": "1"}]]}


class _Info:
    def user_state(self, addr):
        return {"assetPositions": []}  # the wallet no longer holds anything

    def l2_snapshot(self, coin):
        return BOOK


class _Tracker:
    total_pnl = 0.0

    def __init__(self, size):
        self.pos = SimpleNamespace(size=size)
        self.closed = None

    def get(self, trader, coin, is_long):
        return self.pos

    def close(self, **kw):
        self.closed = kw
        return (0.0, 0.0, 0.0)


def _close(is_long: bool, size: float):
    tracker = _Tracker(size)
    rec = PositionReconciler(tracker, _Info(), verbose=False)
    asyncio.run(rec._reconcile_one("0xabc", "BTC", is_long))
    return tracker.closed


def test_long_phantom_close_sells_into_the_bids_not_the_mid():
    closed = _close(is_long=True, size=2.0)
    assert closed["exit_price"] == 98.5  # (99 + 98) / 2, not 100
    assert abs(closed["exit_fee_usd"] - 2.0 * 98.5 * FillSimulator.DEFAULT_FEE_RATE) < 1e-9


def test_short_phantom_close_buys_from_the_asks_not_the_mid():
    closed = _close(is_long=False, size=1.0)
    assert closed["exit_price"] == 101.0
