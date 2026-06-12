"""Tests known-answer pour verdict.py — l'agrégateur du CRITIC.

Combine les 3 gates en UN verdict : un edge ne PASSE que s'il survit à TOUS :
  - beta-neutral (vrai alpha résiduel, pas du beta déguisé)
  - DSR > seuil (robuste au multiple-testing / overfit, déflaté par n_trials)
  - PBO < seuil (pas d'overfit en CSCV)  [si matrice fournie]

Run: cd backend/edge_factory && ../../.venv/bin/python test_verdict.py
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verdict as v  # noqa: E402


def _alpha_strat(n, alpha, seed):
    rng = random.Random(seed)
    return [alpha + rng.gauss(0, 0.3) for _ in range(n)]


def _bench(n, seed):
    rng = random.Random(seed)
    return [rng.gauss(0, 1) for _ in range(n)]


def test_pass_real_edge():
    # vrai alpha décorrélé, peu de trials -> doit PASSER tous les gates
    strat = _alpha_strat(300, 0.4, 1)
    bench = _bench(300, 11)
    r = v.evaluate_edge(strat, bench, n_trials=10, sr_variance=0.25)
    assert r["pass"] is True, r


def test_fail_pure_beta():
    # strat = 1.2*bench + bruit : pas d'alpha -> FAIL (beta_deguise)
    bench = _bench(300, 2)
    rng = random.Random(22)
    strat = [1.2 * b + rng.gauss(0, 0.2) for b in bench]
    r = v.evaluate_edge(strat, bench, n_trials=10, sr_variance=0.25)
    assert r["pass"] is False
    assert "beta_deguise" in r["reasons"]


def test_fail_overfit_dsr():
    # même alpha modéré MAIS 100k trials testés -> DSR s'effondre -> FAIL (dsr)
    strat = _alpha_strat(300, 0.25, 3)
    bench = _bench(300, 33)
    r = v.evaluate_edge(strat, bench, n_trials=100000, sr_variance=0.25)
    assert r["pass"] is False
    assert "dsr" in r["reasons"]


def test_fail_pbo_overfit_matrix():
    # déterministe : on calcule le PBO réel de la matrice puis on force le seuil
    # juste en-dessous -> teste le CÂBLAGE du gate PBO (indépendant du bruit).
    rng = random.Random(42)
    mat = [[rng.gauss(0, 1) for _ in range(10)] for _ in range(120)]
    pbo_val, _ = v._stats.pbo_cscv(mat, S=8)
    strat = _alpha_strat(300, 0.4, 4)
    bench = _bench(300, 44)
    r = v.evaluate_edge(strat, bench, n_trials=10, sr_variance=0.25,
                        pbo_matrix=mat, pbo_S=8, pbo_max=pbo_val - 0.01)
    assert r["pass"] is False
    assert "pbo" in r["reasons"]


def test_reasons_empty_when_pass():
    strat = _alpha_strat(300, 0.5, 5)
    bench = _bench(300, 55)
    r = v.evaluate_edge(strat, bench, n_trials=5, sr_variance=0.2)
    assert r["pass"] is True
    assert r["reasons"] == []


def test_permutation_gate_fails_when_insignificant():
    # 4e gate (optionnelle) : permutation fournie avec p>=0.05 -> FAIL même si le
    # reste passe. Prouve le câblage du gate permutation (le tueur de data-mining).
    strat = _alpha_strat(300, 0.5, 6)
    bench = _bench(300, 66)
    r = v.evaluate_edge(strat, bench, n_trials=5, sr_variance=0.2,
                        permutation={"p_value": 0.88, "significant": False})
    assert r["pass"] is False
    assert "permutation" in r["reasons"]
    assert r["gates"]["permutation"] == 0.88


def test_permutation_gate_passes_when_significant():
    strat = _alpha_strat(300, 0.5, 7)
    bench = _bench(300, 77)
    r = v.evaluate_edge(strat, bench, n_trials=5, sr_variance=0.2,
                        permutation={"p_value": 0.01, "significant": True})
    assert r["pass"] is True
    assert "permutation" not in r["reasons"]


def test_marginal_edge_rejected_by_hardened_t_threshold():
    # V2 : un edge marginal dont 2.0 < t_alpha < 3.0 PASSAIT le beta-gate à t_min=2.0,
    # et doit maintenant ÉCHOUER au nouveau défaut t_min=3.0 (HLZ 2016). alpha=0.03
    # sur 400 pts donne t_alpha≈2.85 (calibré empiriquement) → pile dans la zone 2-3.
    strat = _alpha_strat(400, 0.03, 123)
    bench = _bench(400, 321)
    r = v.evaluate_edge(strat, bench, n_trials=5, sr_variance=0.2)
    ta = r["gates"]["beta_neutral"]["t_alpha"]
    assert 2.0 < ta < 3.0, ta                 # marginal : passerait à l'ancien seuil
    # beta≈0 (edge décorrélé) + t<3 → label précis 'weak_alpha' (pas 'beta_deguise')
    assert "weak_alpha" in r["reasons"], r["reasons"]
    assert r["pass"] is False


def test_default_thresholds_are_hardened():
    # V2 : les défauts du juge sont durcis (HLZ t=3.0, LdP PBO=0.2)
    import inspect
    sig = inspect.signature(v.evaluate_edge)
    assert sig.parameters["t_min"].default == 3.0
    assert sig.parameters["pbo_max"].default == 0.2


def test_permutation_absent_is_backward_compatible():
    # sans permutation -> comportement identique à avant (gate ignorée, gauge None)
    strat = _alpha_strat(300, 0.5, 8)
    bench = _bench(300, 88)
    r = v.evaluate_edge(strat, bench, n_trials=5, sr_variance=0.2)
    assert r["pass"] is True
    assert r["gates"]["permutation"] is None


if __name__ == "__main__":
    fns = [val for k, val in sorted(globals().items()) if k.startswith("test_")]
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
