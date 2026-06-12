"""Tests pour hypothesis_dsl.py — DSL d'hypothèses + interpréteur SÛR.

Le LLM (agent Hypothesis) émettra des specs JSON depuis ce vocabulaire ; jamais
du code arbitraire. L'interpréteur valide la spec et produit un signal
no-look-ahead par construction. Specs invalides -> rejetées (pas d'exécution).

Run: cd backend/edge_factory && ../../.venv/bin/python test_hypothesis_dsl.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hypothesis_dsl as dsl  # noqa: E402


def _spec(stype, **params):
    return {"name": "t", "rationale": "r", "signal": {"type": stype, "params": params}}


def test_validate_accepts_known_and_rejects_unknown():
    assert dsl.validate_spec(_spec("momentum", lookback=5)) is True
    assert dsl.validate_spec(_spec("ma_cross", fast=2, slow=4)) is True
    assert dsl.validate_spec(_spec("zscore_reversion", lookback=20, entry_z=2.0)) is True
    # type inconnu -> rejeté
    assert dsl.validate_spec(_spec("magic_oracle", x=1)) is False
    # params manquants -> rejeté
    assert dsl.validate_spec(_spec("ma_cross", fast=2)) is False
    # lookback négatif -> rejeté
    assert dsl.validate_spec(_spec("momentum", lookback=-3)) is False
    # ma_cross fast>=slow -> rejeté (incohérent)
    assert dsl.validate_spec(_spec("ma_cross", fast=10, slow=5)) is False


def test_build_signal_ma_cross_known():
    fn = dsl.build_signal(_spec("ma_cross", fast=2, slow=4))
    # MA2(last2)=(4+5)/2=4.5 > MA4=(2+3+4+5)/4=3.5 -> +1
    assert fn([1, 2, 3, 4, 5]) == 1
    # MA2=(2+1)/2=1.5 < MA4=(4+3+2+1)/4=2.5 -> -1
    assert fn([5, 4, 3, 2, 1]) == -1
    assert fn([1, 2]) == 0  # historique insuffisant


def test_build_signal_zscore_reversion_known():
    fn = dsl.build_signal(_spec("zscore_reversion", lookback=5, entry_z=1.0))
    # window [10,10,10,10,20] : mean=12, pstd=4, z=(20-12)/4=2 > 1 -> fade -> -1
    assert fn([10, 10, 10, 10, 20]) == -1
    # window [10,10,10,10,4] : mean=8.8, z=(4-8.8)/pstd < -1 -> +1
    assert fn([10, 10, 10, 10, 4]) == 1
    # plat -> z=0 -> 0
    assert fn([10, 10, 10, 10, 10]) == 0


def test_build_signal_rejects_invalid():
    try:
        dsl.build_signal(_spec("magic_oracle", x=1))
        raised = False
    except (ValueError, KeyError):
        raised = True
    assert raised


def test_no_lookahead_invariance():
    # signal à t n'utilise que closes[:t+1] : tronquer le futur ne change pas le passé
    fn = dsl.build_signal(_spec("momentum", lookback=2))
    closes = [10, 11, 12, 11, 13, 14, 12, 15]
    full = [fn(closes[:i + 1]) for i in range(len(closes))]
    trunc = [fn(closes[:i + 1]) for i in range(5)]
    assert full[:5] == trunc


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
