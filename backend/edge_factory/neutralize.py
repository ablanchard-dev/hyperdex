#!/usr/bin/env python3
"""Neutralisation de features Numerai — résidualisation linéaire pur-python.

Méthode (doc Numerai FNC) : par era, régresser les prédictions sur les features et
soustraire la composante linéaire → on garde la part ORTHOGONALE aux features.

  pred_neutre = pred − proportion · F · (F⁺ · pred)

où F⁺ = (FᵀF + εI)⁻¹ Fᵀ (pseudo-inverse régularisée). proportion ∈ [0,1] :
0.3–0.5 = sweet spot (trop → la corr utile s'effondre aussi). Réduit le feature-risk
→ augmente le Sharpe de corr payé par Numerai. Réutilise l'algèbre de ridge.py.
"""
from typing import List

import ridge as rg

RIDGE_EPS = 1e-6


def _fit_linear_projection(feats: List[List[float]], y: List[float]) -> List[float]:
    """Retourne F·w où w = (FᵀF + εI)⁻¹ Fᵀy (composante linéaire de y sur F).
    Centre y (la neutralisation porte sur la variation, pas le niveau)."""
    n = len(feats)
    if n == 0:
        return []
    m = len(feats[0])
    # FᵀF + εI  (régularisation pour l'inversibilité, features colinéaires Numerai)
    FtF = rg._matmul_T(feats, feats)
    for i in range(m):
        FtF[i][i] += RIDGE_EPS
    Fty = rg._matmul_T(feats, [[v] for v in y])
    inv = rg._invert(FtF)
    w = [sum(inv[i][k] * Fty[k][0] for k in range(m)) for i in range(m)]
    return [sum(w[j] * row[j] for j in range(m)) for row in feats]


def neutralize(pred: List[float], feats: List[List[float]],
               proportion: float = 0.5) -> List[float]:
    """pred_neutre = pred − proportion · proj_lin(pred sur feats). Centre pred d'abord
    (résidualisation de la variation)."""
    n = len(pred)
    if n == 0 or proportion == 0.0:
        return list(pred)
    mean = sum(pred) / n
    centered = [p - mean for p in pred]
    proj = _fit_linear_projection(feats, centered)
    return [pred[i] - proportion * proj[i] for i in range(n)]


def neutralize_by_era(pred: List[float], feats: List[List[float]],
                      eras: List, proportion: float = 0.5) -> List[float]:
    """Neutralise INDÉPENDAMMENT par era (chaque era = une régression séparée).
    C'est la forme correcte Numerai (le cross-section est par date)."""
    out = [0.0] * len(pred)
    groups: dict = {}
    for i, e in enumerate(eras):
        groups.setdefault(e, []).append(i)
    for e, idx in groups.items():
        sub_pred = [pred[i] for i in idx]
        sub_feat = [feats[i] for i in idx]
        neut = neutralize(sub_pred, sub_feat, proportion)
        for k, i in enumerate(idx):
            out[i] = neut[k]
    return out


# ── Chemin RAPIDE (numpy) : prod sur grosse data Numerai (4.5M lignes) ──────────
# Identique mathématiquement au pur-python (testé par équivalence), 100-1000× plus
# rapide. numpy est déjà dispo (dépendance pandas pour lire les parquet Numerai).

def neutralize_fast(pred, feats, proportion: float = 0.5):
    """Version numpy de neutralize() — même math, vectorisée."""
    import numpy as np
    p = np.asarray(pred, dtype=float)
    F = np.asarray(feats, dtype=float)
    if p.size == 0 or proportion == 0.0:
        return list(p)
    centered = p - p.mean()
    FtF = F.T @ F + RIDGE_EPS * np.eye(F.shape[1])
    w = np.linalg.solve(FtF, F.T @ centered)
    proj = F @ w
    return list(p - proportion * proj)


def neutralize_by_era_fast(pred, feats, eras, proportion: float = 0.5):
    """Version numpy de neutralize_by_era — projection calculée une fois par era.
    Retourne la projection brute aussi (réutilisable pour balayer la proportion)."""
    import numpy as np
    p = np.asarray(pred, dtype=float)
    F = np.asarray(feats, dtype=float)
    eras_arr = np.asarray(eras)
    proj = np.zeros_like(p)
    for e in np.unique(eras_arr):
        mask = eras_arr == e
        Fe = F[mask]
        pe = p[mask]
        centered = pe - pe.mean()
        FtF = Fe.T @ Fe + RIDGE_EPS * np.eye(Fe.shape[1])
        w = np.linalg.solve(FtF, Fe.T @ centered)
        proj[mask] = Fe @ w
    return list(p - proportion * proj), proj  # proj réutilisable cross-proportion
