"""Tests pour hl_adapter.py — HLSmallCapAdapter (niche small-cap/new-listing HL).

Client injecté (DI) -> mock déterministe, AUCUN appel live dans les tests unitaires.
Le live-check réel (touche HL) est séparé : hl_adapter.py --live.

Run: cd backend/edge_factory && ../../.venv/bin/python test_hl_adapter.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adapter as ad  # noqa: E402
import hl_adapter as hla  # noqa: E402


class _MockHL:
    """Imite InfoClient : meta_and_asset_ctxs() + candles()."""

    def meta_and_asset_ctxs(self):
        meta = {"universe": [{"name": "BTC"}, {"name": "SMALLCOIN"},
                             {"name": "BIGCOIN"}]}
        ctxs = [{"dayNtlVlm": "5000000000"},   # BTC 5B -> gros
                {"dayNtlVlm": "1000000"},      # SMALLCOIN 1M -> small-cap
                {"dayNtlVlm": "800000000"}]    # BIGCOIN 800M -> gros
        return [meta, ctxs]

    def candles(self, name, interval, start_ms, end_ms):
        return [
            {"T": 1000, "t": 0, "o": "100", "h": "110", "l": "95",
             "c": "105", "v": "50", "n": 10},
            {"T": 2000, "t": 1000, "o": "105", "h": "120", "l": "100",
             "c": "118", "v": "60", "n": 12},
        ]


def test_universe_filters_smallcaps():
    a = hla.HLSmallCapAdapter(_MockHL(), vol_max_usd=50_000_000)
    u = a.universe()
    assert u == ["SMALLCOIN"]  # BTC 5B et BIGCOIN 800M exclus


def test_history_parses_candles_to_bars():
    a = hla.HLSmallCapAdapter(_MockHL())
    bars = a.history("SMALLCOIN", 0, 9999)
    assert len(bars) == 2
    assert isinstance(bars[0], ad.Bar)
    assert bars[1].ts == 2000
    assert bars[1].close == 118.0
    assert bars[0].open == 100.0 and bars[0].high == 110.0
    assert bars[0].low == 95.0 and bars[0].volume == 50.0


def test_fees_are_hl_schedule():
    a = hla.HLSmallCapAdapter(_MockHL())
    f = a.fees("SMALLCOIN")
    assert f.taker_bps > 0 and f.maker_bps >= 0


def test_benchmark_uses_btc():
    seen = {}

    class _Spy(_MockHL):
        def candles(self, name, interval, start_ms, end_ms):
            seen["name"] = name
            return super().candles(name, interval, start_ms, end_ms)

    a = hla.HLSmallCapAdapter(_Spy(), benchmark="BTC")
    bars = a.benchmark(0, 9999)
    assert seen["name"] == "BTC"
    assert len(bars) == 2 and bars[1].close == 118.0


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
