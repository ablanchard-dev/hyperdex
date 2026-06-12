"""Tests known-answer pour critic.py — test beta-neutral (alpha vs beta).

LE tueur récurrent : un « edge » directionnel est souvent du beta déguisé
(copy-trading HYPE, liquidation-fade beta 1.16×…). Le CRITIC doit régresser
les returns de la strat sur le benchmark et exiger un alpha résiduel
significatif APRÈS retrait du beta.

Run: cd backend/edge_factory && ../../.venv/bin/python -m pytest test_critic.py -q
ou : ../../.venv/bin/python test_critic.py
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import critic as c  # noqa: E402


def test_beta_recovery_exact():
    # strat = 1.5*bench + 0.3 (alpha=0.3, beta=1.5), résidus nuls
    bench = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    strat = [1.5 * b + 0.3 for b in bench]
    r = c.beta_neutral_alpha(strat, bench)
    assert abs(r["beta"] - 1.5) < 1e-9
    assert abs(r["alpha"] - 0.3) < 1e-9


def test_pure_beta_zero_alpha():
    # strat = 2*bench : que du beta, zéro alpha
    bench = [-2.0, -1.0, 0.5, 1.0, 3.0, 4.0]
    strat = [2.0 * b for b in bench]
    r = c.beta_neutral_alpha(strat, bench)
    assert abs(r["beta"] - 2.0) < 1e-9
    assert abs(r["alpha"]) < 1e-9


def test_alpha_significant_when_real():
    # alpha réel (+0.5/période) décorrélé du benchmark -> t_alpha élevé
    rng = random.Random(1)
    bench = [rng.gauss(0, 1) for _ in range(300)]
    strat = [0.5 + rng.gauss(0, 0.3) for _ in range(300)]  # beta~0, alpha~0.5
    r = c.beta_neutral_alpha(strat, bench)
    assert abs(r["alpha"] - 0.5) < 0.1
    assert abs(r["beta"]) < 0.2
    assert r["t_alpha"] > 5  # alpha franchement significatif


def test_alpha_insignificant_when_pure_beta():
    # strat = 1.0*bench + bruit, AUCUN alpha -> t_alpha faible, beta~1
    rng = random.Random(2)
    bench = [rng.gauss(0, 1) for _ in range(300)]
    strat = [b + rng.gauss(0, 0.3) for b in bench]
    r = c.beta_neutral_alpha(strat, bench)
    assert abs(r["beta"] - 1.0) < 0.1
    assert abs(r["alpha"]) < 0.1
    assert abs(r["t_alpha"]) < 2.5  # pas de vrai alpha


def test_verdict_rejects_pure_beta():
    # le verdict CRITIC doit REJETER une strat qui n'est que du beta
    rng = random.Random(3)
    bench = [rng.gauss(0, 1) for _ in range(300)]
    strat = [1.2 * b + rng.gauss(0, 0.2) for b in bench]
    v = c.beta_neutral_verdict(strat, bench, t_min=2.0)
    assert v["pass"] is False
    assert v["reason"] == "beta_deguise"


def test_verdict_accepts_real_alpha():
    rng = random.Random(4)
    bench = [rng.gauss(0, 1) for _ in range(300)]
    strat = [0.4 + rng.gauss(0, 0.3) for _ in range(300)]
    v = c.beta_neutral_verdict(strat, bench, t_min=2.0)
    assert v["pass"] is True


def test_weak_alpha_label_when_beta_near_zero():
    # P5 : strat market-neutral (beta≈0) MAIS sans alpha significatif → la raison doit
    # être 'weak_alpha', PAS 'beta_deguise' (le label était trompeur : un edge sans
    # exposition marché qui manque juste de signal n'est pas du beta déguisé).
    rng = random.Random(11)
    bench = [rng.gauss(0, 1) for _ in range(300)]
    strat = [0.02 + rng.gauss(0, 0.5) for _ in range(300)]  # alpha minuscule, beta~0
    v = c.beta_neutral_verdict(strat, bench, t_min=3.0)
    assert v["pass"] is False
    assert abs(v["beta"]) < 0.5
    assert v["reason"] == "weak_alpha", v["reason"]


def test_beta_deguise_label_when_beta_high():
    # vrai beta déguisé : forte exposition marché, pas d'alpha → 'beta_deguise'
    rng = random.Random(12)
    bench = [rng.gauss(0, 1) for _ in range(300)]
    strat = [1.5 * b + rng.gauss(0, 0.2) for b in bench]
    v = c.beta_neutral_verdict(strat, bench, t_min=3.0)
    assert v["pass"] is False
    assert v["reason"] == "beta_deguise", v["reason"]


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
