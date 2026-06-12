"""Tests pour adapter.py — l'interface VenueAdapter venue-agnostic.

Le cœur de la factory ne connaît PAS la venue : il parle à un VenueAdapter
abstrait. Chaque niche (a prediction market météo, HL small-cap, futures…) = une impl.
concrète. Contrat minimal Phase 1 : universe / history / fees / benchmark.

Run: cd backend/edge_factory && ../../.venv/bin/python test_adapter.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adapter as a  # noqa: E402


class _FakeVenue(a.VenueAdapter):
    name = "fake"

    def universe(self):
        return ["AAA", "BBB"]

    def history(self, symbol, start, end):
        return [a.Bar(ts=1, close=100.0), a.Bar(ts=2, close=110.0),
                a.Bar(ts=3, close=99.0)]

    def fees(self, symbol):
        return a.Fees(taker_bps=4.5, maker_bps=-1.0)

    def benchmark(self, start, end):
        return [a.Bar(ts=1, close=50.0), a.Bar(ts=2, close=55.0)]


def test_abstract_cannot_instantiate():
    # VenueAdapter est abstrait -> instanciation directe interdite
    try:
        a.VenueAdapter()  # type: ignore[abstract]  # intentionnel : doit lever
        raised = False
    except TypeError:
        raised = True
    assert raised


def test_concrete_impl_works():
    v = _FakeVenue()
    assert v.name == "fake"
    assert v.universe() == ["AAA", "BBB"]
    assert v.fees("AAA").taker_bps == 4.5
    assert v.fees("AAA").maker_bps == -1.0
    bars = v.history("AAA", 0, 9)
    assert len(bars) == 3 and bars[1].close == 110.0


def test_returns_from_bars_known():
    bars = [a.Bar(ts=1, close=100.0), a.Bar(ts=2, close=110.0),
            a.Bar(ts=3, close=99.0)]
    r = a.returns_from_bars(bars)
    assert len(r) == 2
    assert abs(r[0] - 0.10) < 1e-9      # 100 -> 110 = +10%
    assert abs(r[1] - (-0.10)) < 1e-9   # 110 -> 99 = -10%


def test_returns_empty_safe():
    assert a.returns_from_bars([]) == []
    assert a.returns_from_bars([a.Bar(ts=1, close=100.0)]) == []


if __name__ == "__main__":
    fns = [val for k, val in sorted(globals().items()) if k.startswith("test_")]
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
