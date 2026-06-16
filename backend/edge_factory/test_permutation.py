"""Tests pour permutation.py — 4e gate du CRITIC (bar-permutation significance).

Bar-permutation test. Principe :
re-tourner la STRATÉGIE sur des prix dont les returns par-barre sont mélangés
(détruit la structure temporelle dont dépend le signal). Si l'edge vient vraiment
de la structure, le Sharpe réel doit battre ~95% des permutations → p<0.05.
Un Sharpe élevé qui ne survit PAS = data-mining (cf TSMOM p=0.88).

Run: cd backend/edge_factory && ../../.venv/bin/python test_permutation.py
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adapter as ad  # noqa: E402
import permutation as pm  # noqa: E402


def _bars(closes):
    return [ad.Bar(ts=i, close=c) for i, c in enumerate(closes)]


def _first_symbol_returns(bars_by_symbol):
    """Stratégie jouet : tient le 1er symbole (returns = ses pct-changes)."""
    s = sorted(bars_by_symbol)[0]
    closes = [b.close for b in bars_by_symbol[s]]
    return [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]


def test_pvalue_in_unit_interval():
    rng = random.Random(1)
    bars = {"A": _bars([100 * (1 + rng.uniform(-0.02, 0.02)) ** i for i in range(200)])}
    out = pm.permutation_test(_first_symbol_returns, bars, n_permutations=100, seed=7)
    assert 0.0 <= out["p_value"] <= 1.0, out["p_value"]


def test_deterministic_with_seed():
    rng = random.Random(2)
    bars = {"A": _bars([100 * (1 + rng.uniform(-0.02, 0.02)) ** i for i in range(150)])}
    a = pm.permutation_test(_first_symbol_returns, bars, n_permutations=80, seed=42)
    b = pm.permutation_test(_first_symbol_returns, bars, n_permutations=80, seed=42)
    assert a["p_value"] == b["p_value"], (a["p_value"], b["p_value"])


def test_input_independent_strategy_not_significant():
    # stratégie qui IGNORE l'input (returns fixes) -> Sharpe réel == tous les shuffled
    # -> p_value = 1.0 -> PAS significatif. Prouve qu'un edge sans dépendance à la
    # structure temporelle est rejeté (le cœur du test).
    fixed = [0.01, -0.005, 0.008, -0.003] * 20

    def const_strategy(_bars_by_symbol):
        return list(fixed)

    bars = {"A": _bars([100 * 1.001 ** i for i in range(81)])}
    out = pm.permutation_test(const_strategy, bars, n_permutations=50, seed=1)
    assert out["p_value"] == 1.0, out["p_value"]
    assert out["significant"] is False


def test_shuffle_preserves_first_close_and_length():
    closes = [100, 102, 101, 105, 103, 108]
    bars = {"A": _bars(closes)}
    rng = random.Random(0)
    shuffled = pm._shuffle_bars(bars, rng)
    assert len(shuffled["A"]) == len(bars["A"])
    assert shuffled["A"][0].close == bars["A"][0].close  # 1ère barre préservée


def test_serial_structure_signal_beats_permutation():
    # série à FORTE autocorrélation (blocs up/down) + stratégie qui exploite la
    # structure temporelle (long si barre précédente positive). Sur la vraie série
    # le timing capte les blocs ; mélangé -> timing aléatoire -> Sharpe s'effondre.
    pattern = ([0.02] * 5 + [-0.02] * 5) * 12  # 120 returns autocorrélés
    closes = [100.0]
    for r in pattern:
        closes.append(closes[-1] * (1 + r))
    bars = {"A": _bars(closes)}

    def momentum_timing(bbs):
        c = [b.close for b in bbs["A"]]
        rets = [(c[i] - c[i - 1]) / c[i - 1] for i in range(1, len(c))]
        # return à t = ret[t] si ret[t-1] > 0 sinon flat (décision passé-only)
        return [rets[i] if rets[i - 1] > 0 else 0.0 for i in range(1, len(rets))]

    out = pm.permutation_test(momentum_timing, bars, n_permutations=200, seed=42)
    # l'edge structurel réel doit battre la médiane des permutations
    assert out["p_value"] < 0.5, out
    assert out["real_sharpe"] > out["mean_shuffled"], out


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
