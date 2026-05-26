"""Unit tests FillSimulator — books synthétiques, pas de dépendance API.

Couverture :
  - book freshness (stale → reject ; fresh → OK ; no ts → tolérance)
  - VWAP BUY walks ASKS, lowest first
  - VWAP SELL walks BIDS, highest first
  - partial fill (book trop mince)
  - malformed levels (px/sz manquants, négatifs)
  - side invalide
  - fee calc = notional × fee_rate
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, "/opt/app/hyperdex/backend")
from app.services.paper.fill_simulator import FillSimulator, SimulatedFill


def make_book(asks: list[tuple[float, float]],
              bids: list[tuple[float, float]],
              age_ms: int = 0) -> dict:
    """Helper : build book dict synthétique."""
    return {
        "coin": "TEST",
        "time": int(time.time() * 1000) - age_ms,
        "levels": [
            [{"px": str(p), "sz": str(s), "n": 1} for p, s in bids],
            [{"px": str(p), "sz": str(s), "n": 1} for p, s in asks],
        ],
    }


# ====== freshness ======

def test_fresh_book_accepted():
    sim = FillSimulator(max_book_age_s=2.0)
    book = make_book([(100, 1)], [(99, 1)], age_ms=500)
    err = sim.check_book_fresh(book)
    assert err is None, f"fresh book rejected: {err}"


def test_stale_book_rejected():
    sim = FillSimulator(max_book_age_s=2.0)
    book = make_book([(100, 1)], [(99, 1)], age_ms=3000)
    err = sim.check_book_fresh(book)
    assert err is not None and "stale" in err.lower()


def test_book_no_timestamp_tolerated():
    sim = FillSimulator(max_book_age_s=2.0)
    book = {"levels": [[{"px": "99", "sz": "1", "n": 1}],
                       [{"px": "100", "sz": "1", "n": 1}]]}
    err = sim.check_book_fresh(book)
    assert err is None  # pas de ts = on tolère


# ====== VWAP BUY ======

def test_vwap_buy_single_level():
    sim = FillSimulator(fee_rate=0.001)
    book = make_book(
        asks=[(100.0, 1.0), (101.0, 1.0)],
        bids=[(99.0, 1.0), (98.0, 1.0)],
    )
    vwap, sz, lvls = sim.compute_vwap(book, "B", 0.5)
    assert vwap == 100.0, f"expected 100, got {vwap}"
    assert sz == 0.5
    assert lvls == 1


def test_vwap_buy_walks_multiple_levels():
    sim = FillSimulator()
    book = make_book(
        asks=[(100.0, 0.5), (101.0, 1.0)],
        bids=[(99.0, 1.0)],
    )
    # buy 0.8 → 0.5 @ 100 + 0.3 @ 101 = 50 + 30.3 = 80.3 / 0.8 = 100.375
    vwap, sz, lvls = sim.compute_vwap(book, "B", 0.8)
    assert abs(vwap - 100.375) < 1e-6, f"VWAP {vwap}"
    assert sz == 0.8
    assert lvls == 2


def test_vwap_buy_partial_fill_book_too_thin():
    sim = FillSimulator()
    book = make_book(asks=[(100.0, 0.3)], bids=[(99.0, 1.0)])
    vwap, sz, lvls = sim.compute_vwap(book, "B", 1.0)
    # On ne peut filler que 0.3
    assert vwap == 100.0
    assert sz == 0.3
    assert lvls == 1


# ====== VWAP SELL ======

def test_vwap_sell_walks_bids_highest_first():
    sim = FillSimulator()
    book = make_book(
        asks=[(101.0, 1.0)],
        bids=[(100.0, 0.5), (99.0, 1.0), (98.0, 1.0)],
    )
    # sell 0.8 → 0.5 @ 100 + 0.3 @ 99 = 50 + 29.7 = 79.7 / 0.8 = 99.625
    vwap, sz, lvls = sim.compute_vwap(book, "A", 0.8)
    assert abs(vwap - 99.625) < 1e-6
    assert sz == 0.8
    assert lvls == 2


# ====== bad inputs ======

def test_invalid_side():
    sim = FillSimulator()
    book = make_book(asks=[(100, 1)], bids=[(99, 1)])
    vwap, sz, lvls = sim.compute_vwap(book, "X", 0.5)
    assert vwap == 0.0 and sz == 0.0


def test_empty_book():
    sim = FillSimulator()
    book = {"levels": [[], []], "time": int(time.time()*1000)}
    vwap, sz, lvls = sim.compute_vwap(book, "B", 0.5)
    assert vwap == 0.0 and sz == 0.0


def test_malformed_level_skipped():
    sim = FillSimulator()
    book = make_book(
        asks=[(100.0, 0.5), (-1.0, 1.0), (101.0, 1.0)],  # négatif
        bids=[(99.0, 1.0)],
    )
    vwap, sz, lvls = sim.compute_vwap(book, "B", 0.6)
    # Skip le négatif → walks 100@0.5 puis 101@0.1 = (50+10.1)/0.6 = 100.167
    assert sz == 0.6
    assert abs(vwap - (60.1 / 0.6)) < 1e-6


# ====== simulate() integration ======

def test_simulate_success_fresh_book():
    sim = FillSimulator(fee_rate=0.001, max_book_age_s=5.0)
    book = make_book(asks=[(100.0, 2.0)], bids=[(99.0, 2.0)], age_ms=100)
    f = sim.simulate(book, "B", 1.0)
    assert f.success
    assert f.vwap == 100.0
    assert f.filled_size == 1.0
    assert abs(f.notional_usd - 100.0) < 1e-6
    assert abs(f.fee_usd - 0.1) < 1e-6  # 100 * 0.001
    assert f.levels_walked == 1
    assert f.error is None


def test_simulate_stale_book_rejected():
    sim = FillSimulator(max_book_age_s=1.0)
    book = make_book(asks=[(100, 1)], bids=[(99, 1)], age_ms=2000)
    f = sim.simulate(book, "B", 0.5)
    assert not f.success
    assert "stale" in (f.error or "").lower()


def test_simulate_empty_book_rejected():
    sim = FillSimulator()
    book = {"levels": [[], []], "time": int(time.time()*1000)}
    f = sim.simulate(book, "B", 0.5)
    assert not f.success
    assert "nul" in (f.error or "").lower() or "vide" in (f.error or "").lower()


def test_simulate_fee_rate_zero():
    sim = FillSimulator(fee_rate=0.0)
    book = make_book(asks=[(100, 1)], bids=[(99, 1)])
    f = sim.simulate(book, "B", 0.5)
    assert f.success
    assert f.fee_usd == 0.0


if __name__ == "__main__":
    # Run tests manually
    import sys as _s
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
    _s.exit(0 if n_fail == 0 else 1)
