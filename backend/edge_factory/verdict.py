#!/usr/bin/env python3
"""Agrégateur de verdict du CRITIC — le juge incorruptible en UN appel.

Un edge ne PASSE que s'il survit à TOUS les gates :
  1. beta-neutral  : vrai alpha résiduel (pas du beta déguisé)         [critic.py]
  2. DSR > seuil   : robuste au multiple-testing, déflaté par n_trials [_dsr_pbo]
  3. PBO < seuil   : pas d'overfit en CSCV (si matrice fournie)        [_dsr_pbo]

Note : les returns passés doivent être ceux de la période TEST/OOS (le split
temporel est la responsabilité de l'appelant / de l'Experiment Agent). En Phase 2
on consolide _dsr_pbo dans edge_factory ; pour l'instant import par chemin.
"""
import os
import sys

import critic as _critic  # même package

# _dsr_pbo vit dans scripts/p2 (CLI standalone) — import par chemin en Phase 1
_P2 = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "scripts", "p2"))
if _P2 not in sys.path:
    sys.path.insert(0, _P2)
import _dsr_pbo as _stats  # noqa: E402

_PERM_P_MAX = 0.05  # seuil significativité permutation (intouchable)


def evaluate_edge(strat_returns, bench_returns, n_trials, sr_variance,
                  pbo_matrix=None, pbo_S=16, dsr_min=0.95, pbo_max=0.2,
                  t_min=3.0, permutation=None):
    """Verdict agrégé. Retourne pass/reasons/gates chiffrés.

    n_trials, sr_variance = fournis par le processus de recherche (largeur du
    multiple-testing + variance des Sharpe entre essais) → déflation DSR.

    permutation (optionnel) = résultat de permutation.permutation_test (dict avec
    'p_value'/'significant'), 4e gate : rejette le data-mining si p ≥ 0.05. Absent
    → gate ignorée (rétro-compatible).
    """
    reasons = []

    # gate 1 : beta-neutral (alpha résiduel réel) — raison précise (beta_deguise / weak_alpha)
    beta = _critic.beta_neutral_verdict(strat_returns, bench_returns, t_min=t_min)
    if not beta["pass"]:
        reasons.append(beta["reason"])

    # gate 2 : DSR (overfit / multiple-testing)
    sr = _stats._sharpe(strat_returns)
    skew = _stats._skew(strat_returns)
    kurt = _stats._kurtosis(strat_returns)
    dsr = _stats.deflated_sharpe(sr, T=len(strat_returns), skew=skew, kurt=kurt,
                                 sr_variance=sr_variance, n_trials=n_trials)
    if not (dsr > dsr_min):
        reasons.append("dsr")

    # gate 3 : PBO (CSCV) — optionnel
    pbo = None
    if pbo_matrix is not None:
        pbo, _ = _stats.pbo_cscv(pbo_matrix, S=pbo_S)
        if not (pbo < pbo_max):
            reasons.append("pbo")

    # gate 4 : permutation / block-bootstrap — optionnel (tueur de data-mining)
    perm_p = None
    if permutation is not None:
        perm_p = permutation["p_value"]
        if not permutation.get("significant", perm_p < _PERM_P_MAX):
            reasons.append("permutation")

    # gate 5 : convexité / tail — tue le short-vol déguisé (toujours actif, V3)
    conv = _critic.convexity_verdict(strat_returns, bench_returns)
    if not conv["pass"]:
        reasons.append("convexity_risk")

    return {
        "pass": len(reasons) == 0,
        "reasons": reasons,
        "gates": {
            "beta_neutral": beta,
            "dsr": round(dsr, 4),
            "sharpe": round(sr, 4),
            "pbo": round(pbo, 4) if pbo is not None else None,
            "permutation": round(perm_p, 4) if perm_p is not None else None,
            "convexity": conv,
        },
    }
