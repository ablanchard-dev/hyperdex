"""Tests pour tvl_signal.py — TVL growth → token returns (fondamental on-chain, daily).

Angle NEUF (21 réfut = prix/microstructure horaire ; ici = fondamental on-chain DAILY).
Hypothèse cross-sectional : long les tokens dont la TVL CROÎT le plus (capital afflue =
adoption → prix suit), short ceux qui décroissent. Recherche mitigée (TVL/MCAP bands +15%
mais Algorand dit non-prédictif, Granger non-causal) → vaut un test propre. Data DeFiLlama
gratuite (TVL daily). No-look-ahead : croissance TVL à j = TVL[j]/TVL[j-lb] (passé), fill j+1.

Run: cd backend/edge_factory && ../../.venv/bin/python test_tvl_signal.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tvl_signal as tv  # noqa: E402


def test_tvl_growth_pct():
    g = tv.tvl_growth([100.0, 110.0, 121.0], lookback=1)
    assert abs(g[1] - 0.10) < 1e-9 and abs(g[2] - 0.10) < 1e-9


def test_align_tvl_to_price_dates():
    # TVL et prix sur dates différentes → aligné sur dates communes (daily epoch)
    tvl = {1000: 100.0, 2000: 110.0, 3000: 120.0}
    px = {2000: 5.0, 3000: 5.5, 4000: 6.0}
    dates, t_al, p_al = tv.align(tvl, px)
    assert dates == [2000, 3000]
    assert t_al == [110.0, 120.0] and p_al == [5.0, 5.5]


def test_xs_backtest_shape_and_neutral():
    # 4 tokens, TVL et prix daily synthétiques → returns long-short dollar-neutral
    import random
    rng = random.Random(1)
    n = 60
    dates = list(range(n))
    tvl = {f"T{j}": {d: 1000.0 * (1 + 0.01 * j) ** d for d in dates} for j in range(4)}
    px = {f"T{j}": {d: 100.0 * (1 + rng.uniform(-0.02, 0.02)) ** d for d in dates}
          for j in range(4)}
    rets = tv.tvl_xs_backtest(tvl, px, lookback=5, top_frac=0.5,
                              taker_bps=4.5, slippage_bps=5.0, exec_lag=1)
    assert len(rets) > 0 and all(r == r for r in rets)


def test_no_look_ahead_prefix_invariance():
    n = 50
    dates = list(range(n))
    tvl = {f"T{j}": {d: 1000.0 + 10 * d + j for d in dates} for j in range(4)}
    px = {f"T{j}": {d: 100.0 + (d * (j + 1)) % 7 for d in dates} for j in range(4)}
    full = tv.tvl_xs_backtest(tvl, px, lookback=5, top_frac=0.5, taker_bps=2.0)
    tvl_pre = {k: {d: v for d, v in s.items() if d < 40} for k, s in tvl.items()}
    px_pre = {k: {d: v for d, v in s.items() if d < 40} for k, s in px.items()}
    pre = tv.tvl_xs_backtest(tvl_pre, px_pre, lookback=5, top_frac=0.5, taker_bps=2.0)
    for t in range(len(pre)):
        assert abs(pre[t] - full[t]) < 1e-12, (t, pre[t], full[t])


def test_high_tvl_growth_leads_price_makes_money():
    # T0 a TVL qui explose puis prix qui monte ; T3 TVL chute puis prix baisse.
    # long T0 / short T3 → si la TVL LEAD le prix, gagne.
    n = 40
    dates = list(range(n))
    def ramp(d, base, rate): return base * (1 + rate) ** d
    tvl = {"T0": {d: ramp(d, 1000, 0.05) for d in dates},   # TVL explose
           "T1": {d: 1000.0 for d in dates},
           "T2": {d: 1000.0 for d in dates},
           "T3": {d: ramp(d, 1000, -0.03) for d in dates}}  # TVL chute
    # prix suit la TVL avec retard de 2 jours
    px = {"T0": {d: 100.0 * (1.02 ** max(0, d - 2)) for d in dates},
          "T1": {d: 100.0 for d in dates},
          "T2": {d: 100.0 for d in dates},
          "T3": {d: 100.0 * (0.985 ** max(0, d - 2)) for d in dates}}
    rets = tv.tvl_xs_backtest(tvl, px, lookback=5, top_frac=0.25,
                              taker_bps=0.0, slippage_bps=0.0, exec_lag=1)
    assert sum(rets) > 0, sum(rets)


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
