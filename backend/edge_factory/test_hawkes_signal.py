"""Tests pour hawkes_signal.py — signal mean-reversion post-cascade.

Vérifie : (1) attribution de la pression de liq aux barres avec le bon SIGNE ;
(2) flags de cascade au-dessus du seuil d'intensité ; (3) la stratégie GAGNE quand
le prix rebondit après une cascade (V-shape) et qu'on entre CONTRE la pression ;
(4) no-look-ahead (la décision à i n'utilise que le passé). Synthétique → pas
besoin de la data réelle (qui attend la clé Coinalyze).

Run: cd backend/edge_factory && ../../.venv/bin/python test_hawkes_signal.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adapter as ad  # noqa: E402
import hawkes_signal as hs  # noqa: E402


def _bars(closes, step_ms=60000):
    return [ad.Bar(ts=i * step_ms, close=c) for i, c in enumerate(closes)]


def test_attribute_pressure_sign_long_is_negative():
    bars_ts = [0, 100, 200, 300]
    events = [{"ts": 50, "liquidated_side": "long", "notional": 1000.0},
              {"ts": 150, "liquidated_side": "short", "notional": 500.0}]
    pressure = hs.attribute_pressure_to_bars(events, bars_ts)
    assert pressure[0] == -1000.0  # long liquidé = sell-off = négatif
    assert pressure[1] == 500.0    # short liquidé = positif
    assert pressure[2] == 0.0


def test_cascade_flags_threshold():
    flags = hs.cascade_flags([0.1, 0.5, 1.2, 0.3, 2.0], threshold=1.0)
    assert flags == [False, False, True, False, True]


def test_strategy_profits_on_vshape_rebound():
    # cascade de longs liquidés à la barre 2 -> prix CHUTE puis REBONDIT (V-shape).
    # entrer CONTRE (long) à la barre de cascade doit capter le rebond -> return>0.
    closes = [100, 100, 90, 98, 99, 99]  # chute barre2 puis rebond
    bars = _bars(closes)
    flags = [False, False, True, False, False, False]
    pressure = [0.0, 0.0, -50000.0, 0.0, 0.0, 0.0]  # longs liquidés à la barre 2
    rets = hs.mean_reversion_returns(bars, flags, pressure,
                                     taker_bps=0.0, slippage_bps=0.0, exec_lag=1)
    # décision barre2 -> fill barre3 (98) -> sortie barre4 (99) = long gagne
    assert sum(rets) > 0, rets


def test_strategy_flat_when_no_cascade():
    closes = [100, 101, 102, 103, 104, 105]
    bars = _bars(closes)
    flags = [False] * 6
    pressure = [0.0] * 6
    rets = hs.mean_reversion_returns(bars, flags, pressure)
    assert all(r == 0.0 for r in rets)


def test_costs_reduce_returns():
    closes = [100, 100, 90, 98, 99, 99]
    bars = _bars(closes)
    flags = [False, False, True, False, False, False]
    pressure = [0.0, 0.0, -50000.0, 0.0, 0.0, 0.0]
    gross = sum(hs.mean_reversion_returns(bars, flags, pressure,
                                          taker_bps=0.0, slippage_bps=0.0))
    net = sum(hs.mean_reversion_returns(bars, flags, pressure,
                                        taker_bps=4.5, slippage_bps=5.0))
    assert net < gross


def test_intensity_series_on_grid():
    # intensité plus haute juste après un cluster d'events qu'avant
    events = [10.0, 10.1, 10.2, 10.3]
    grid = [5.0, 10.5, 50.0]
    inten = hs.intensity_series(events, grid, mu=0.1, alpha=1.0, beta=2.0)
    assert inten[1] > inten[0]   # juste après le cluster > avant
    assert inten[1] > inten[2]   # juste après > loin après (décroissance)


def test_no_look_ahead_future_bars_dont_change_past_returns():
    closes = [100, 100, 90, 98, 99, 99]
    bars = _bars(closes)
    flags = [False, False, True, False, False, False]
    pressure = [0.0, 0.0, -50000.0, 0.0, 0.0, 0.0]
    full = hs.mean_reversion_returns(bars, flags, pressure, exec_lag=1)
    # tronquer le futur (garder jusqu'à la barre 4) ne change pas les returns passés
    pre = hs.mean_reversion_returns(bars[:5], flags[:5], pressure[:5], exec_lag=1)
    for t in range(len(pre)):
        assert abs(pre[t] - full[t]) < 1e-12, (t, pre[t], full[t])


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
