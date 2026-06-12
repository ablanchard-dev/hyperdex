"""Tests pour oi_volume_signal.py — ratio ΔOI/volume (accumulation passive vs active).

Hypothèse (recherche groundée) : OI qui gonfle SANS volume proportionnel = accumulation
PASSIVE (positions tenues, peu de churn) → tend à continuer ; OI qui bouge AVEC gros
volume = participation active/spéculative. Signal = z-score du ratio |ΔOI|/volume
(rolling passé-only) ; ratio extrême haut = accumulation passive → suivre la direction
de l'OI (momentum de positionnement). No-look-ahead, fill i+exec_lag.

Run: cd backend/edge_factory && ../../.venv/bin/python test_oi_volume.py
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adapter as ad  # noqa: E402
import oi_volume_signal as ov  # noqa: E402


def _bars(closes):
    return [ad.Bar(ts=i * 3600000, close=c) for i, c in enumerate(closes)]


def test_oi_vol_ratio_high_when_oi_moves_no_volume():
    # OI bouge de +5% avec volume faible -> ratio élevé ; même ΔOI gros volume -> ratio bas
    oi = [1000.0, 1050.0]
    vol_low = [100.0, 100.0]
    vol_high = [100.0, 10000.0]
    r_low = ov.oi_vol_ratio(oi, vol_low)
    r_high = ov.oi_vol_ratio(oi, vol_high)
    assert r_low[0] > r_high[0], (r_low, r_high)


def test_ratio_zero_volume_safe():
    # volume nul -> pas de division par zéro (ratio 0 = pas de signal)
    r = ov.oi_vol_ratio([1000.0, 1050.0], [0.0, 0.0])
    assert all(x == x for x in r)  # pas de NaN


def test_signal_length_and_no_look_ahead():
    rng = random.Random(3)
    n = 90
    closes = [100.0]
    oi = [1000.0]
    vol = []
    for _ in range(n):
        closes.append(closes[-1] * (1 + rng.uniform(-0.02, 0.02)))
        oi.append(oi[-1] * (1 + rng.uniform(-0.04, 0.04)))
        vol.append(abs(rng.gauss(1000, 300)) + 1)
    vol.append(abs(rng.gauss(1000, 300)) + 1)
    bars = _bars(closes)
    full = ov.oi_volume_returns(bars, oi, vol, window=20, threshold=2.0,
                                taker_bps=2.0, slippage_bps=2.0, exec_lag=1)
    K = 70
    pre = ov.oi_volume_returns(bars[:K], oi[:K], vol[:K], window=20, threshold=2.0,
                               taker_bps=2.0, slippage_bps=2.0, exec_lag=1)
    for t in range(len(pre)):
        assert abs(pre[t] - full[t]) < 1e-12, (t, pre[t], full[t])


def test_passive_accumulation_continues_makes_money():
    # Fond bruité RÉALISTE (OI/vol jamais parfaitement plats → variance passée non nulle
    # pour le z-score) PUIS accumulation passive haussière (OI gonfle fort + volume bas),
    # prix continue de monter -> suivre l'OI (LONG) gagne.
    rng = random.Random(5)
    base_oi = [1000.0 + rng.gauss(0, 5) for _ in range(30)]      # OI bruité ~plat
    base_vol = [5000.0 + rng.gauss(0, 200) for _ in range(30)]   # vol bruité
    base_px = [100.0 + rng.gauss(0, 0.05) for _ in range(30)]
    closes = base_px + [100, 101, 102, 103, 104, 105, 106, 107]
    oi = base_oi + [1100, 1250, 1400, 1550, 1700, 1850, 2000, 2150]  # OI explose
    vol = base_vol + [200, 200, 200, 200, 200, 200, 200, 200]        # volume effondré
    bars = _bars(closes)
    rets = ov.oi_volume_returns(bars, oi, vol, window=15, threshold=1.0,
                                taker_bps=0.0, slippage_bps=0.0, exec_lag=1)
    assert sum(rets) > 0, rets


def test_no_trade_below_threshold():
    bars = _bars([100.0 + 0.01 * i for i in range(50)])
    oi = [1000.0 + i for i in range(50)]
    vol = [1000.0] * 50
    rets = ov.oi_volume_returns(bars, oi, vol, window=15, threshold=10.0,
                                taker_bps=4.5, slippage_bps=5.0)
    assert all(r == 0.0 for r in rets)


def test_costs_reduce():
    closes = [100.0] * 30 + [100, 101, 102, 103, 104, 105]
    oi = [1000.0] * 30 + [1100, 1200, 1300, 1400, 1500, 1600]
    vol = [5000.0] * 30 + [200, 200, 200, 200, 200, 200]
    bars = _bars(closes)
    gross = sum(ov.oi_volume_returns(bars, oi, vol, window=15, threshold=1.0,
                                     taker_bps=0.0, slippage_bps=0.0))
    net = sum(ov.oi_volume_returns(bars, oi, vol, window=15, threshold=1.0,
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
