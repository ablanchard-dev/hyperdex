"""Tests pour llm_hypothesis.py — agent LLM Hypothesis (parsing + validation).

Le LLM propose des hypothèses en JSON-DSL ; l'agent EXTRAIT + VALIDE (rejette le
malformé/non-supporté) avant toute exécution. L'appel LLM réel est injecté (mock
en test, claude CLI en --live) : AUCUN appel LLM dans les tests unitaires.

Run: cd backend/edge_factory && ../../.venv/bin/python test_llm_hypothesis.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import llm_hypothesis as lh  # noqa: E402

_FENCED = '''Voici mes hypothèses :
```json
[
  {"name":"mom5","rationale":"trend","signal":{"type":"momentum","params":{"lookback":5}}},
  {"name":"bad","rationale":"x","signal":{"type":"magic_oracle","params":{"x":1}}},
  {"name":"zr","rationale":"rev","signal":{"type":"zscore_reversion","params":{"lookback":20,"entry_z":2.0}}}
]
```
Fin.'''

_RAW = ('blabla [ {"name":"mac","rationale":"r","signal":'
        '{"type":"ma_cross","params":{"fast":10,"slow":50}}} ] etc')


def test_extract_specs_fenced():
    specs = lh.extract_specs(_FENCED)
    assert len(specs) == 3
    assert specs[0]["signal"]["type"] == "momentum"


def test_extract_specs_raw_array():
    specs = lh.extract_specs(_RAW)
    assert len(specs) == 1 and specs[0]["signal"]["type"] == "ma_cross"


def test_extract_specs_garbage_safe():
    assert lh.extract_specs("aucun json ici") == []
    assert lh.extract_specs("") == []


def test_valid_specs_filters_invalid():
    specs = lh.extract_specs(_FENCED)        # contient 1 spec invalide (magic_oracle)
    valid = lh.valid_specs(specs)
    assert len(valid) == 2                    # bad rejetée
    assert all(s["signal"]["type"] in ("momentum", "zscore_reversion") for s in valid)


def test_generate_hypotheses_with_mock_llm():
    calls = {}

    def mock_llm(prompt):
        calls["prompt"] = prompt
        return _FENCED

    valid = lh.generate_hypotheses(mock_llm, n=3)
    assert len(valid) == 2                    # seules les valides passent
    assert "momentum" in calls["prompt"] or "zscore" in calls["prompt"] \
        or "JSON" in calls["prompt"]          # le prompt cadre le DSL


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
