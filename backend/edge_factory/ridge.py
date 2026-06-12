#!/usr/bin/env python3
"""Régression ridge pur-python — modèle de base pour Numerai Signals.

Ridge = OLS + pénalité L2 : w = (XᵀX + λI)⁻¹ Xᵀy. Robuste à l'overfit (l'ennemi
n°1 sur Numerai : features corrélées, cible bruitée). Zéro numpy : produit matriciel
+ inversion Gauss-Jordan à la main. L'intercept n'est PAS pénalisé. Métrique de
scoring = corrélation de Spearman (rang), la métrique Numerai.
"""
from typing import Dict, List

Matrix = List[List[float]]


def _design(X: List[List[float]]) -> Matrix:
    """Ajoute la colonne d'intercept (1.0) en tête."""
    return [[1.0] + list(row) for row in X]


def _matmul_T(A: Matrix, B: Matrix) -> Matrix:
    """Aᵀ · B."""
    n, m, p = len(A), len(A[0]), len(B[0])
    out = [[0.0] * p for _ in range(m)]
    for k in range(n):
        ak = A[k]
        bk = B[k]
        for i in range(m):
            aki = ak[i]
            if aki:
                oi = out[i]
                for j in range(p):
                    oi[j] += aki * bk[j]
    return out


def _invert(M: Matrix) -> Matrix:
    """Inverse par Gauss-Jordan avec pivot partiel."""
    n = len(M)
    A = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(M)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(A[r][col]))
        if abs(A[piv][col]) < 1e-12:
            A[piv][col] += 1e-9  # régularisation numérique de secours
        A[col], A[piv] = A[piv], A[col]
        pivval = A[col][col]
        A[col] = [x / pivval for x in A[col]]
        for r in range(n):
            if r != col and A[r][col] != 0.0:
                factor = A[r][col]
                A[r] = [a - factor * b for a, b in zip(A[r], A[col])]
    return [row[n:] for row in A]


def fit(X: List[List[float]], y: List[float], lam: float = 1.0) -> Dict:
    """Ridge : w = (XᵀX + λI)⁻¹ Xᵀy. λ ne pénalise PAS l'intercept (I[0,0]=0)."""
    Xd = _design(X)
    m = len(Xd[0])
    XtX = _matmul_T(Xd, Xd)
    for i in range(1, m):  # pas l'intercept
        XtX[i][i] += lam
    Xty = _matmul_T(Xd, [[v] for v in y])
    w = _invert(XtX)
    weights = [sum(w[i][k] * Xty[k][0] for k in range(m)) for i in range(m)]
    return {"weights": weights, "lam": lam}


def predict(model: Dict, X: List[List[float]]) -> List[float]:
    w = model["weights"]
    return [w[0] + sum(w[i + 1] * v for i, v in enumerate(row)) for row in X]


def _rank(xs: List[float]) -> List[float]:
    """Rangs avec ties = rang MOYEN (correct pour valeurs répétées, ex constante)."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0  # rang moyen du groupe d'égalités [i..j]
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(a: List[float], b: List[float]) -> float:
    """Corrélation de rang (Spearman) = métrique de scoring Numerai."""
    ra, rb = _rank(a), _rank(b)
    n = len(ra)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((ra[i] - ma) * (rb[i] - mb) for i in range(n))
    da = sum((ra[i] - ma) ** 2 for i in range(n)) ** 0.5
    db = sum((rb[i] - mb) ** 2 for i in range(n)) ** 0.5
    if da == 0 or db == 0:
        return 0.0
    return num / (da * db)


# ── Chemins RAPIDES (numpy) pour la grosse data Numerai (4.3M lignes) ───────────
# Mathématiquement identiques au pur-python (testés par équivalence). numpy déjà
# dispo (dépendance pandas). Le pur-python reste la référence + tourne sans numpy.

def predict_fast(model: dict, X) -> List[float]:
    import numpy as np
    w = np.asarray(model["weights"], dtype=float)
    Xa = np.asarray(X, dtype=float)
    return list(w[0] + Xa @ w[1:])


def _rank_fast(x):
    import numpy as np
    a = np.asarray(x, dtype=float)
    order = a.argsort(kind="mergesort")
    ranks = np.empty(len(a), dtype=float)
    ranks[order] = np.arange(len(a), dtype=float)
    # rangs moyens pour les ties (cohérent avec _rank pur-python)
    _, inv, counts = np.unique(a, return_inverse=True, return_counts=True)
    cum = np.concatenate([[0], counts.cumsum()])
    avg = (cum[:-1] + cum[1:] - 1) / 2.0
    return avg[inv]


def spearman_fast(a, b) -> float:
    import numpy as np
    ra, rb = _rank_fast(a), _rank_fast(b)
    ra = ra - ra.mean()
    rb = rb - rb.mean()
    da = float(np.sqrt((ra * ra).sum()))
    db = float(np.sqrt((rb * rb).sum()))
    if da == 0 or db == 0:
        return 0.0
    return float((ra * rb).sum() / (da * db))
