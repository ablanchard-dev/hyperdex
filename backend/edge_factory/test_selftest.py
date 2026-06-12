"""Tests pour selftest.py — le SELF-TEST DU JUGE (Gate 0 de la plateforme).

Prouve que le CRITIC n'est ni cassé ni laxiste, empiriquement :
  1. best-of-noise : sélectionner le MEILLEUR Sharpe parmi N stratégies de bruit
     (le piège du data-mining — son t_alpha dépasse souvent 2, passerait beta-neutral
     SEUL) DOIT être rejeté par le DSR déflaté par n_trials=N. C'est le test
     family-wise-error de López de Prado.
  2. noise survivor rate : sur N bruits jugés avec n_trials=N, le taux de survivants
     doit être ~0 (< 5%).
  3. planted edge : un alpha réel décorrélé DOIT passer (le juge n'est pas qu'un
     mur — il laisse passer le vrai signal).

Run: cd backend/edge_factory && ../../.venv/bin/python test_selftest.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import selftest as st  # noqa: E402


def test_best_of_noise_is_rejected():
    # le meilleur parmi 200 bruits = data-mining max -> DSR déflaté doit le tuer
    out = st.best_of_noise_verdict(n_strategies=200, n_obs=250, seed=1)
    assert out["pass"] is False, out
    # et c'est bien un piège : son Sharpe brut est élevé (sélection du max)
    assert out["selected_sharpe"] > 0.08, out


def test_noise_survivor_rate_near_zero():
    rate = st.noise_survivor_rate(n_strategies=100, n_obs=250, seed=2)
    assert rate <= 0.05, rate


def test_planted_edge_detected():
    out = st.planted_edge_verdict(n_obs=300, alpha=0.5, n_trials=10, seed=3)
    assert out["pass"] is True, out


def test_selftest_deterministic():
    a = st.noise_survivor_rate(n_strategies=60, n_obs=200, seed=7)
    b = st.noise_survivor_rate(n_strategies=60, n_obs=200, seed=7)
    assert a == b, (a, b)


def test_run_all_returns_clean_summary():
    # le harness one-shot : doit retourner un dict avec un flag global pass
    summary = st.run_self_test(seed=5)
    assert summary["pass"] is True, summary
    assert summary["best_of_noise_rejected"] is True
    assert summary["noise_survivor_rate"] <= 0.05
    assert summary["planted_edge_detected"] is True
    # V4 : le juge ne doit pas être trop strict (faux-négatif)
    assert summary["realistic_edge_detected"] is True


def test_realistic_edge_passes_hardened_judge():
    # V4 : edge FAIBLE mais RÉEL (pas l'alpha parfait du planted) — doit PASSER les
    # seuils DURCIS (t=3.0). Si le durcissage le tue, les seuils sont trop stricts.
    out = st.realistic_edge_verdict(seed=11)
    assert out["pass"] is True, out


def test_realistic_edge_deterministic():
    a = st.realistic_edge_verdict(seed=3)
    b = st.realistic_edge_verdict(seed=3)
    assert a["pass"] == b["pass"]


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
