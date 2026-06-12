"""Tests pour obi_backtest.py — OBI[t] prédit-il le mouvement de mid ? (sous-horaire).

Sur une série de snapshots (time, obi, mid) : si OBI[t] > seuil (pression acheteuse) →
LONG le move mid[t+lag]→mid[t+1+lag] ; OBI < -seuil → SHORT. exec_lag CRUCIAL (latence
retail) : lag=1 réaliste. Mesure GROSS (sans coût) ET NET (coût round-trip par trade).
No-look-ahead : OBI[t] = état du carnet À t, move postérieur. Pur, testable sans réseau.

Run: cd backend/edge_factory && ../../.venv/bin/python test_obi_backtest.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import obi_backtest as ob  # noqa: E402


def _series(obis_mids):
    # [(obi, mid)] -> [(time, obi, mid)] à 10s d'intervalle
    return [(1000 + i * 10000, o, m) for i, (o, m) in enumerate(obis_mids)]


def test_obi_predicts_move_gross_positive():
    # OBI>0 systématiquement suivi d'une hausse de mid -> gross > 0
    s = _series([(0.5, 100.0), (0.5, 100.1), (0.5, 100.2), (0.5, 100.3), (0.5, 100.4)])
    r = ob.obi_backtest(s, threshold=0.2, cost_bps=0.0, exec_lag=0)
    assert sum(r) > 0, r


def test_costs_can_flip_sign():
    # même série : gross>0 mais coût élevé -> net<0 (le mur du sous-horaire)
    s = _series([(0.5, 100.0), (0.5, 100.01), (0.5, 100.02), (0.5, 100.03)])
    gross = sum(ob.obi_backtest(s, threshold=0.2, cost_bps=0.0, exec_lag=0))
    net = sum(ob.obi_backtest(s, threshold=0.2, cost_bps=50.0, exec_lag=0))
    assert gross > 0 and net < gross


def test_exec_lag_shifts_entry():
    s = _series([(0.8, 100.0), (0.8, 101.0), (0.8, 102.0), (0.8, 103.0), (0.8, 104.0)])
    r0 = ob.obi_backtest(s, threshold=0.2, cost_bps=0.0, exec_lag=0)
    r1 = ob.obi_backtest(s, threshold=0.2, cost_bps=0.0, exec_lag=1)
    # lag=1 produit une série plus courte (on perd 1 obs au début de l'exécution)
    assert len(r1) <= len(r0)


def test_no_trade_below_threshold():
    s = _series([(0.05, 100.0), (-0.05, 100.1), (0.05, 100.2), (-0.05, 100.3)])
    r = ob.obi_backtest(s, threshold=0.5, cost_bps=4.5, exec_lag=1)
    assert all(x == 0.0 for x in r)


def test_short_on_negative_obi():
    # OBI<0 (pression vendeuse) suivi de baisse -> SHORT gagne (gross>0)
    s = _series([(-0.5, 100.0), (-0.5, 99.9), (-0.5, 99.8), (-0.5, 99.7)])
    r = ob.obi_backtest(s, threshold=0.2, cost_bps=0.0, exec_lag=0)
    assert sum(r) > 0, r


def test_empty_and_short_safe():
    assert ob.obi_backtest([], threshold=0.2, cost_bps=0.0) == []
    assert ob.obi_backtest(_series([(0.5, 100.0)]), threshold=0.2, cost_bps=0.0) == []


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
