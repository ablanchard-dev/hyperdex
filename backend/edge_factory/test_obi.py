"""Tests pour obi_signal.py — order-book imbalance (frontière microstructure sous-horaire).

OBI = (vol_bid - vol_ask)/(vol_bid + vol_ask) sur top-N niveaux du carnet l2_snapshot HL
({levels:[[bids],[asks]], chaque niveau {px,sz,n}}). Prédicteur microstructure standard :
OBI>0 = pression acheteuse. ⚠️ prior FAIBLE (HFT-contesté, latence retail) mais seul angle
gratuit non-testé. Ici = calcul PUR (parse carnet → OBI) testable sans réseau ; le recorder
(poll l2_snapshot dans le temps) est l'I/O. No-look-ahead par nature (snapshot = état présent).

Run: cd backend/edge_factory && ../../.venv/bin/python test_obi.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import obi_signal as obi  # noqa: E402


def _book(bids, asks, t=1000):
    return {"coin": "BTC", "time": t,
            "levels": [[{"px": str(p), "sz": str(s), "n": 1} for p, s in bids],
                       [{"px": str(p), "sz": str(s), "n": 1} for p, s in asks]]}


def test_obi_balanced_is_zero():
    b = _book([(100, 1.0)], [(101, 1.0)])
    assert abs(obi.compute_obi(b, depth=1)) < 1e-9


def test_obi_buy_pressure_positive():
    # bid volume >> ask volume -> OBI > 0
    b = _book([(100, 5.0)], [(101, 1.0)])
    assert obi.compute_obi(b, depth=1) > 0.5


def test_obi_sell_pressure_negative():
    b = _book([(100, 1.0)], [(101, 5.0)])
    assert obi.compute_obi(b, depth=1) < -0.5


def test_obi_depth_aggregates_levels():
    # depth=2 agrège 2 niveaux par côté
    b = _book([(100, 1.0), (99, 2.0)], [(101, 1.0), (102, 1.0)])
    # bid vol = 3, ask vol = 2 -> OBI = 1/5 = 0.2
    assert abs(obi.compute_obi(b, depth=2) - 0.2) < 1e-9


def test_obi_empty_book_safe():
    assert obi.compute_obi({"levels": [[], []]}, depth=5) == 0.0
    assert obi.compute_obi({}, depth=5) == 0.0


def test_mid_price():
    b = _book([(100, 1.0)], [(102, 1.0)])
    assert abs(obi.mid_price(b) - 101.0) < 1e-9


def test_obi_series_from_snapshots():
    # liste de snapshots horodatés -> série (ts, obi, mid) triée
    snaps = [_book([(100, 3.0)], [(101, 1.0)], t=2000),
             _book([(100, 1.0)], [(101, 3.0)], t=1000)]
    series = obi.obi_series(snaps, depth=1)
    assert len(series) == 2
    assert series[0][0] == 1000  # trié par temps
    assert series[0][1] < 0 and series[1][1] > 0  # sell puis buy


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
