#!/usr/bin/env python3
"""Gradient boosting d'arbres de régression — non-linéarité pour Numerai (numpy).

Le ridge linéaire plafonne (corr 0.0003) : il ne capte pas les INTERACTIONS entre
features. GBM = somme d'arbres CART peu profonds, chacun fittant le résidu (gradient
de la MSE) du précédent → capture non-linéarités + interactions. Vectorisé numpy
(80k×22 en temps raisonnable). Arbres volontairement peu profonds (depth 3-5) +
learning rate faible = anti-overfit (l'ennemi Numerai).

Réf : Friedman 2001 (gradient boosting), depth/lr/n_trees = hyperparams standard.
"""
from typing import Dict, List

import numpy as np


def _best_split(X, y):
    """Meilleur (feature, seuil) minimisant la SSE des deux côtés. VECTORISÉ numpy :
    pour chaque feature, tous les seuils évalués en une passe (cumsum) sans boucle
    Python sur les n lignes → ~100× plus rapide que la version naïve."""
    n, m = X.shape
    best = None
    best_sse = float(np.sum((y - y.mean()) ** 2))
    for j in range(m):
        col = X[:, j]
        order = np.argsort(col, kind="mergesort")
        cs = col[order]
        ys = y[order]
        csum = np.cumsum(ys)
        csum2 = np.cumsum(ys * ys)
        total = csum[-1]
        total2 = csum2[-1]
        # positions i=1..n-1 : gauche=[0,i), droite=[i,n). Tout vectorisé.
        i = np.arange(1, n)
        nl = i.astype(float)
        nr = (n - i).astype(float)
        sl = csum[:-1]
        sr = total - sl
        sse_l = csum2[:-1] - sl * sl / nl
        sse_r = (total2 - csum2[:-1]) - sr * sr / nr
        sse = sse_l + sse_r
        # interdire les splits entre valeurs égales (seuil dégénéré)
        valid = cs[1:] != cs[:-1]
        sse = np.where(valid, sse, np.inf)
        k = int(np.argmin(sse))
        if sse[k] < best_sse:
            best_sse = float(sse[k])
            thr = (cs[k + 1] + cs[k]) / 2.0
            best = (j, float(thr))
    return best


def fit_tree(X, y, max_depth=3, min_leaf=5) -> Dict:
    """Arbre de régression CART (récursif). Feuille = moyenne de y."""
    Xa = np.asarray(X, dtype=float)
    ya = np.asarray(y, dtype=float)

    def build(idx, depth):
        yi = ya[idx]
        node = {"leaf": True, "value": float(yi.mean())}
        if depth >= max_depth or len(idx) < 2 * min_leaf or yi.std() < 1e-12:
            return node
        split = _best_split(Xa[idx], yi)
        if split is None:
            return node
        j, thr = split
        left = idx[Xa[idx, j] <= thr]
        right = idx[Xa[idx, j] > thr]
        if len(left) < min_leaf or len(right) < min_leaf:
            return node
        return {"leaf": False, "feature": j, "thr": thr,
                "left": build(left, depth + 1), "right": build(right, depth + 1)}

    return build(np.arange(len(ya)), 0)


def predict_tree(tree, X) -> List[float]:
    Xa = np.asarray(X, dtype=float)
    out = np.empty(len(Xa))
    for i in range(len(Xa)):
        node = tree
        while not node["leaf"]:
            node = node["left"] if Xa[i, node["feature"]] <= node["thr"] else node["right"]
        out[i] = node["value"]
    return list(out)


def fit(X, y, n_trees=30, max_depth=3, lr=0.1, min_leaf=5) -> Dict:
    """Gradient boosting MSE : init = moyenne, puis n_trees arbres sur le résidu."""
    Xa = np.asarray(X, dtype=float)
    ya = np.asarray(y, dtype=float)
    base = float(ya.mean())
    pred = np.full(len(ya), base)
    trees = []
    for _ in range(n_trees):
        residual = ya - pred
        tree = fit_tree(Xa, residual, max_depth=max_depth, min_leaf=min_leaf)
        update = np.asarray(predict_tree(tree, Xa))
        pred = pred + lr * update
        trees.append(tree)
    return {"base": base, "lr": lr, "trees": trees}


def predict(model, X) -> List[float]:
    Xa = np.asarray(X, dtype=float)
    pred = np.full(len(Xa), model["base"])
    lr = model["lr"]
    for tree in model["trees"]:
        pred = pred + lr * np.asarray(predict_tree(tree, Xa))
    return list(pred)
