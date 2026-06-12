"""Tests pour oi_xs.py — OI-divergence CROSS-SECTIONAL long-short (beta annulé).

Plutôt que contrarian par-coin (oi_signal), on RANKE l'univers par divergence OI-prix
à chaque barre : SHORT les + crowded-long (div haute), LONG les + crowded-short (div
basse) → dollar-neutral → beta≈0 par construction → isole l'alpha du POSITIONNEMENT.
Réutilise la divergence de oi_signal + le squelette long-short de cross_sectional.
No-look-ahead : divergence à i = passé only ; fill i+exec_lag.

Run: cd backend/edge_factory && ../../.venv/bin/python test_oi_xs.py
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adapter as ad  # noqa: E402
import oi_xs  # noqa: E402


def _bars(closes):
    return [ad.Bar(ts=i * 3600000, close=c) for i, c in enumerate(closes)]


def _walk(n, seed):
    rng = random.Random(seed)
    p, out = 100.0, [100.0]
    for _ in range(n - 1):
        p *= 1 + rng.uniform(-0.02, 0.02)
        out.append(p)
    return _bars(out)


def _flat_oi(n, base, seed):
    rng = random.Random(seed)
    return [base + rng.gauss(0, base * 0.01) for _ in range(n)]


def test_returns_length_and_neutral_shape():
    n = 120
    sb = {f"S{j}": _walk(n, j) for j in range(6)}
    oi = {f"S{j}": _flat_oi(n, 1000 + j * 100, j + 50) for j in range(6)}
    rets = oi_xs.oi_xs_backtest(sb, oi, window=20, top_frac=0.3,
                               taker_bps=4.5, slippage_bps=5.0, exec_lag=1)
    assert len(rets) > 0
    assert all(isinstance(r, float) for r in rets)


def test_no_look_ahead_prefix_invariance():
    n = 100
    sb = {f"S{j}": _walk(n, j + 10) for j in range(5)}
    oi = {f"S{j}": _flat_oi(n, 2000, j + 99) for j in range(5)}
    full = oi_xs.oi_xs_backtest(sb, oi, window=20, top_frac=0.3, taker_bps=2.0)
    K = 80
    sb_pre = {s: b[:K] for s, b in sb.items()}
    oi_pre = {s: v[:K] for s, v in oi.items()}
    pre = oi_xs.oi_xs_backtest(sb_pre, oi_pre, window=20, top_frac=0.3, taker_bps=2.0)
    for t in range(len(pre)):
        assert abs(pre[t] - full[t]) < 1e-12, (t, pre[t], full[t])


def test_crowded_long_reverts_makes_money():
    # 4 coins. À la barre de signal, S0 est crowded-long (OI explose, prix monte un peu)
    # puis revient ; S3 crowded-short puis revient. Long-short contrarian → gagne.
    n = 40
    flat = [100.0] * 30
    s0 = _bars(flat + [101, 102, 103, 100, 98, 96, 95, 95, 95, 95])     # pump puis dump
    s3 = _bars(flat + [99, 98, 97, 100, 102, 104, 105, 105, 105, 105])  # dump puis pump
    s1 = _bars([100.0] * n)
    s2 = _bars([100.0] * n)
    sb = {"S0": s0, "S1": s1, "S2": s2, "S3": s3}
    oi = {"S0": [1000.0] * 30 + [1100, 1250, 1450, 1450, 1450, 1450, 1450, 1450, 1450, 1450],
          "S3": [1000.0] * 30 + [1100, 1250, 1450, 1450, 1450, 1450, 1450, 1450, 1450, 1450],
          "S1": [1000.0] * n, "S2": [1000.0] * n}
    rets = oi_xs.oi_xs_backtest(sb, oi, window=15, top_frac=0.25,
                               taker_bps=0.0, slippage_bps=0.0, exec_lag=1)
    assert sum(rets) > 0, rets


def test_costs_reduce():
    n = 120
    sb = {f"S{j}": _walk(n, j) for j in range(6)}
    oi = {f"S{j}": _flat_oi(n, 1000, j) for j in range(6)}
    gross = sum(oi_xs.oi_xs_backtest(sb, oi, window=20, top_frac=0.3,
                                    taker_bps=0.0, slippage_bps=0.0))
    net = sum(oi_xs.oi_xs_backtest(sb, oi, window=20, top_frac=0.3,
                                  taker_bps=4.5, slippage_bps=5.0))
    assert net <= gross


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
