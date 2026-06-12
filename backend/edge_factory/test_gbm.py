"""Tests pour gbm.py — gradient boosting d'arbres de régression (numpy).

Le levier Numerai identifié = NON-LINÉARITÉ (le ridge linéaire plafonne à corr 0.0003).
GBM = somme d'arbres peu profonds, chacun fittant le résidu du précédent (gradient
de la perte MSE = résidu). Capte interactions + non-linéarités. numpy pour la vitesse
(80k×22), avec known-answer sur des cas vérifiables.

Run: cd backend/edge_factory && ../../.venv/bin/python test_gbm.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gbm  # noqa: E402


def test_tree_learns_step_function():
    # cible en marche d'escalier : x<0.5 -> 0, x>=0.5 -> 1. Un arbre depth=1 (1 split)
    # doit la capturer parfaitement (le linéaire ne peut PAS).
    X = [[x / 100] for x in range(100)]
    y = [0.0 if x < 50 else 1.0 for x in range(100)]
    tree = gbm.fit_tree(X, y, max_depth=1, min_leaf=1)
    pred = gbm.predict_tree(tree, X)
    for p, t in zip(pred, y):
        assert abs(p - t) < 0.05, (p, t)


def test_tree_captures_interaction_with_marginal():
    # interaction a*b AVEC composante marginale (réaliste : la vraie data n'est pas
    # du XOR pur symétrique, qui est le pire cas pathologique de CART greedy).
    # Un arbre depth=3 doit expliquer l'essentiel de la variance.
    import random
    rng = random.Random(7)
    X = [[rng.choice([0.0, 1.0]), rng.choice([0.0, 1.0])] for _ in range(400)]
    y = [0.6 * a + a * b + 0.05 * rng.gauss(0, 1) for a, b in X]
    tree = gbm.fit_tree(X, y, max_depth=3, min_leaf=2)
    pred = gbm.predict_tree(tree, X)
    import numpy as np
    mse = np.mean([(p - t) ** 2 for p, t in zip(pred, y)])
    assert mse < 0.3 * np.var(y), (mse, np.var(y))


def test_gbm_reduces_error_vs_single_tree():
    import random
    rng = random.Random(0)
    X = [[rng.gauss(0, 1), rng.gauss(0, 1)] for _ in range(300)]
    # interaction a*b AVEC marginal (réaliste) -> GBM doit expliquer >50% variance
    y = [0.5 * a + a * b + 0.1 * rng.gauss(0, 1) for a, b in X]
    model = gbm.fit(X, y, n_trees=30, max_depth=3, lr=0.2, min_leaf=5)
    pred = gbm.predict(model, X)
    mse = sum((p - t) ** 2 for p, t in zip(pred, y)) / len(y)
    base = sum((sum(y) / len(y) - t) ** 2 for t in y) / len(y)  # prédire la moyenne
    assert mse < 0.5 * base, (mse, base)  # GBM explique >50% de la variance


def test_gbm_predict_shape_and_finite():
    import random
    rng = random.Random(1)
    X = [[rng.gauss(0, 1) for _ in range(5)] for _ in range(100)]
    y = [rng.gauss(0, 1) for _ in range(100)]
    model = gbm.fit(X, y, n_trees=10, max_depth=2, lr=0.1, min_leaf=3)
    pred = gbm.predict(model, X)
    assert len(pred) == 100
    assert all(p == p for p in pred)  # pas de NaN


def test_gbm_learning_rate_shrinks_step():
    # lr plus petit -> chaque arbre contribue moins -> prédiction plus proche du base
    import random
    rng = random.Random(2)
    X = [[rng.gauss(0, 1)] for _ in range(200)]
    y = [3 * row[0] for row in X]
    p_lo = gbm.predict(gbm.fit(X, y, n_trees=5, max_depth=2, lr=0.05, min_leaf=3), X)
    p_hi = gbm.predict(gbm.fit(X, y, n_trees=5, max_depth=2, lr=0.5, min_leaf=3), X)
    base = sum(y) / len(y)
    # lr élevé s'éloigne plus du base (apprend plus vite)
    dev_lo = sum(abs(p - base) for p in p_lo)
    dev_hi = sum(abs(p - base) for p in p_hi)
    assert dev_hi > dev_lo


def test_gbm_deterministic():
    import random
    rng = random.Random(3)
    X = [[rng.gauss(0, 1) for _ in range(3)] for _ in range(80)]
    y = [rng.gauss(0, 1) for _ in range(80)]
    a = gbm.predict(gbm.fit(X, y, n_trees=8, max_depth=2, lr=0.1, min_leaf=3), X)
    b = gbm.predict(gbm.fit(X, y, n_trees=8, max_depth=2, lr=0.1, min_leaf=3), X)
    assert a == b


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
