"""Tests pour neutralize.py — neutralisation de features Numerai (résidualisation).

Méthode (doc Numerai FNC) : par era, régresser les prédictions sur les features et
soustraire la composante linéaire → pred_neutre = pred − prop·F·(F⁺·pred). Isole la
part ORTHOGONALE aux features (réduit feature-risk, booste le Sharpe de corr payé).
Propriété clé testée : à prop=1, corr(pred_neutre, chaque feature) ≈ 0.

Run: cd backend/edge_factory && ../../.venv/bin/python test_neutralize.py
"""
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import neutralize as nz  # noqa: E402
import ridge as rg  # noqa: E402


def test_full_neutralization_kills_feature_correlation():
    # pred = 2*f1 + bruit -> après neutralisation prop=1, corr(pred_neutre, f1) ~0
    rng = random.Random(0)
    n = 200
    f1 = [rng.gauss(0, 1) for _ in range(n)]
    f2 = [rng.gauss(0, 1) for _ in range(n)]
    feats = [[a, b] for a, b in zip(f1, f2)]
    pred = [2 * a + 0.5 * rng.gauss(0, 1) for a in f1]
    neutral = nz.neutralize(pred, feats, proportion=1.0)
    # corrélation résiduelle avec f1 doit être quasi nulle
    assert abs(rg.spearman(neutral, f1)) < 0.15, rg.spearman(neutral, f1)


def test_zero_proportion_is_identity():
    rng = random.Random(1)
    feats = [[rng.gauss(0, 1), rng.gauss(0, 1)] for _ in range(50)]
    pred = [rng.gauss(0, 1) for _ in range(50)]
    out = nz.neutralize(pred, feats, proportion=0.0)
    for a, b in zip(out, pred):
        assert abs(a - b) < 1e-9


def test_partial_proportion_reduces_but_keeps_some():
    rng = random.Random(2)
    n = 200
    f1 = [rng.gauss(0, 1) for _ in range(n)]
    feats = [[a] for a in f1]
    pred = [3 * a + 0.8 * rng.gauss(0, 1) for a in f1]  # forte corr MAIS bruitée
    raw = abs(rg.spearman(pred, f1))
    half = abs(rg.spearman(nz.neutralize(pred, feats, 0.5), f1))
    full = abs(rg.spearman(nz.neutralize(pred, feats, 1.0), f1))
    # neutralisation monotone : full < half < brute
    assert full < half < raw, (full, half, raw)


def test_neutralize_by_era_groups_independent():
    # 2 eras aux échelles TRÈS différentes : neutraliser globalement ≠ par-era.
    # Par-era, chaque groupe est résidualisé sur SES propres features uniquement.
    feats = [[1.0], [2.0], [3.0]] + [[10.0], [20.0], [30.0]]
    pred = [1.0, 2.0, 3.0, 5.0, 6.0, 7.0]
    eras = ["A", "A", "A", "B", "B", "B"]
    by_era = nz.neutralize_by_era(pred, feats, eras, proportion=1.0)
    glob = nz.neutralize(pred, feats, proportion=1.0)
    assert len(by_era) == 6
    # le résultat par-era diffère du global (preuve que les eras sont indépendants)
    assert any(abs(by_era[i] - glob[i]) > 1e-6 for i in range(6))
    # la neutralisation réduit l'AMPLITUDE du résiduel vs la prédiction brute
    var_raw = statistics.pvariance(pred[:3])
    var_neut = statistics.pvariance(by_era[:3])
    assert var_neut < var_raw, (var_neut, var_raw)


def test_numpy_path_matches_pure_python():
    # le chemin numpy (prod, grosse data) doit donner le MÊME résultat que pur-python
    rng = random.Random(11)
    n, m = 300, 6
    feats = [[rng.gauss(0, 1) for _ in range(m)] for _ in range(n)]
    pred = [rng.gauss(0, 1) for _ in range(n)]
    pure = nz.neutralize(pred, feats, proportion=0.5)
    fast = nz.neutralize_fast(pred, feats, proportion=0.5)
    for a, b in zip(pure, fast):
        assert abs(a - b) < 1e-6, (a, b)


def test_preserves_length_and_finite():
    rng = random.Random(3)
    feats = [[rng.gauss(0, 1) for _ in range(5)] for _ in range(80)]
    pred = [rng.gauss(0, 1) for _ in range(80)]
    out = nz.neutralize(pred, feats, proportion=0.5)
    assert len(out) == 80
    assert all(x == x for x in out)  # pas de NaN


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
