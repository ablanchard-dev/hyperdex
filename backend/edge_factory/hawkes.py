#!/usr/bin/env python3
"""Processus de Hawkes auto-excitant à kernel exponentiel — pur-python.

λ(t) = μ + Σ_{t_i<t} α·e^(−β(t−t_i))

- branching ratio ρ = α/β : ρ<1 sous-critique (stationnaire, cascades s'éteignent),
  ρ≥1 supercritique (explose). C'est LE diagnostic de régime.
- log-vraisemblance par récursion O(n) d'Ozaki (vs O(n²) naïf) → vitesse d'exécution.
- simulation par thinning d'Ogata (1981).
- MLE par coordinate-descent / golden-section pur-python (zéro scipy/numpy).

Cible Phase 1 : cascades de liquidation crypto (auto-excitation contagieuse). Réf :
kernel exp + ρ=α/β standard (arxiv 1502.04592 « Hawkes processes in finance »).
"""
import math
import random
from typing import Dict, List


def branching_ratio(alpha: float, beta: float) -> float:
    return alpha / beta if beta > 0 else float("inf")


def is_supercritical(alpha: float, beta: float) -> bool:
    return branching_ratio(alpha, beta) >= 1.0


def intensity(t: float, events: List[float], mu: float, alpha: float,
              beta: float) -> float:
    """λ(t) = μ + Σ_{t_i<t} α·e^(−β(t−t_i)) (events strictement avant t)."""
    s = 0.0
    for ti in events:
        if ti < t:
            s += math.exp(-beta * (t - ti))
        else:
            break
    return mu + alpha * s


def log_likelihood_naive(events: List[float], mu: float, alpha: float,
                         beta: float, T: float) -> float:
    """Référence O(n²) : ll = Σ log λ(t_i) − ∫_0^T λ(s) ds."""
    n = len(events)
    if n == 0:
        return -mu * T
    sum_log = 0.0
    for i, ti in enumerate(events):
        lam = mu + alpha * sum(math.exp(-beta * (ti - events[j]))
                               for j in range(i))
        sum_log += math.log(lam) if lam > 0 else -1e9
    # compensateur : ∫ μ dt + Σ (α/β)(1 − e^{−β(T−t_i)})
    compensator = mu * T + (alpha / beta) * sum(
        1 - math.exp(-beta * (T - ti)) for ti in events)
    return sum_log - compensator


def log_likelihood(events: List[float], mu: float, alpha: float, beta: float,
                   T: float) -> float:
    """Récursion O(n) d'Ozaki : A_i = e^{−β(t_i−t_{i−1})}(1 + A_{i−1})."""
    n = len(events)
    if n == 0:
        return -mu * T
    sum_log = 0.0
    a = 0.0  # A_0 = 0
    prev = events[0]
    sum_log += math.log(mu) if mu > 0 else -1e9  # λ(t_0) = μ
    for i in range(1, n):
        a = math.exp(-beta * (events[i] - prev)) * (1.0 + a)
        lam = mu + alpha * a
        sum_log += math.log(lam) if lam > 0 else -1e9
        prev = events[i]
    compensator = mu * T + (alpha / beta) * sum(
        1 - math.exp(-beta * (T - ti)) for ti in events)
    return sum_log - compensator


def simulate(mu: float, alpha: float, beta: float, T: float,
             seed: int = 0) -> List[float]:
    """Thinning d'Ogata (1981). Suppose sous-critique (α<β) pour terminer."""
    rng = random.Random(seed)
    events: List[float] = []
    t = 0.0
    while t < T:
        lam_bar = intensity(t, events, mu, alpha, beta) + alpha  # majorant local
        w = rng.expovariate(lam_bar)
        t += w
        if t >= T:
            break
        if rng.random() <= intensity(t, events, mu, alpha, beta) / lam_bar:
            events.append(t)
    return events


def _golden_min(f, lo, hi, tol=1e-4, it=60):
    """Minimise f sur [lo,hi] par section dorée (unimodal supposé localement)."""
    gr = (math.sqrt(5) - 1) / 2
    c = hi - gr * (hi - lo)
    d = lo + gr * (hi - lo)
    fc, fd = f(c), f(d)
    for _ in range(it):
        if hi - lo < tol:
            break
        if fc < fd:
            hi, d, fd = d, c, fc
            c = hi - gr * (hi - lo)
            fc = f(c)
        else:
            lo, c, fc = c, d, fd
            d = lo + gr * (hi - lo)
            fd = f(d)
    return (lo + hi) / 2


def fit_mle(events: List[float], T: float, max_iter: int = 30) -> Dict[str, float]:
    """MLE par coordinate-descent (golden-section sur chaque param), pur-python.
    Retourne {mu, alpha, beta, branching_ratio, loglik}. Contrainte α<β imposée."""
    n = len(events)
    mu = max(n / T * 0.5, 1e-3)
    beta = 1.0
    alpha = 0.3

    def neg_ll(mu_, al_, be_):
        if mu_ <= 0 or al_ <= 0 or be_ <= 0 or al_ >= be_:
            return 1e18
        return -log_likelihood(events, mu_, al_, be_, T)

    for _ in range(max_iter):
        mu = _golden_min(lambda m: neg_ll(m, alpha, beta), 1e-4, n / T * 3 + 1)
        beta = _golden_min(lambda b: neg_ll(mu, alpha, b),
                           max(alpha * 1.01, 0.05), 20.0)
        alpha = _golden_min(lambda a: neg_ll(mu, a, beta), 1e-4, beta * 0.99)
    return {
        "mu": mu, "alpha": alpha, "beta": beta,
        "branching_ratio": branching_ratio(alpha, beta),
        "loglik": log_likelihood(events, mu, alpha, beta, T),
    }
