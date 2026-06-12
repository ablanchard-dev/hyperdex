"""Tests known-answer pour ridge.py — régression ridge pur-python (Numerai modeling).

Ridge = moindres carrés + pénalité L2 (lambda) → robuste à l'overfit, le bon défaut
pour Numerai (beaucoup de features corrélées, cible bruitée). Résolu par équations
normales : w = (XᵀX + λI)⁻¹ Xᵀy. Pur-python (zéro numpy) : inversion par Gauss-Jordan.
On valide sur des cas à réponse connue.

Run: cd backend/edge_factory && ../../.venv/bin/python test_ridge.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ridge as rg  # noqa: E402


def test_recovers_exact_linear_no_noise_zero_lambda():
    # y = 2*x1 + 3*x2 (+ intercept 1) exact -> ridge λ=0 doit retrouver les poids
    X = [[1.0, 1.0], [2.0, 0.0], [0.0, 2.0], [3.0, 1.0], [1.0, 4.0]]
    y = [1 + 2 * a + 3 * b for a, b in X]
    model = rg.fit(X, y, lam=0.0)
    pred = rg.predict(model, X)
    for p, t in zip(pred, y):
        assert abs(p - t) < 1e-6, (p, t)


def test_lambda_shrinks_weights():
    X = [[1.0, 1.0], [2.0, 0.0], [0.0, 2.0], [3.0, 1.0], [1.0, 4.0], [2.0, 2.0]]
    y = [1 + 2 * a + 3 * b for a, b in X]
    w0 = rg.fit(X, y, lam=0.0)["weights"]
    w1 = rg.fit(X, y, lam=10.0)["weights"]
    # la pénalité L2 réduit la norme des poids (hors intercept)
    assert sum(w**2 for w in w1[1:]) < sum(w**2 for w in w0[1:])


def test_predict_constant_target():
    X = [[1.0], [2.0], [3.0], [4.0]]
    y = [5.0, 5.0, 5.0, 5.0]
    model = rg.fit(X, y, lam=0.1)
    pred = rg.predict(model, X)
    for p in pred:
        assert abs(p - 5.0) < 0.5  # ~constant


def test_correlation_metric():
    # corrélation de Spearman (rang) = métrique Numerai
    a = [1.0, 2.0, 3.0, 4.0, 5.0]
    b = [2.0, 4.0, 6.0, 8.0, 10.0]  # monotone croissant -> corr rang = 1
    assert abs(rg.spearman(a, b) - 1.0) < 1e-9
    c = [5.0, 4.0, 3.0, 2.0, 1.0]   # monotone décroissant -> -1
    assert abs(rg.spearman(a, c) + 1.0) < 1e-9


def test_correlation_zero_for_independent():
    a = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    b = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0]  # constant -> corr définie 0
    assert rg.spearman(a, b) == 0.0


def test_predict_fast_matches_pure():
    # chemin numpy (grosse data) == pur-python
    import random
    rng = random.Random(5)
    X = [[rng.gauss(0, 1) for _ in range(8)] for _ in range(500)]
    y = [rng.gauss(0, 1) for _ in range(500)]
    model = rg.fit(X, y, lam=1.0)
    pure = rg.predict(model, X)
    fast = rg.predict_fast(model, X)
    for a, b in zip(pure, fast):
        assert abs(a - b) < 1e-9, (a, b)


def test_spearman_fast_matches_pure():
    import random
    rng = random.Random(6)
    a = [rng.gauss(0, 1) for _ in range(400)]
    b = [0.5 * a[i] + rng.gauss(0, 1) for i in range(400)]
    assert abs(rg.spearman(a, b) - rg.spearman_fast(a, b)) < 1e-9


def test_fit_predict_shapes():
    X = [[float(i), float(i * 2)] for i in range(20)]
    y = [float(i) for i in range(20)]
    model = rg.fit(X, y, lam=1.0)
    assert len(model["weights"]) == 3  # intercept + 2 features
    assert len(rg.predict(model, X)) == 20


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
