"""Tests pour recency.py — détection des new-listings (la VRAIE niche inefficiente).

HL meta ne donne pas la date de listing → proxy : âge = (now - timestamp de la
1ère candle disponible). Un perp récemment listé a un historique court.

Run: cd backend/edge_factory && ../../.venv/bin/python test_recency.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adapter as ad  # noqa: E402
import recency as rc  # noqa: E402

DAY = 86_400_000


class _MockAdapter(ad.VenueAdapter):
    name = "mock"

    def __init__(self, now_ms):
        self._now = now_ms
        # OLDCOIN : 1ère barre il y a 300j ; NEWCOIN : il y a 20j
        self._first = {"OLDCOIN": now_ms - 300 * DAY, "NEWCOIN": now_ms - 20 * DAY}

    def universe(self):
        return ["OLDCOIN", "NEWCOIN"]

    def history(self, symbol, start, end):
        first = self._first[symbol]
        # barres horaires depuis first jusqu'à now (échantillon : juste bornes)
        return [ad.Bar(ts=first, close=1.0), ad.Bar(ts=self._now, close=1.1)]

    def fees(self, symbol):
        return ad.Fees(4.5, 1.5)

    def benchmark(self, start, end):
        return []


def test_listing_age_days():
    now = 1_000_000 * DAY
    a = _MockAdapter(now)
    assert abs(rc.listing_age_days(a, "OLDCOIN", now) - 300) < 0.01
    assert abs(rc.listing_age_days(a, "NEWCOIN", now) - 20) < 0.01


def test_new_listings_filter():
    now = 1_000_000 * DAY
    a = _MockAdapter(now)
    nl = rc.new_listings(a, now, max_age_days=90)
    names = [s for s, age in nl]
    assert names == ["NEWCOIN"]  # OLDCOIN (300j) exclu, NEWCOIN (20j) gardé


def test_new_listings_empty_safe():
    now = 1_000_000 * DAY
    a = _MockAdapter(now)
    assert rc.new_listings(a, now, max_age_days=10) == []  # aucun < 10j


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
