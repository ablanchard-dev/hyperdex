"""Tests pour oi_signal.py — divergence open-interest / prix (positionnement crowded).

Hypothèse (recherche groundée) : un SPIKE d'OI sans mouvement de prix = buildup de
positions crowded → réversion. On mesure la divergence = z-score(ΔOI) − z-score(Δprix)
sur fenêtre rolling passé-only ; |divergence| > seuil → entrer CONTRE le côté crowded.
Signal de POSITIONNEMENT (distinct de momentum/reversion prix et de liq-spike).
No-look-ahead : z à i = passé only ; fill i+exec_lag.

Run: cd backend/edge_factory && ../../.venv/bin/python test_oi_signal.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adapter as ad  # noqa: E402
import oi_signal as oi  # noqa: E402


def _bars(closes):
    return [ad.Bar(ts=i * 3600000, close=c) for i, c in enumerate(closes)]


def test_oi_change_pct():
    oiv = [100.0, 110.0, 99.0]
    chg = oi.pct_change(oiv)
    assert abs(chg[0] - 0.10) < 1e-9       # +10%
    assert abs(chg[1] - (-0.10)) < 1e-9    # -10%


def test_divergence_zero_when_oi_tracks_price():
    # OI et prix montent ensemble -> pas de divergence (positionnement directionnel sain)
    n = 100
    bars = _bars([100 * 1.01 ** i for i in range(n)])
    oiv = [1000 * 1.01 ** i for i in range(n)]  # OI suit le prix
    div = oi.oi_price_divergence(bars, oiv, window=20)
    # dans la zone établie, divergence faible (corrélés)
    assert abs(div[-1]) < 1.5, div[-1]


def test_divergence_high_when_oi_spikes_flat_price():
    # OI fait un SPIKE ISOLÉ (saut brutal d'1 barre) sur prix plat = buildup crowded.
    # Un z-score détecte le saut isolé (pas une rampe régulière) → divergence forte.
    import random
    rng = random.Random(1)
    n = 60
    closes = [100.0 + rng.gauss(0, 0.02) for _ in range(n)]      # prix vraiment plat (bruit)
    oiv = [1000.0 + rng.gauss(0, 1.0) for _ in range(n)]          # OI stable...
    oiv[55] = 1400.0                                              # ...sauf SPIKE isolé +40%
    bars = _bars(closes)
    div = oi.oi_price_divergence(bars, oiv, window=20)
    assert abs(div[55]) > 3.0, div[55]  # le spike isolé d'OI = divergence très forte


def test_no_look_ahead_prefix_invariance():
    import random
    rng = random.Random(3)
    closes = [100.0]
    oiv = [1000.0]
    for _ in range(80):
        closes.append(closes[-1] * (1 + rng.uniform(-0.02, 0.02)))
        oiv.append(oiv[-1] * (1 + rng.uniform(-0.05, 0.05)))
    bars = _bars(closes)
    full = oi.oi_price_divergence(bars, oiv, window=20)
    pre = oi.oi_price_divergence(bars[:60], oiv[:60], window=20)
    for t in range(len(pre)):
        assert abs(pre[t] - full[t]) < 1e-12, (t, pre[t], full[t])


def test_strategy_contrarian_on_crowded_long():
    # OI explose à la hausse + prix monte un peu (longs crowded) PUIS reversion baisse.
    # divergence>0 (OI monte plus que prix) -> SHORT le crowded -> gagne sur la baisse.
    closes = [100.0] * 30 + [101, 102, 103, 100, 98, 97]  # petite hausse puis chute
    oiv = [1000.0] * 30 + [1100, 1250, 1450, 1450, 1450, 1450]  # OI explose
    bars = _bars(closes)
    rets = oi.oi_divergence_returns(bars, oiv, window=15, threshold=1.0,
                                    taker_bps=0.0, slippage_bps=0.0, exec_lag=1)
    assert sum(rets) > 0, rets


def test_no_trade_below_threshold():
    bars = _bars([100.0 + 0.01 * i for i in range(50)])
    oiv = [1000.0 + i for i in range(50)]  # OI et prix montent doucement ensemble
    rets = oi.oi_divergence_returns(bars, oiv, window=15, threshold=5.0,
                                    taker_bps=4.5, slippage_bps=5.0)
    assert all(r == 0.0 for r in rets)


def test_costs_reduce():
    closes = [100.0] * 30 + [101, 102, 103, 100, 98, 97]
    oiv = [1000.0] * 30 + [1100, 1250, 1450, 1450, 1450, 1450]
    bars = _bars(closes)
    gross = sum(oi.oi_divergence_returns(bars, oiv, window=15, threshold=1.0,
                                         taker_bps=0.0, slippage_bps=0.0))
    net = sum(oi.oi_divergence_returns(bars, oiv, window=15, threshold=1.0,
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
