"""Tests known-answer pour metrics.py — métriques de perf pur-python.

Définitions canoniques (littérature quant) :
  - max_drawdown : max(peak - equity) sur l'equity cumulée des returns
  - sortino : mean / downside-deviation (vol des seuls returns < 0)
  - calmar : return annualisé / |maxDD|
  - profit_factor : somme gains / |somme pertes|
  - expectancy : moyenne des returns
Valeurs vérifiées à la main (known-answer, pas de dépendance au bruit).

Run: cd backend/edge_factory && ../../.venv/bin/python test_metrics.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import metrics as mt  # noqa: E402


def test_max_drawdown_known():
    # equity cumulée : +1, +2, 0, +1, -1  (returns 1,1,-2,1,-2)
    # peaks 1,2,2,2,2 ; dd max = 2-(-1) = 3
    assert abs(mt.max_drawdown([1, 1, -2, 1, -2]) - 3.0) < 1e-9


def test_max_drawdown_monotone_up_is_zero():
    assert mt.max_drawdown([0.5, 0.5, 0.5]) == 0.0


def test_profit_factor_known():
    # gains 3+1=4 ; pertes |-1-1|=2 ; PF=2.0
    assert abs(mt.profit_factor([3, -1, 1, -1]) - 2.0) < 1e-9


def test_profit_factor_no_losses_is_inf():
    assert mt.profit_factor([1, 2, 3]) == float("inf")


def test_expectancy_known():
    assert abs(mt.expectancy([1, -1, 2, -2, 5]) - 1.0) < 1e-9


def test_sortino_known():
    # returns symétriques [1,-1,1,-1] : mean=0 -> sortino=0
    assert abs(mt.sortino([1, -1, 1, -1])) < 1e-9


def test_sortino_positive_when_upside_dominates():
    r = [0.02, 0.03, -0.01, 0.025, -0.005]
    s = mt.sortino(r)
    assert s > 0
    # downside dev n'utilise QUE les returns négatifs -> sortino > sharpe ici
    assert s > mt.sharpe(r)


def test_sortino_no_downside_is_inf():
    assert mt.sortino([0.01, 0.02, 0.0]) == float("inf")


def test_calmar_known():
    # returns [1,1,-2,1,-2] : total=-1 ; maxDD=3 ; calmar = -1/3
    assert abs(mt.calmar([1, 1, -2, 1, -2], periods_per_year=len([1, 1, -2, 1, -2]))
               - (-1.0 / 3.0)) < 1e-9


def test_calmar_zero_dd_is_inf():
    assert mt.calmar([0.5, 0.5], periods_per_year=2) == float("inf")


def test_summary_returns_all_keys():
    r = [0.01, -0.02, 0.03, 0.01, -0.01, 0.02]
    s = mt.summary(r, periods_per_year=252)
    for k in ("sharpe", "sortino", "calmar", "max_drawdown",
              "profit_factor", "expectancy", "n"):
        assert k in s, k
    assert s["n"] == 6


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
