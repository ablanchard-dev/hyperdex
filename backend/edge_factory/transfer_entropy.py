#!/usr/bin/env python3
"""Transfer entropy de Schreiber — causalité directionnelle, pur-python.

TE(X→Y) = Σ p(y+,y,x)·log2[ p(y+|y,x) / p(y+|y) ]

Mesure l'information que le PASSÉ de X apporte sur le FUTUR de Y au-delà du passé de
Y. Non-paramétrique, ASYMÉTRIQUE → capte la causalité directionnelle non-linéaire
(≠ corrélation symétrique+linéaire = l'erreur du lead-lag linéaire réfuté).

Discrétisation : bins quantiles (standard finance). Biais positif sur comptages
clairsemés → effective TE = TE − moyenne(TE sur surrogates de X shufflés), avec
p-value = fraction des surrogates ≥ TE observé. Réfs : Schreiber 2000 ;
worldscientific S0219477513500193 (binning quantile) ; arxiv 2506.16215 (finite data).
"""
import math
import random
from typing import Dict, List

LOG2 = math.log(2)


def symbolize(xs: List[float], n_bins: int) -> List[int]:
    """Discrétise par quantiles empiriques → symboles 0..n_bins-1 (~équilibrés)."""
    srt = sorted(xs)
    n = len(srt)
    edges = [srt[min(n - 1, (k * n) // n_bins)] for k in range(1, n_bins)]
    out = []
    for v in xs:
        b = 0
        while b < len(edges) and v > edges[b]:
            b += 1
        out.append(b)
    return out


def _te_from_symbols(sx: List[int], sy: List[int]) -> float:
    """TE(X→Y) sur séries déjà symbolisées, embedding 1 (y+,y,x)."""
    n = len(sy)
    p_yyx: Dict[tuple, int] = {}   # (y+, y, x)
    p_yy: Dict[tuple, int] = {}    # (y+, y)
    p_yx: Dict[tuple, int] = {}    # (y, x)
    p_y: Dict[int, int] = {}       # (y)
    total = 0
    for t in range(1, n):
        yp, y, x = sy[t], sy[t - 1], sx[t - 1]
        p_yyx[(yp, y, x)] = p_yyx.get((yp, y, x), 0) + 1
        p_yy[(yp, y)] = p_yy.get((yp, y), 0) + 1
        p_yx[(y, x)] = p_yx.get((y, x), 0) + 1
        p_y[y] = p_y.get(y, 0) + 1
        total += 1
    te = 0.0
    for (yp, y, x), c in p_yyx.items():
        p_joint = c / total
        # p(y+|y,x) = c / p_yx(y,x) ; p(y+|y) = p_yy(y+,y) / p_y(y)
        cond_yx = c / p_yx[(y, x)]
        cond_y = p_yy[(yp, y)] / p_y[y]
        if cond_yx > 0 and cond_y > 0:
            te += p_joint * math.log(cond_yx / cond_y) / LOG2
    return te


def transfer_entropy(x: List[float], y: List[float], n_bins: int = 4) -> float:
    """TE(X→Y) en bits. Discrétise puis applique Schreiber."""
    m = min(len(x), len(y))
    sx = symbolize(x[:m], n_bins)
    sy = symbolize(y[:m], n_bins)
    return _te_from_symbols(sx, sy)


def effective_transfer_entropy(x: List[float], y: List[float], n_bins: int = 4,
                               n_surrogates: int = 50, seed: int = 0) -> Dict:
    """ETE = TE − mean(TE surrogates), p = fraction surrogates ≥ TE observé.

    Surrogates : on shuffle le PASSÉ de X (détruit le couplage temporel X→Y mais
    préserve la distribution marginale) → null du biais d'estimation."""
    m = min(len(x), len(y))
    sx = symbolize(x[:m], n_bins)
    sy = symbolize(y[:m], n_bins)
    te_obs = _te_from_symbols(sx, sy)
    rng = random.Random(seed)
    surro = []
    for _ in range(n_surrogates):
        shuffled = sx[:]
        rng.shuffle(shuffled)
        surro.append(_te_from_symbols(shuffled, sy))
    mean_surro = sum(surro) / len(surro) if surro else 0.0
    ge = sum(1 for s in surro if s >= te_obs)
    p_value = ge / len(surro) if surro else 1.0
    return {
        "te": te_obs,
        "ete": te_obs - mean_surro,
        "mean_surrogate": mean_surro,
        "p_value": p_value,
        "significant": p_value < 0.05 and (te_obs - mean_surro) > 0,
        "n_bins": n_bins,
    }
