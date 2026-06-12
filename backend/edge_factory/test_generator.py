"""Tests pour generator.py — HypothesisGenerator v0 (recherche systématique).

Le cœur de la Phase 2 : énumère un espace d'hypothèses, backteste chaque OOS,
passe au CRITIC en DÉFLATANT le DSR par le nombre TOTAL d'hypothèses (anti-snoop),
collecte les survivants. Sur du bruit -> 0 survivant (honnête). Sur un edge
planté -> il le surface (ne fait pas que rejeter).

Run: cd backend/edge_factory && ../../.venv/bin/python test_generator.py
"""
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adapter as ad  # noqa: E402
import generator as g  # noqa: E402


def _bars(closes):
    return [ad.Bar(ts=i, close=c) for i, c in enumerate(closes)]


def test_generate_space_structure():
    sp = g.generate_space(["momentum", "mean_reversion"], [6, 12, 24])
    assert len(sp) == 6  # 2 familles × 3 lookbacks
    assert {"family": "momentum", "params": {"lookback": 6}} in sp


def test_signals_basic():
    assert g.SIGNALS["momentum"]([1, 2, 3], {"lookback": 2}) == 1
    assert g.SIGNALS["mean_reversion"]([1, 2, 3], {"lookback": 2}) == -1  # fade hausse
    # breakout : close franchit le max de la fenêtre -> +1
    assert g.SIGNALS["breakout"]([1, 2, 1, 1, 5], {"lookback": 3}) == 1


def test_n_trials_equals_space_size():
    # ANTI-SNOOP : chaque hypothèse jugée avec n_trials = taille de l'espace
    rng = random.Random(1)
    sb = {f"S{j}": _bars([100 * (1 + rng.gauss(0, 0.01)) ** 0 + rng.gauss(0, 1)
                          for _ in range(200)]) for j in range(3)}
    bench = _bars([100 + rng.gauss(0, 1) for _ in range(200)])
    sp = g.generate_space(["momentum", "mean_reversion"], [6, 12])
    res = g.run_generator(sb, bench, sp, taker_bps=2.0)
    assert len(res) == len(sp)
    assert all(r["n_trials"] == len(sp) for r in res)


def test_noise_gives_no_survivors():
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
    sp = g.generate_space(["momentum", "mean_reversion", "breakout"], [6, 12, 24])
    res = g.run_generator(sb, bench, sp, taker_bps=5.0)
    assert len(g.survivors(res)) == 0  # bruit -> le système dit honnêtement 'rien'


def test_planted_edge_surfaces():
    # prix alternants (mean-reversion forte, décorrélée du bench) -> doit survivre
    sb = {f"S{j}": _bars([100.0 if t % 2 == 0 else 103.0 for t in range(220)])
          for j in range(3)}
    bench = _bars([100 + 5 * math.sin(t / 7.0) for t in range(220)])  # autre dynamique
    # espace à 1 hypothèse (n_trials=1, pas de déflation multi-trial) : prouve que
    # le générateur SURFACE un edge réel quand il existe (la déflation multi-trial
    # est testée séparément : test_noise_gives_no_survivors + test_n_trials).
    sp = g.generate_space(["mean_reversion"], [1])
    res = g.run_generator(sb, bench, sp, taker_bps=0.0)
    surv = g.survivors(res)
    assert len(surv) >= 1
    assert any(r["hypothesis"]["family"] == "mean_reversion" for r in surv)


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
