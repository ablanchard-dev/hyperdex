"""Tests known-answer pour _dsr_pbo.py (DSR + PBO/CSCV, Lopez de Prado).

Run: cd backend/scripts/p2 && ../../../.venv/bin/python -m pytest test_dsr_pbo.py -q
ou : ../../../.venv/bin/python test_dsr_pbo.py
"""
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _dsr_pbo as d  # noqa: E402


# ---------- primitives ----------
def test_norm_cdf_ppf_roundtrip():
    assert abs(d._norm_cdf(0.0) - 0.5) < 1e-9
    assert abs(d._norm_ppf(0.5) - 0.0) < 1e-9
    for p in (0.1, 0.3, 0.975):
        assert abs(d._norm_cdf(d._norm_ppf(p)) - p) < 1e-6


def test_sharpe_known():
    # returns [1,2,3] : mean=2, stdev échantillon=1 -> sharpe=2
    assert abs(d._sharpe([1.0, 2.0, 3.0]) - 2.0) < 1e-9
    # variance nulle -> 0 (pas de division par zéro)
    assert d._sharpe([5.0, 5.0, 5.0]) == 0.0
    assert d._sharpe([1.0]) == 0.0


def test_skew_symmetric_zero():
    # série symétrique -> skew ~ 0
    assert abs(d._skew([-2.0, -1.0, 0.0, 1.0, 2.0])) < 1e-9
    # série à queue droite -> skew > 0
    assert d._skew([0.0, 0.0, 0.0, 0.0, 10.0]) > 0


def test_kurtosis_non_excess():
    # convention NON-excess (normale ~ 3). Données plates symétriques -> bas.
    # pour [-1,-1,1,1] : m4=1, var=1 -> kurt=1.0 (platykurtique)
    assert abs(d._kurtosis([-1.0, -1.0, 1.0, 1.0]) - 1.0) < 1e-9


# ---------- PSR ----------
def test_psr_at_benchmark_is_half():
    # sr == benchmark -> PSR = Phi(0) = 0.5
    assert abs(d.psr(1.0, 1.0, T=100, skew=0.0, kurt=3.0) - 0.5) < 1e-9


def test_psr_monotone_in_sr():
    lo = d.psr(0.5, 0.0, T=100, skew=0.0, kurt=3.0)
    hi = d.psr(1.5, 0.0, T=100, skew=0.0, kurt=3.0)
    assert 0.5 < lo < hi <= 1.0


# ---------- DSR / expected max sharpe ----------
def test_expected_max_sharpe_grows_with_trials():
    v = 0.25  # variance des Sharpe entre trials
    sr0_few = d.expected_max_sharpe(v, n_trials=10)
    sr0_many = d.expected_max_sharpe(v, n_trials=1000)
    assert 0 < sr0_few < sr0_many  # plus on teste, plus le max attendu sous H0 monte


def test_deflated_sharpe_half_when_sr_equals_sr0():
    v, nt, T = 0.25, 100, 250
    sr0 = d.expected_max_sharpe(v, nt)
    dsr = d.deflated_sharpe(sr0, T=T, skew=0.0, kurt=3.0,
                            sr_variance=v, n_trials=nt)
    assert abs(dsr - 0.5) < 1e-6


def test_deflated_sharpe_drops_with_more_trials():
    # même Sharpe observé, mais plus de trials -> DSR plus bas (plus dur à croire)
    sr, v, T = 1.0, 0.25, 250
    dsr_few = d.deflated_sharpe(sr, T, 0.0, 3.0, v, n_trials=10)
    dsr_many = d.deflated_sharpe(sr, T, 0.0, 3.0, v, n_trials=5000)
    assert dsr_few > dsr_many


# ---------- PBO / CSCV ----------
def _matrix_dominant(T, N, seed=0):
    """Stratégie 0 domine chaque période (mean élevé), bruit pour var>0."""
    rng = random.Random(seed)
    rows = []
    for _ in range(T):
        row = [1.0 + rng.gauss(0, 0.05)]            # strat 0 : mean ~1
        row += [rng.gauss(0, 0.05) for _ in range(N - 1)]  # autres : mean ~0
        rows.append(row)
    return rows


def test_pbo_dominant_strategy_is_zero():
    rows = _matrix_dominant(T=80, N=8, seed=1)
    pbo, logits = d.pbo_cscv(rows, S=8)
    assert len(logits) == math.comb(8, 4)
    assert pbo == 0.0  # la meilleure IS est toujours la meilleure OOS


def test_pbo_pure_noise_near_half():
    rng = random.Random(42)
    T, N = 120, 10
    rows = [[rng.gauss(0, 1) for _ in range(N)] for _ in range(T)]
    pbo, _ = d.pbo_cscv(rows, S=8)
    assert 0.30 <= pbo <= 0.70  # pas d'edge réel -> overfit ~ coin flip


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
    print(f"\n{len(fns)-fails}/{len(fns)} passed")
    sys.exit(1 if fails else 0)
