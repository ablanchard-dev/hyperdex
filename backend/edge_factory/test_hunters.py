"""Tests pour hunters.py — fabriques de chasseurs branchant les VRAIES familles.

Chaque make_*_hunter(data, ...) retourne un Hunter (callable ()->dict) compatible
hunt.Registry : il fait le split OOS, lance le backtest de la famille, et renvoie
{strat, bench, n_trials, sr_variance}. Testé sur data SYNTHÉTIQUE (zéro réseau) :
le fetch live est dans run_hunt.py. Vérifie shape, OOS, intégration Registry, déterminisme.

Run: cd backend/edge_factory && ../../.venv/bin/python test_hunters.py
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hunt  # noqa: E402
import hunters as H  # noqa: E402
from adapter import Bar  # noqa: E402


def _trend_bars(n, drift, seed):
    rng = random.Random(seed)
    p, out = 100.0, []
    for i in range(n):
        p *= 1 + drift + rng.uniform(-0.01, 0.01)
        out.append(Bar(ts=i * 3600000, close=p))
    return out


def _symbol_bars(n=200):
    # 6 symboles à drifts différents -> du cross-section exploitable
    return {f"S{j}": _trend_bars(n, 0.001 * (j - 3), seed=j) for j in range(6)}


def test_cross_sectional_hunter_shape():
    sb = _symbol_bars()
    bench = _trend_bars(200, 0.0005, seed=99)
    h = H.make_cross_sectional_hunter(sb, bench, "xs_momentum",
                                      {"lookback": 5}, top_frac=0.3, n_trials=8)
    out = h()
    for k in ("strat", "bench", "n_trials", "sr_variance"):
        assert k in out, k
    assert len(out["strat"]) == len(out["bench"])
    assert out["n_trials"] == 8
    # strat = portion OOS (test) -> plus courte que la série complète
    assert 0 < len(out["strat"]) < 200


def test_funding_carry_hunter_shape():
    n = 200
    rng = random.Random(1)
    funding = {f"C{j}": [0.0005 + 0.0001 * rng.gauss(0, 1) for _ in range(n)]
               for j in range(5)}
    premium = {f"C{j}": [0.0001 * rng.gauss(0, 1) for _ in range(n)] for j in range(5)}
    bench = _trend_bars(n, 0.0, seed=7)
    h = H.make_funding_carry_hunter(funding, premium, bench, fee_bps=1.5, n_trials=4)
    out = h()
    assert len(out["strat"]) == len(out["bench"]) > 0
    assert out["n_trials"] == 4


def test_liq_spike_hunter_shape():
    n = 200
    bars = _trend_bars(n, 0.0, seed=3)
    rng = random.Random(4)
    net_liq = [rng.gauss(0, 1000) for _ in range(n)]
    net_liq[100] = -500000  # un spike
    h = H.make_liq_spike_hunter(bars, net_liq, z_window=24, z_threshold=2.0, n_trials=3)
    out = h()
    assert len(out["strat"]) == len(out["bench"]) > 0
    assert out["n_trials"] == 3


def test_oi_divergence_hunter_shape():
    n = 200
    bars = _trend_bars(n, 0.0, seed=5)
    import random
    rng = random.Random(6)
    oiv = [1000.0 + rng.gauss(0, 50) for _ in range(n)]
    oiv[150] = 1600.0  # spike OI isolé
    h = H.make_oi_divergence_hunter(bars, oiv, window=48, threshold=2.0, n_trials=3)
    out = h()
    assert len(out["strat"]) == len(out["bench"]) > 0
    assert out["n_trials"] == 3


def test_hunters_plug_into_registry():
    sb = _symbol_bars()
    bench = _trend_bars(200, 0.0005, seed=99)
    reg = hunt.Registry()
    reg.register("xs_mom", H.make_cross_sectional_hunter(
        sb, bench, "xs_momentum", {"lookback": 5}, top_frac=0.3, n_trials=8))
    reg.register("xs_rev", H.make_cross_sectional_hunter(
        sb, bench, "xs_reversion", {"lookback": 5}, top_frac=0.3, n_trials=8))
    results = reg.hunt_all()
    assert len(results) == 2
    lb = reg.leaderboard()
    assert len(lb) == 2
    for r in lb:
        assert "pass" in r and "gates" in r


def test_hunter_deterministic():
    sb = _symbol_bars()
    bench = _trend_bars(200, 0.0005, seed=99)
    h = H.make_cross_sectional_hunter(sb, bench, "xs_momentum",
                                      {"lookback": 5}, top_frac=0.3, n_trials=8)
    a, b = h(), h()
    assert a["strat"] == b["strat"]


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
