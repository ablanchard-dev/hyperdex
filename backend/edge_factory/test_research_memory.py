"""Tests pour research_memory.py — mémoire de recherche persistante.

Log tested/rejected/survived, dédup (ne pas re-tester une hypothèse), persistance
JSON. Permet au générateur de reprendre sans refaire le travail + de surfacer les
survivants accumulés.

Run: cd backend/edge_factory && ../../.venv/bin/python test_research_memory.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import research_memory as rm  # noqa: E402


def _res(family, lb, passed):
    return {"hypothesis": {"family": family, "params": {"lookback": lb}},
            "pass": passed, "reasons": [] if passed else ["dsr"],
            "gates": {"dsr": 0.97 if passed else 0.2}, "venue": "hl"}


def test_record_and_survivors():
    with tempfile.TemporaryDirectory() as d:
        m = rm.ResearchMemory(os.path.join(d, "m.json"))
        m.record(_res("momentum", 6, False))
        m.record(_res("mean_reversion", 12, True))
        assert len(m.all()) == 2
        surv = m.survivors()
        assert len(surv) == 1
        assert surv[0]["hypothesis"]["family"] == "mean_reversion"


def test_dedup_is_tested():
    with tempfile.TemporaryDirectory() as d:
        m = rm.ResearchMemory(os.path.join(d, "m.json"))
        h = {"family": "momentum", "params": {"lookback": 6}}
        assert m.is_tested(h, "hl") is False
        m.record(_res("momentum", 6, False))
        assert m.is_tested(h, "hl") is True
        # même hypothèse re-enregistrée -> pas de doublon
        m.record(_res("momentum", 6, False))
        assert len(m.all()) == 1


def test_handles_dsl_signal_format():
    # specs DSL/LLM : hypothesis = {"signal":{"type","params"}} (pas "family")
    with tempfile.TemporaryDirectory() as d:
        m = rm.ResearchMemory(os.path.join(d, "m.json"))
        dsl_res = {"hypothesis": {"name": "x", "signal": {
            "type": "zscore_reversion", "params": {"lookback": 20, "entry_z": 2.0}}},
            "pass": True, "reasons": [], "gates": {}, "venue": "equities_xs"}
        m.record(dsl_res)  # ne doit PAS lever KeyError
        assert len(m.all()) == 1
        assert m.is_tested(dsl_res["hypothesis"], "equities_xs") is True


def test_persistence_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "m.json")
        m = rm.ResearchMemory(path)
        m.record(_res("breakout", 24, True))
        m.record(_res("momentum", 6, False))
        m.save()
        # nouvelle instance recharge depuis le disque
        m2 = rm.ResearchMemory(path)
        assert len(m2.all()) == 2
        assert len(m2.survivors()) == 1
        assert m2.is_tested({"family": "breakout", "params": {"lookback": 24}},
                            "hl") is True


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
