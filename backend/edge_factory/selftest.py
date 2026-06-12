#!/usr/bin/env python3
"""Self-test du juge (Gate 0) — preuve EMPIRIQUE que le CRITIC marche.

Trois épreuves, pur-python :
  1. best_of_noise_verdict : génère N stratégies de PUR BRUIT, sélectionne celle au
     Sharpe max (= data-mining maximal), la juge avec n_trials=N. Le DSR déflaté
     doit la REJETER même si son t_alpha brut dépasse 2. C'est le contrôle
     family-wise-error de López de Prado (le faux positif monte avec N).
  2. noise_survivor_rate : fraction de N bruits qui survivent (doit être ~0).
  3. planted_edge_verdict : un vrai alpha décorrélé, peu de trials → doit PASSER.

Si les 3 tiennent : le juge n'est ni cassé (laisse passer le vrai signal) ni laxiste
(tue le best-of-noise). C'est le critère d'acceptation de la plateforme.
"""
import random
import statistics

from verdict import _stats, evaluate_edge


def _noise(n_obs, rng):
    return [rng.gauss(0, 1) for _ in range(n_obs)]


def best_of_noise_verdict(n_strategies=200, n_obs=250, seed=0):
    """Sélectionne le bruit au Sharpe max parmi N, le juge avec n_trials=N."""
    rng = random.Random(seed)
    bench = _noise(n_obs, random.Random(seed + 99))
    strats = [_noise(n_obs, rng) for _ in range(n_strategies)]
    sharpes = [_stats._sharpe(s) for s in strats]
    best_i = max(range(len(strats)), key=lambda i: sharpes[i])
    sr_var = max(statistics.pvariance(sharpes), 1e-4)
    v = evaluate_edge(strats[best_i], bench, n_trials=n_strategies, sr_variance=sr_var)
    v["selected_sharpe"] = sharpes[best_i]
    return v


def noise_survivor_rate(n_strategies=100, n_obs=250, seed=0):
    """Fraction de bruits qui passent le CRITIC quand jugés avec n_trials=N."""
    rng = random.Random(seed)
    bench = _noise(n_obs, random.Random(seed + 7))
    strats = [_noise(n_obs, rng) for _ in range(n_strategies)]
    sharpes = [_stats._sharpe(s) for s in strats]
    sr_var = max(statistics.pvariance(sharpes), 1e-4)
    survivors = 0
    for s in strats:
        v = evaluate_edge(s, bench, n_trials=n_strategies, sr_variance=sr_var)
        survivors += int(v["pass"])
    return survivors / n_strategies


def planted_edge_verdict(n_obs=300, alpha=0.5, n_trials=10, seed=0):
    rng = random.Random(seed)
    strat = [alpha + rng.gauss(0, 0.3) for _ in range(n_obs)]
    bench = [rng.gauss(0, 1) for _ in range(n_obs)]
    return evaluate_edge(strat, bench, n_trials=n_trials, sr_variance=0.25)


def realistic_edge_verdict(n_obs=800, alpha=0.15, beta=0.3, noise=0.25,
                           n_trials=5, seed=0):
    """V4 — test de FAUX-NÉGATIF : un edge RÉEL de force RAISONNABLE doit passer le juge DURCI.

    ≠ planted_edge (alpha parfait, énorme, beta nul). Ici : edge modéré (Sharpe ~0.57)
    + beta partiel 0.3 (expo marché réelle) + bruit + non-trivial. Calibré empiriquement :
    un edge plus faible (Sharpe ~0.16) est CORRECTEMENT rejeté par le DSR (indistinguable
    du bruit sur cette taille) — preuve que les seuils durcis ne sont pas trop stricts mais
    JUSTES. Un edge réel de force raisonnable, lui, DOIT passer → c'est ce qu'on teste.
    """
    rng = random.Random(seed)
    bench = [rng.gauss(0, 1) for _ in range(n_obs)]
    strat = [alpha + beta * b + rng.gauss(0, noise) for b in bench]
    return evaluate_edge(strat, bench, n_trials=n_trials, sr_variance=0.05)


def run_self_test(seed=0):
    bon = best_of_noise_verdict(seed=seed)
    rate = noise_survivor_rate(seed=seed + 1)
    planted = planted_edge_verdict(seed=seed + 2)
    realistic = realistic_edge_verdict(seed=seed + 3)
    return {
        "best_of_noise_rejected": not bon["pass"],
        "noise_survivor_rate": rate,
        "planted_edge_detected": planted["pass"],
        "realistic_edge_detected": realistic["pass"],
        "pass": (not bon["pass"]) and rate <= 0.05 and planted["pass"]
        and realistic["pass"],
    }


if __name__ == "__main__":
    import json
    print(json.dumps(run_self_test(seed=0), indent=2))
