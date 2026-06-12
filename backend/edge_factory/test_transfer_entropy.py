"""Tests pour transfer_entropy.py — TE de Schreiber (causalité directionnelle).

TE(X→Y) = Σ p(y+,y,x)·log2[ p(y+|y,x) / p(y+|y) ] : information que le PASSÉ de X
apporte sur le FUTUR de Y au-delà du passé de Y seul. Non-paramétrique, ASYMÉTRIQUE
(≠ corrélation, qui est symétrique+linéaire → c'est l'erreur du lead-lag linéaire
réfuté). Discrétisation par bins quantiles. Effective TE = TE − moyenne(surrogates
shufflés) corrige le biais positif (recherche : worldscientific/arxiv 2506.16215).

Run: cd backend/edge_factory && ../../.venv/bin/python test_transfer_entropy.py
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import transfer_entropy as te  # noqa: E402


def test_symbolize_quantile_bins_balanced():
    xs = list(range(100))
    sym = te.symbolize(xs, n_bins=4)
    # 4 bins quantiles ~équilibrés sur 100 points : chaque symbole ~25 fois
    counts = [sym.count(b) for b in range(4)]
    assert all(20 <= c <= 30 for c in counts), counts


def test_te_zero_for_independent_series():
    rng = random.Random(0)
    x = [rng.gauss(0, 1) for _ in range(2000)]
    y = [rng.gauss(0, 1) for _ in range(2000)]
    # séries indépendantes -> effective TE ~0 (non significatif)
    out = te.effective_transfer_entropy(x, y, n_bins=3, n_surrogates=30, seed=1)
    assert abs(out["ete"]) < 0.02, out


def test_te_detects_directional_coupling():
    # construit X -> Y : Y_{t+1} copie X_t (+ bruit). TE(X->Y) >> TE(Y->X).
    rng = random.Random(2)
    n = 3000
    x = [rng.gauss(0, 1) for _ in range(n)]
    y = [0.0]
    for t in range(1, n):
        y.append(0.8 * x[t - 1] + 0.2 * rng.gauss(0, 1))  # Y suit le passé de X
    te_xy = te.transfer_entropy(x, y, n_bins=4)   # X cause Y
    te_yx = te.transfer_entropy(y, x, n_bins=4)   # Y cause X (faux)
    assert te_xy > te_yx, (te_xy, te_yx)
    assert te_xy > 0.05, te_xy


def test_effective_te_significant_for_coupled():
    rng = random.Random(3)
    n = 3000
    x = [rng.gauss(0, 1) for _ in range(n)]
    y = [0.0]
    for t in range(1, n):
        y.append(0.8 * x[t - 1] + 0.2 * rng.gauss(0, 1))
    out = te.effective_transfer_entropy(x, y, n_bins=4, n_surrogates=40, seed=5)
    assert out["significant"] is True, out
    assert out["ete"] > 0, out
    assert out["p_value"] < 0.05, out


def test_te_deterministic():
    rng = random.Random(9)
    x = [rng.gauss(0, 1) for _ in range(800)]
    y = [rng.gauss(0, 1) for _ in range(800)]
    a = te.effective_transfer_entropy(x, y, n_bins=3, n_surrogates=20, seed=7)
    b = te.effective_transfer_entropy(x, y, n_bins=3, n_surrogates=20, seed=7)
    assert a["ete"] == b["ete"] and a["p_value"] == b["p_value"]


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
