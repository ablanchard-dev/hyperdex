"""Tests pour autonomous.py — boucle complète LLM→DSL→backtest→CRITIC.

run_dsl_hypotheses : exécute des specs DSL (venues d'un LLM) via le CRITIC, DSR
déflaté par #specs. Bruit -> 0 survivant ; edge planté -> surfacé.

Run: cd backend/edge_factory && ../../.venv/bin/python test_autonomous.py
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adapter as ad  # noqa: E402
import autonomous as au  # noqa: E402


def _bars(closes):
    return [ad.Bar(ts=i, close=c) for i, c in enumerate(closes)]


def _spec(stype, **params):
    return {"name": stype, "rationale": "r",
            "signal": {"type": stype, "params": params}}


def test_n_trials_deflation():
    rng = random.Random(1)
    sb = {f"S{j}": _bars([100 + rng.gauss(0, 1) for _ in range(200)])
          for j in range(3)}
    bench = _bars([100 + rng.gauss(0, 1) for _ in range(200)])
    specs = [_spec("momentum", lookback=6),
             _spec("zscore_reversion", lookback=20, entry_z=2.0)]
    res = au.run_dsl_hypotheses(sb, bench, specs, taker_bps=2.0)
    assert len(res) == 2
    assert all(r["n_trials"] == 2 for r in res)


def test_noise_no_survivors():
    rng = random.Random(7)
    sb = {}
    for j in range(4):
        px, p = [100.0], 100.0
        for _ in range(260):
            p *= (1 + rng.gauss(0, 0.01))
            px.append(p)
        sb[f"S{j}"] = _bars(px)
    bpx, p = [100.0], 100.0
    for _ in range(260):
        p *= (1 + rng.gauss(0, 0.01))
        bpx.append(p)
    bench = _bars(bpx)
    specs = [_spec("momentum", lookback=6), _spec("breakout", lookback=12),
             _spec("ma_cross", fast=5, slow=20),
             _spec("zscore_reversion", lookback=20, entry_z=2.0)]
    res = au.run_dsl_hypotheses(sb, bench, specs, taker_bps=5.0)
    assert au.survivors(res) == []


def test_planted_edge_surfaces():
    sb = {f"S{j}": _bars([100.0 if t % 2 == 0 else 103.0 for t in range(220)])
          for j in range(3)}
    bench = _bars([100 + (t % 5) for t in range(220)])
    specs = [_spec("zscore_reversion", lookback=2, entry_z=0.5)]  # capte l'oscillation
    res = au.run_dsl_hypotheses(sb, bench, specs, taker_bps=0.0)
    assert len(au.survivors(res)) >= 1


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
