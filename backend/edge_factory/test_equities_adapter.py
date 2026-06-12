"""Tests pour equities_adapter.py — venue actions small-cap (API chart Yahoo).

fetch_bars injecté (DI) -> mock déterministe, AUCUN réseau en test unitaire.
Live-check séparé : --live.

Run: cd backend/edge_factory && ../../.venv/bin/python test_equities_adapter.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adapter as ad  # noqa: E402
import equities_adapter as eq  # noqa: E402


def _mock_bars(symbol, start_ms, end_ms):
    return [ad.Bar(ts=1_700_000_000_000, close=10.2, open=10.0, high=10.5,
                   low=9.8, volume=1e5),
            ad.Bar(ts=1_700_086_400_000, close=10.9, open=10.2, high=11.0,
                   low=10.1, volume=1.2e5)]


def test_universe_is_injected_list():
    a = eq.EquitiesAdapter(["AAA", "BBB", "CCC"], fetch_bars=_mock_bars)
    assert a.universe() == ["AAA", "BBB", "CCC"]


def test_history_delegates_to_fetch():
    a = eq.EquitiesAdapter(["AAA"], fetch_bars=_mock_bars)
    bars = a.history("AAA", 0, 9_999_999_999_999)
    assert len(bars) == 2 and isinstance(bars[0], ad.Bar)
    assert bars[0].close == 10.2 and bars[1].close == 10.9


def test_benchmark_uses_iwm():
    seen = {}

    def spy(symbol, a_, b_):
        seen["sym"] = symbol
        return _mock_bars(symbol, a_, b_)

    a = eq.EquitiesAdapter(["AAA"], fetch_bars=spy, benchmark="IWM")
    a.benchmark(0, 9)
    assert seen["sym"] == "IWM"


def test_fees_positive():
    a = eq.EquitiesAdapter(["AAA"], fetch_bars=_mock_bars)
    assert a.fees("AAA").taker_bps > 0


def test_yahoo_parser_uses_adjclose():
    # close DOIT être l'adjclose (ajusté splits+dividendes), PAS le close brut,
    # sinon les returns sont pollués (faux -50% sur un split, etc.).
    payload = {"chart": {"result": [{
        "timestamp": [1_700_000_000, 1_700_086_400, 1_700_172_800],
        "indicators": {
            "quote": [{
                "open": [10.0, 10.2, None],
                "high": [10.5, 11.0, 11.2],
                "low": [9.8, 10.1, 10.5],
                "close": [10.2, 10.9, None],   # close BRUT
                "volume": [100000, 120000, 90000],
            }],
            "adjclose": [{"adjclose": [9.50, 10.20, None]}],  # AJUSTÉ (diffère)
        },
    }]}}
    bars = eq._yahoo_to_bars(payload)
    assert len(bars) == 2  # 3e barre (close/adjclose None) skippée
    assert bars[0].ts == 1_700_000_000_000
    assert bars[0].close == 9.50   # adjclose, pas 10.2
    assert bars[1].close == 10.20  # adjclose, pas 10.9


def test_yahoo_parser_fallback_raw_close():
    # si adjclose absent -> fallback sur close brut (pas de crash)
    payload = {"chart": {"result": [{
        "timestamp": [1_700_000_000],
        "indicators": {"quote": [{
            "open": [10.0], "high": [10.5], "low": [9.8],
            "close": [10.2], "volume": [100000]}]},
    }]}}
    bars = eq._yahoo_to_bars(payload)
    assert len(bars) == 1 and bars[0].close == 10.2


def test_yahoo_parser_garbage_safe():
    assert eq._yahoo_to_bars({}) == []
    assert eq._yahoo_to_bars({"chart": {"result": []}}) == []


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    fails = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            fails += 1
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:
            fails += 1
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - fails}/{len(fns)} passed")
    sys.exit(1 if fails else 0)
