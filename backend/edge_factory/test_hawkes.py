"""Tests pour hawkes.py — processus de Hawkes auto-excitant (kernel exponentiel).

Modèle : intensité λ(t) = μ + Σ_{t_i<t} α·e^(−β(t−t_i)). Le branching ratio ρ=α/β
gouverne la dynamique (ρ<1 sous-critique/stationnaire = cascades qui s'éteignent ;
ρ≥1 explose). Log-vraisemblance via la RÉCURSION O(n) (Ozaki) — clé pour la vitesse.

Cible applicative (Phase 1) : modéliser les cascades de liquidation crypto (papier
SSRN 2026 : régime sous-critique stable, auto-excitation significative). Ici on
valide le MOTEUR (calibration retrouve les vrais params sur data synthétique connue).

Run: cd backend/edge_factory && ../../.venv/bin/python test_hawkes.py
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hawkes as hk  # noqa: E402


def test_branching_ratio():
    assert abs(hk.branching_ratio(0.5, 2.0) - 0.25) < 1e-12


def test_intensity_jumps_after_event_and_decays():
    # à t juste après un event en 0 : λ ≈ μ + α ; loin après : λ → μ
    events = [0.0]
    near = hk.intensity(0.001, events, mu=0.1, alpha=1.0, beta=2.0)
    far = hk.intensity(100.0, events, mu=0.1, alpha=1.0, beta=2.0)
    assert near > far
    assert abs(near - (0.1 + 1.0)) < 0.05
    assert abs(far - 0.1) < 1e-6


def test_loglik_recursive_matches_naive():
    # la récursion O(n) doit donner la MÊME log-vraisemblance que la somme O(n²)
    events = sorted(random.Random(0).uniform(0, 50) for _ in range(40))
    fast = hk.log_likelihood(events, mu=0.5, alpha=0.8, beta=1.5, T=50.0)
    naive = hk.log_likelihood_naive(events, mu=0.5, alpha=0.8, beta=1.5, T=50.0)
    assert abs(fast - naive) < 1e-6, (fast, naive)


def test_mle_recovers_known_params_subcritical():
    # simule un Hawkes à params connus (sous-critique), MLE doit les retrouver ~bien
    true_mu, true_alpha, true_beta = 0.5, 0.6, 2.0  # ρ=0.3 sous-critique
    events = hk.simulate(true_mu, true_alpha, true_beta, T=4000.0, seed=42)
    assert len(events) > 200  # assez d'events pour calibrer
    est = hk.fit_mle(events, T=4000.0)
    # branching ratio estimé proche du vrai (0.3) — la quantité qui compte
    assert abs(hk.branching_ratio(est["alpha"], est["beta"]) - 0.3) < 0.15, est
    assert abs(est["mu"] - true_mu) < 0.25, est


def test_simulate_subcritical_does_not_explode():
    # ρ<1 -> nombre d'events fini et raisonnable (pas d'explosion)
    events = hk.simulate(0.3, 0.5, 2.0, T=1000.0, seed=1)
    # taux théorique stationnaire = μ/(1-ρ) = 0.3/0.75 = 0.4 -> ~400 events
    assert 200 < len(events) < 700, len(events)


def test_fit_flags_supercritical():
    # si on calibre sur un burst très auto-excité, le moteur doit pouvoir signaler
    # le régime via branching ratio (diagnostic stationnarité)
    est = {"alpha": 3.0, "beta": 2.0}
    assert hk.is_supercritical(est["alpha"], est["beta"]) is True
    assert hk.is_supercritical(0.5, 2.0) is False


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
