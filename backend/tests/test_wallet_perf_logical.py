"""Tests pour `compute_hold_ms_logical` — A4 fix (2026-05-27).

Run standalone :
    cd backend && .venv/bin/python -m pytest tests/test_wallet_perf_logical.py -v
ou :
    cd backend && .venv/bin/python tests/test_wallet_perf_logical.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Permet l'exécution `python tests/test_wallet_perf_logical.py` sans pytest.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.paper.wallet_perf import (  # noqa: E402
    compute_hold_ms_logical,
    median_hold_ms_logical,
)


def _f(coin: str, side: str, sz: float, time_ms: int, oid: int = 0) -> dict:
    """Helper pour fabriquer un fill HL minimal."""
    return {"coin": coin, "side": side, "sz": sz, "time": time_ms, "oid": oid}


# ---------------------------------------------------------------------------
# Test 1 : 5 fills atomiques same oid en <1s → 1 ouverture / 0 close.
# (Couvre le bug racine A4 : 5 fragments d'1 ordre marché ne doivent PAS
#  produire 5 cycles de hold_ms ~200 ms chacun.)
# ---------------------------------------------------------------------------
def test_atomic_fills_same_order_no_spurious_closes():
    fills = [
        _f("HYPE", "B", 7.5, 1_000_000_000, oid=42),
        _f("HYPE", "B", 7.5, 1_000_000_100, oid=42),
        _f("HYPE", "B", 7.5, 1_000_000_200, oid=42),
        _f("HYPE", "B", 7.5, 1_000_000_300, oid=42),
        _f("HYPE", "B", 7.5, 1_000_000_400, oid=42),
    ]
    closures = compute_hold_ms_logical(fills)
    assert closures == [], (
        f"5 fills atomiques same-side ne doivent produire AUCUN close, "
        f"got {closures}"
    )
    assert median_hold_ms_logical(fills) is None


# ---------------------------------------------------------------------------
# Test 2 : open BUY puis 10s plus tard close SELL même size → 1 cycle 10000ms.
# ---------------------------------------------------------------------------
def test_simple_open_then_close_10s():
    fills = [
        _f("BTC", "B", 0.5, 1_700_000_000_000),
        _f("BTC", "A", 0.5, 1_700_000_010_000),  # +10s
    ]
    closures = compute_hold_ms_logical(fills)
    assert len(closures) == 1
    c = closures[0]
    assert c["coin"] == "BTC"
    assert c["side"] == "long"
    assert c["hold_ms"] == 10_000


# ---------------------------------------------------------------------------
# Test 3 : multi-coin entrelacé. HYPE entry, BTC entry, HYPE exit, BTC exit
# → 2 holds corrects (HYPE hold court, BTC hold long).
# ---------------------------------------------------------------------------
def test_multi_coin_interleaved():
    t0 = 1_700_000_000_000
    fills = [
        _f("HYPE", "B", 10.0, t0),
        _f("BTC", "B", 0.1, t0 + 5_000),
        _f("HYPE", "A", 10.0, t0 + 60_000),    # HYPE hold = 60s
        _f("BTC", "A", 0.1, t0 + 600_000),     # BTC  hold = 595s
    ]
    closures = compute_hold_ms_logical(fills)
    assert len(closures) == 2
    by_coin = {c["coin"]: c for c in closures}
    assert by_coin["HYPE"]["hold_ms"] == 60_000
    assert by_coin["BTC"]["hold_ms"] == 595_000


# ---------------------------------------------------------------------------
# Test 4 : input en reverse-chrono (comme HL `user_fills_by_time` brut).
# Le tri ascending interne doit donner le bon résultat.
# ---------------------------------------------------------------------------
def test_reverse_chronological_input():
    t0 = 1_700_000_000_000
    fills_correct = [
        _f("HYPE", "B", 5.0, t0),
        _f("HYPE", "A", 5.0, t0 + 30_000),
    ]
    closures_correct = compute_hold_ms_logical(fills_correct)
    fills_reversed = list(reversed(fills_correct))
    closures_reversed = compute_hold_ms_logical(fills_reversed)
    assert closures_correct == closures_reversed
    assert closures_reversed[0]["hold_ms"] == 30_000


# ---------------------------------------------------------------------------
# Test 5 : scale-in puis close partiels (taille agrégée).
# OPEN sz=10, OPEN sz=5 (add), CLOSE sz=8 (partial), CLOSE sz=7 (close fully).
# → 1 cycle, hold = du 1er OPEN au last CLOSE.
# ---------------------------------------------------------------------------
def test_scale_in_then_close():
    t0 = 1_700_000_000_000
    fills = [
        _f("HYPE", "B", 10.0, t0),                 # OPEN long sz=10
        _f("HYPE", "B", 5.0, t0 + 5_000),          # ADD sz=5 → sz=15
        _f("HYPE", "A", 8.0, t0 + 120_000),        # CLOSE partial sz=8 → sz=7
        _f("HYPE", "A", 7.0, t0 + 300_000),        # CLOSE full → sz=0
    ]
    closures = compute_hold_ms_logical(fills)
    assert len(closures) == 1
    assert closures[0]["hold_ms"] == 300_000
    assert closures[0]["side"] == "long"


# ---------------------------------------------------------------------------
# Test 6 : flip long → short. Open long 10, then sell 15 → close long, open
# short 5. Vérifie 1 close + nouveau état short.
# ---------------------------------------------------------------------------
def test_flip_long_to_short():
    t0 = 1_700_000_000_000
    fills = [
        _f("HYPE", "B", 10.0, t0),
        _f("HYPE", "A", 15.0, t0 + 60_000),        # closes long, opens short 5
        _f("HYPE", "B", 5.0, t0 + 90_000),         # closes short
    ]
    closures = compute_hold_ms_logical(fills)
    assert len(closures) == 2
    # 1er cycle long
    assert closures[0]["side"] == "long"
    assert closures[0]["hold_ms"] == 60_000
    # 2e cycle short, entry au moment du flip
    assert closures[1]["side"] == "short"
    assert closures[1]["hold_ms"] == 30_000


# ---------------------------------------------------------------------------
# Test 7 : median_hold_ms_logical wrapper.
# 3 cycles avec holds [1000, 5000, 10000] → médiane = 5000.
# ---------------------------------------------------------------------------
def test_median_wrapper():
    t0 = 1_700_000_000_000
    fills = [
        _f("HYPE", "B", 1.0, t0),
        _f("HYPE", "A", 1.0, t0 + 1_000),
        _f("HYPE", "B", 1.0, t0 + 10_000),
        _f("HYPE", "A", 1.0, t0 + 15_000),
        _f("HYPE", "B", 1.0, t0 + 100_000),
        _f("HYPE", "A", 1.0, t0 + 110_000),
    ]
    assert median_hold_ms_logical(fills) == 5_000


# ---------------------------------------------------------------------------
# Test 8 : no closure (only opens, no flip / no flat) → median = None.
# Reproduit le pattern d'un scalper sur 24h qui ne touche jamais 0 :
# accumulation longue + scale-out partials qui ne ferment jamais.
# ---------------------------------------------------------------------------
def test_no_closure_only_opens():
    t0 = 1_700_000_000_000
    fills = [
        _f("HYPE", "B", 10.0, t0),
        _f("HYPE", "B", 10.0, t0 + 1_000),
        _f("HYPE", "B", 10.0, t0 + 2_000),
    ]
    assert compute_hold_ms_logical(fills) == []
    assert median_hold_ms_logical(fills) is None


# ---------------------------------------------------------------------------
# Runner standalone.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    tests = [
        test_atomic_fills_same_order_no_spurious_closes,
        test_simple_open_then_close_10s,
        test_multi_coin_interleaved,
        test_reverse_chronological_input,
        test_scale_in_then_close,
        test_flip_long_to_short,
        test_median_wrapper,
        test_no_closure_only_opens,
    ]
    fails = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            print(f"FAIL  {t.__name__}: {e}")
            fails += 1
        except Exception as e:
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
            fails += 1
    print(f"\n{len(tests) - fails}/{len(tests)} passed")
    sys.exit(0 if fails == 0 else 1)
