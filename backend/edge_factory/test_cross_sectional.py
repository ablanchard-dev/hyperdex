"""Tests pour cross_sectional.py — signaux long-short MARKET-NEUTRAL.

À chaque période : ranker l'univers par une feature, long le top / short le bottom
(dollar-neutral). Beta ≈ 0 par construction → si edge, il ressort en alpha.
Tests : feature connue, MARKET-NEUTRALITÉ (facteur commun → long-short≈0, pas de
beta fabriqué), edge cross-sectional planté → capté, jugement via CRITIC.

Run: cd backend/edge_factory && ../../.venv/bin/python test_cross_sectional.py
"""
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adapter as ad  # noqa: E402
import cross_sectional as xs  # noqa: E402


def _bars(closes):
    return [ad.Bar(ts=i, close=c) for i, c in enumerate(closes)]


def test_xs_momentum_feature():
    # trailing return sur lookback : [100,...,110] lb=2 -> (110-prev)/prev
    f = xs.XS_FEATURES["xs_momentum"]([100, 105, 110], {"lookback": 2})
    assert abs(f - (110 - 100) / 100) < 1e-9
    assert xs.XS_FEATURES["xs_momentum"]([100], {"lookback": 2}) is None


import critic as _crit  # noqa: E402


def _factor_universe(seed, drift_per_symbol=False, T=260, k=10):
    """Modèle à facteur : returns_s = commun + idiosyncratique. bench = commun."""
    rng = random.Random(seed)
    common = [rng.gauss(0.0004, 0.012) for _ in range(T)]
    sb = {}
    for j in range(k):
        d = (0.0006 * (j - k / 2)) if drift_per_symbol else 0.0  # edge planté ?
        idio = [d + rng.gauss(0, 0.006) for _ in range(T)]
        px = [100.0]
        for t in range(T):
            px.append(px[-1] * (1 + common[t] + idio[t]))
        sb[f"S{j}"] = _bars(px)
    bpx = [100.0]
    for t in range(T):
        bpx.append(bpx[-1] * (1 + common[t]))
    return sb, _bars(bpx)


def test_market_neutral_beta_near_zero():
    # facteur commun + idio iid (pas d'edge) : le long-short ANNULE le facteur
    # commun -> beta ≈ 0 (market-neutral), même si mean != 0.
    sb, bench = _factor_universe(3, drift_per_symbol=False)
    ls = xs.cross_sectional_backtest(sb, "xs_momentum", {"lookback": 20},
                                     top_frac=0.3, taker_bps=0.0)
    from adapter import returns_from_bars
    br = returns_from_bars(bench)
    m = min(len(ls), len(br))
    r = _crit.beta_neutral_alpha(ls[:m], br[:m])
    assert abs(r["beta"]) < 0.35  # le facteur commun est annulé par le long-short


def test_planted_cross_sectional_edge():
    # drift persistant différent par symbole : les winners CONTINUENT
    # -> xs_momentum long-short capte le spread -> mean > 0
    sb = {}
    for j in range(10):
        drift = 0.0005 * (j - 5)  # de -0.0025 à +0.002 par période
        sb[f"S{j}"] = _bars([100.0 * (1 + drift) ** t for t in range(260)])
    rets = xs.cross_sectional_backtest(sb, "xs_momentum",
                                       {"lookback": 20}, top_frac=0.3,
                                       taker_bps=0.0)
    assert statistics.mean(rets) > 0  # capte la persistance des winners


def test_cross_sectional_pbo_returns_valid():
    # PBO/CSCV sur la matrice des hypothèses cross-sectional : valeur dans [0,1]
    sb, _ = _factor_universe(8, drift_per_symbol=False)
    specs = [{"name": f"m{lb}", "rationale": "r",
              "signal": {"type": "xs_momentum",
                         "params": {"lookback": lb, "top_frac": 0.3}}}
             for lb in (10, 20, 40, 60)]
    pbo = xs.cross_sectional_pbo(sb, specs, taker_bps=0.0, S=8)
    assert 0.0 <= pbo <= 1.0


def test_exec_lag_parity():
    # parité backtest=live : décider à close i, REMPLIR à i+1 (pas de fill same-bar).
    # lag=1 doit capter des returns différents de lag=0 + 1 période en moins.
    sb = {f"S{j}": _bars([100.0 * (1 + 0.0005 * (j - 5)) ** t for t in range(60)])
          for j in range(10)}
    r0 = xs.cross_sectional_backtest(sb, "xs_momentum", {"lookback": 10}, 0.3,
                                     taker_bps=0.0, exec_lag=0)
    r1 = xs.cross_sectional_backtest(sb, "xs_momentum", {"lookback": 10}, 0.3,
                                     taker_bps=0.0, exec_lag=1)
    assert len(r1) == len(r0) - 1          # 1 période d'exécution décalée
    assert r0[:len(r1)] != r1               # capture décalée (timing live)


def test_borrow_cost_reduces_long_short():
    # le short paie un borrow -> rendement long-short PLUS BAS qu'en gratuit
    sb = {f"S{j}": _bars([100.0 * (1 + 0.0005 * (j - 5)) ** t for t in range(260)])
          for j in range(10)}
    free = xs.cross_sectional_backtest(sb, "xs_momentum", {"lookback": 20},
                                       top_frac=0.3, taker_bps=0.0)
    borr = xs.cross_sectional_backtest(sb, "xs_momentum", {"lookback": 20},
                                       top_frac=0.3, taker_bps=0.0,
                                       borrow_bps_annual=2000.0)
    assert statistics.mean(borr) < statistics.mean(free)


def test_judge_cross_sectional_beta_near_zero():
    # univers à facteur + drift idiosyncratique planté : le CRITIC doit voir
    # un beta ≈ 0 (market-neutral) — l'edge, s'il passe, sera de l'ALPHA.
    sb, bench = _factor_universe(5, drift_per_symbol=True)
    specs = [{"name": "xsm", "rationale": "r",
              "signal": {"type": "xs_momentum",
                         "params": {"lookback": 20, "top_frac": 0.3}}}]
    res = xs.judge_cross_sectional(sb, bench, specs, taker_bps=0.0)
    assert len(res) == 1
    assert abs(res[0]["gates"]["beta_neutral"]["beta"]) < 0.5  # ~ market-neutral


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
