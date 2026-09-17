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
    from app.services.paper.position import PaperPosition
    tracker = _Tracker(size)
    # levier 1 : liquidation impossible, ces tests ne portent que sur la sortie au VWAP
    tracker.pos = PaperPosition(trader="0xabc", coin="BTC", is_long=is_long, size=size,
                                entry_price=100.0, leverage=1.0, open_ts_ms=0,
                                open_fee_usd=0.0, open_fill_id="x")
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


# --- Liquidation : la copie peut sauter alors que le trader copie tient ------------
# Avant, liquidation_price n'etait que LOGUE : une copie a 5x traversait en paper un
# mouvement qui l'aurait liquidee en live, et le PnL paper restait optimiste.

from app.services.paper.position import PaperPosition


class _HoldingInfo(_Info):
    def __init__(self, book):
        self.book = book

    def user_state(self, addr):  # le trader copie TIENT toujours sa position
        return {"assetPositions": [{"position": {"coin": "BTC", "szi": "1.0"}},
                                   {"position": {"coin": "ETH", "szi": "-1.0"}}]}

    def l2_snapshot(self, coin):
        return self.book


def _book(bid, ask):
    return {"levels": [[{"px": str(bid), "sz": "10"}], [{"px": str(ask), "sz": "10"}]]}


def _run(pos, book):
    tracker = _Tracker(1.0)
    tracker.pos = pos
    rec = PositionReconciler(tracker, _HoldingInfo(book), verbose=False)
    asyncio.run(rec._reconcile_one("0xabc", pos.coin, pos.is_long))
    return tracker.closed, rec.stats


def _pos(is_long):
    # entry 100, 5x, maint 5% -> liq long 85, liq short 115
    return PaperPosition(trader="0xabc", coin="BTC" if is_long else "ETH", is_long=is_long,
                         size=1.0, entry_price=100.0, leverage=5.0, open_ts_ms=0,
                         open_fee_usd=0.0, open_fill_id="x", maint_margin_pct=0.05)


def test_long_liquidated_when_bid_crosses_liquidation_price_even_if_trader_holds():
    closed, stats = _run(_pos(True), _book(84.0, 84.5))
    assert closed is not None, "la copie doit etre liquidee"
    assert abs(closed["exit_price"] - 85.0) < 1e-9
    assert closed["exit_fill_id"].startswith("liquidation:")
    assert stats["liquidations"] == 1


def test_short_liquidated_when_ask_crosses_liquidation_price():
    closed, _ = _run(_pos(False), _book(115.5, 116.0))
    assert closed is not None
    assert abs(closed["exit_price"] - 115.0) < 1e-9


def test_no_liquidation_above_the_threshold_and_trader_still_holds():
    closed, stats = _run(_pos(True), _book(90.0, 90.5))
    assert closed is None
    assert stats["liquidations"] == 0


# --- Surveillance continue : une meche entre deux cycles de 5 min ne passe plus ------
# HL liquide sur le prix mark ; metaAndAssetCtxs couvre toutes les coins en UN appel (poids 20).

class _OpenTracker(_Tracker):
    def __init__(self, positions):
        self.open_positions = {(p.trader, p.coin, p.is_long): p for p in positions}
        self.closed_all = []

    def get(self, trader, coin, is_long):
        return self.open_positions.get((trader, coin, is_long))

    def close(self, **kw):
        self.closed_all.append(kw)
        self.open_positions.pop((kw["trader"], kw["coin"], kw["is_long"]), None)
        return (0.0, 0.0, 0.0)


def test_liquidation_watch_closes_on_mark_cross_without_waiting_for_the_cycle():
    tracker = _OpenTracker([_pos(True), _pos(False)])  # BTC long liq 85, ETH short liq 115
    rec = PositionReconciler(tracker, _HoldingInfo(_book(90, 91)), verbose=False)
    n = rec.check_liquidations({"BTC": "84.9", "ETH": "110"})
    assert n == 1
    assert [c["coin"] for c in tracker.closed_all] == ["BTC"]
    assert abs(tracker.closed_all[0]["exit_price"] - 85.0) < 1e-9
    assert rec.stats["liquidations"] == 1


def test_liquidation_watch_ignores_missing_or_bad_marks():
    tracker = _OpenTracker([_pos(True)])
    rec = PositionReconciler(tracker, _HoldingInfo(_book(90, 91)), verbose=False)
    assert rec.check_liquidations({}) == 0
    assert rec.check_liquidations({"BTC": "nan?"}) == 0
    assert tracker.closed_all == []


# --- Prix MARK, pas mid : c'est sur le mark que Hyperliquid liquide -------------------
def test_marks_are_read_from_meta_and_asset_ctxs_by_universe_index():
    meta = {"universe": [{"name": "BTC"}, {"name": "ETH"}]}
    ctxs = [{"markPx": "100.5", "midPx": "99"}, {"markPx": "7.25", "midPx": "7"}]
    assert PositionReconciler.marks_from_ctxs([meta, ctxs]) == {"BTC": 100.5, "ETH": 7.25}


def test_marks_ignore_malformed_entries():
    meta = {"universe": [{"name": "BTC"}, {"name": "X"}, {"name": "Y"}]}
    ctxs = [{"markPx": "100"}, {"midPx": "1"}, {"markPx": "nope"}]
    assert PositionReconciler.marks_from_ctxs([meta, ctxs]) == {"BTC": 100.0}
    assert PositionReconciler.marks_from_ctxs(None) == {}
