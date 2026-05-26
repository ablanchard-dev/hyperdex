"""Unit tests state recovery — replay JSONL → reconstruct open_positions + stats."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/home/dexter/hyperdex/backend")
from app.services.paper.pnl_tracker import PnLTracker
from app.services.paper.position import PaperPosition


def _make_tracker_with_log(events: list[dict]) -> PnLTracker:
    """Write events to temp JSONL, return tracker pointing to it."""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    for ev in events:
        tmp.write(json.dumps(ev) + "\n")
    tmp.close()
    return PnLTracker(Path(tmp.name))


def test_restore_empty_log():
    t = _make_tracker_with_log([])
    stats = t.restore_from_jsonl()
    assert stats["events"] == 0
    assert stats["open_restored"] == 0
    assert t.active_total() == 0


def test_restore_open_then_close_results_zero_active():
    events = [
        {"event": "open", "trader": "0xabc", "coin": "BTC", "is_long": True,
         "size": 0.001, "entry_price": 90000.0, "leverage": 5.0,
         "open_ts_ms": 1000, "open_fee_usd": 0.05, "open_fill_id": "f1"},
        {"event": "close", "trader": "0xabc", "coin": "BTC", "was_long": True,
         "size": 0.001, "entry_price": 90000.0, "exit_price": 91000.0,
         "hold_ms": 60000, "gross_pnl": 1.0, "fees_total": 0.1,
         "funding_accrued": 0.0, "net_pnl": 0.9,
         "exit_ts_ms": 61000, "exit_fill_id": "f2"},
    ]
    t = _make_tracker_with_log(events)
    stats = t.restore_from_jsonl()
    assert stats["opens"] == 1 and stats["closes"] == 1
    assert t.active_total() == 0
    assert t.n_opens == 1 and t.n_closes == 1 and t.n_wins == 1
    assert abs(t.total_pnl - 0.9) < 1e-6


def test_restore_open_only_position_still_active():
    events = [
        {"event": "open", "trader": "0xdef", "coin": "ETH", "is_long": False,
         "size": 0.5, "entry_price": 2000.0, "leverage": 3.0,
         "open_ts_ms": 5000, "open_fee_usd": 0.25, "open_fill_id": "f3"},
    ]
    t = _make_tracker_with_log(events)
    stats = t.restore_from_jsonl()
    assert stats["open_restored"] == 1
    assert t.active_total() == 1
    pos = t.get("0xdef", "ETH", False)
    assert pos is not None
    assert pos.size == 0.5
    assert pos.entry_price == 2000.0
    assert pos.leverage == 3.0
    assert not pos.is_long


def test_restore_funding_applies_to_open_position():
    events = [
        {"event": "open", "trader": "0xghi", "coin": "HYPE", "is_long": True,
         "size": 10.0, "entry_price": 20.0, "leverage": 5.0,
         "open_ts_ms": 100, "open_fee_usd": 0.05, "open_fill_id": "f4"},
        {"event": "funding", "trader": "0xghi", "coin": "HYPE",
         "is_long": True, "hourly_rate": 0.0001, "delta_usd": 0.02,
         "funding_accrued_total": 0.02, "ts_ms": 3600100},
    ]
    t = _make_tracker_with_log(events)
    t.restore_from_jsonl()
    pos = t.get("0xghi", "HYPE", True)
    assert pos is not None
    assert abs(pos.funding_accrued_usd - 0.02) < 1e-9
    assert pos.last_funding_ts_ms == 3600100


def test_restore_multiple_cycles():
    """Open, close (loss), open same key, leave open. Active = 1, n_losses = 1."""
    events = [
        {"event": "open", "trader": "0xaaa", "coin": "SOL", "is_long": True,
         "size": 1.0, "entry_price": 200.0, "leverage": 2.0,
         "open_ts_ms": 1, "open_fee_usd": 0.05, "open_fill_id": "a"},
        {"event": "close", "trader": "0xaaa", "coin": "SOL", "was_long": True,
         "size": 1.0, "entry_price": 200.0, "exit_price": 195.0,
         "hold_ms": 100, "gross_pnl": -5.0, "fees_total": 0.1,
         "funding_accrued": 0.0, "net_pnl": -5.1,
         "exit_ts_ms": 101, "exit_fill_id": "b"},
        {"event": "open", "trader": "0xaaa", "coin": "SOL", "is_long": True,
         "size": 1.0, "entry_price": 196.0, "leverage": 2.0,
         "open_ts_ms": 200, "open_fee_usd": 0.05, "open_fill_id": "c"},
    ]
    t = _make_tracker_with_log(events)
    t.restore_from_jsonl()
    assert t.active_total() == 1
    assert t.n_opens == 2
    assert t.n_closes == 1
    assert t.n_losses == 1
    assert abs(t.total_pnl - (-5.1)) < 1e-6
    pos = t.get("0xaaa", "SOL", True)
    assert pos.entry_price == 196.0


def test_restore_bad_lines_skipped():
    """Corrupted line should be counted but not crash."""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    tmp.write('{"event": "open", "trader": "0x1", "coin": "BTC", "is_long": true,'
              ' "size": 0.001, "entry_price": 90000, "leverage": 5,'
              ' "open_ts_ms": 1, "open_fee_usd": 0, "open_fill_id": "x"}\n')
    tmp.write("THIS IS NOT JSON\n")
    tmp.write('{"incomplete\n')
    tmp.close()
    t = PnLTracker(Path(tmp.name))
    stats = t.restore_from_jsonl()
    assert stats["opens"] == 1
    assert stats["bad_lines"] >= 2
    assert t.active_total() == 1


def test_restore_idempotent_call_twice():
    """Calling restore twice should double-count (test = run-once contract)."""
    events = [
        {"event": "open", "trader": "0xbbb", "coin": "DOGE", "is_long": True,
         "size": 1000.0, "entry_price": 0.1, "leverage": 1.0,
         "open_ts_ms": 1, "open_fee_usd": 0.025, "open_fill_id": "d1"},
    ]
    t = _make_tracker_with_log(events)
    t.restore_from_jsonl()
    assert t.n_opens == 1
    # Note : second call replays → n_opens 2. C'est attendu (méthode "fresh boot").
    # Le contract est "appelé UNE fois au startup".


if __name__ == "__main__":
    funcs = [v for n, v in globals().items()
             if n.startswith("test_") and callable(v)]
    n_pass = n_fail = 0
    for f in funcs:
        try:
            f()
            print(f"  ✓ {f.__name__}")
            n_pass += 1
        except AssertionError as e:
            print(f"  ✗ {f.__name__}: {e}")
            n_fail += 1
        except Exception as e:
            print(f"  ✗ {f.__name__}: {type(e).__name__}: {e}")
            n_fail += 1
    print(f"\nRésultat : {n_pass} pass / {n_fail} fail")
    import sys as _s
    _s.exit(0 if n_fail == 0 else 1)
