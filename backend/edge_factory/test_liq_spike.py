"""Tests pour liq_spike.py — signal contrarian sur spike de liquidation horaire.

Adapté à la data AGRÉGÉE par bucket (Coinalyze horaire) : pas de Hawkes ponctuel
(inadapté à des events bucketés), mais détection de SPIKE via z-score rolling de la
liquidation nette par barre, puis entrée CONTRE (contrarian, recherche groundée :
liquidations = indicateur contrarian, snapback post-extrême). No-look-ahead : z-score
à la barre i n'utilise que les barres < i ; fill à i+exec_lag.

Run: cd backend/edge_factory && ../../.venv/bin/python test_liq_spike.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adapter as ad  # noqa: E402
import liq_spike as ls  # noqa: E402


def _bars(closes):
    return [ad.Bar(ts=i * 3600000, close=c) for i, c in enumerate(closes)]


def test_rolling_zscore_past_only():
    # fond bruité (variance non nulle, réaliste) + spike final -> z élevé.
    base = [1.0, 1.2, 0.8, 1.1, 0.9, 1.3, 0.7, 1.0, 1.1, 0.9] * 2
    series = base + [10.0]        # spike à la fin
    z = ls.rolling_zscore(series, window=10)
    assert z[-1] > 3.0           # le spike final est très au-dessus de sa fenêtre passée
    # variance nulle (fenêtre plate) -> z=0 (pas de signal, cas dégénéré géré)
    assert ls.rolling_zscore([5.0] * 15, window=10)[-1] == 0.0


def test_no_look_ahead_prefix_invariance():
    series = [float(i % 3) for i in range(50)]
    full = ls.rolling_zscore(series, window=10)
    pre = ls.rolling_zscore(series[:40], window=10)
    for t in range(len(pre)):
        assert abs(pre[t] - full[t]) < 1e-12, (t, pre[t], full[t])


def test_signal_contrarian_long_after_long_liquidation_spike():
    # fond bruité de liquidations + SPIKE de longs liquidés à i=5 -> prix chute i=6
    # puis rebond -> signal LONG (contrarian) capte le snapback. fill i+1.
    closes = [100, 100, 100, 100, 100, 100, 92, 99, 99, 99]
    bars = _bars(closes)
    # bruit de fond non nul (variance > 0 pour le z-score) + spike négatif à i=5
    net_liq = [-1000, 2000, -1500, 1200, -800, -500000, 0, 0, 0, 0]
    rets = ls.liq_spike_returns(bars, net_liq, z_window=5, z_threshold=1.5,
                                taker_bps=0.0, slippage_bps=0.0, exec_lag=1)
    assert sum(rets) > 0, rets


def test_no_trade_when_no_spike():
    closes = [100, 101, 102, 103, 104, 105, 106]
    bars = _bars(closes)
    net_liq = [0.0] * 7
    rets = ls.liq_spike_returns(bars, net_liq, z_window=3, z_threshold=2.0)
    assert all(r == 0.0 for r in rets)


def test_costs_applied():
    closes = [100, 100, 100, 100, 100, 100, 92, 99, 99, 99]
    bars = _bars(closes)
    net_liq = [-1000, 2000, -1500, 1200, -800, -500000, 0, 0, 0, 0]
    gross = sum(ls.liq_spike_returns(bars, net_liq, z_window=5, z_threshold=1.5,
                                     taker_bps=0.0, slippage_bps=0.0))
    net = sum(ls.liq_spike_returns(bars, net_liq, z_window=5, z_threshold=1.5,
                                   taker_bps=4.5, slippage_bps=5.0))
    assert net < gross


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
