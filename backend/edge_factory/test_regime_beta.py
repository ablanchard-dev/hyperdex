"""Tests pour regime_beta.py — rotation de beta conditionnelle au régime BTC.

Hypothèse (recherche : beta alt se dilate en risk-on / comprime en risk-off) : en
régime BTC haussier+volatil (risk-on), les alts à HAUT beta amplifient → les détenir
surperforme ; en risk-off, le beta comprime → préférer bas beta. On RANKE par beta
trailing (passé only), et on choisit le côté selon le RÉGIME BTC trailing (passé only).
Le CRITIC tranchera si c'est de l'ALPHA ou juste du beta timé (probable beta_deguise).
No-look-ahead, fill i+exec_lag.

Run: cd backend/edge_factory && ../../.venv/bin/python test_regime_beta.py
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adapter as ad  # noqa: E402
import regime_beta as rb  # noqa: E402


def _bars(closes):
    return [ad.Bar(ts=i * 3600000, close=c) for i, c in enumerate(closes)]


def _walk(n, drift, vol, seed):
    rng = random.Random(seed)
    p, out = 100.0, [100.0]
    for _ in range(n - 1):
        p *= 1 + drift + rng.gauss(0, vol)
        out.append(p)
    return _bars(out)


def test_btc_regime_riskon_when_up_and_volatile():
    # BTC en forte hausse récente -> régime risk-on (+1)
    up = _bars([100 * 1.01 ** i for i in range(60)])
    reg = rb.btc_regime(up, window=24)
    assert reg[-1] == 1, reg[-1]
    # BTC en forte baisse -> risk-off (-1)
    down = _bars([100 * 0.99 ** i for i in range(60)])
    assert rb.btc_regime(down, window=24)[-1] == -1


def test_trailing_beta_ranks_high_low():
    # un alt qui bouge 2× BTC a un beta ~2 ; un alt qui bouge 0.5× a beta ~0.5
    rng = random.Random(1)
    n = 80
    btc_r = [rng.gauss(0, 0.02) for _ in range(n)]
    btc = [100.0]
    for r in btc_r:
        btc.append(btc[-1] * (1 + r))
    high = [100.0]
    low = [100.0]
    for r in btc_r:
        high.append(high[-1] * (1 + 2 * r))
        low.append(low[-1] * (1 + 0.5 * r))
    b_high = rb.trailing_beta(_bars(high), _bars(btc), window=40)
    b_low = rb.trailing_beta(_bars(low), _bars(btc), window=40)
    assert b_high[-1] > 1.5, b_high[-1]
    assert b_low[-1] < 1.0, b_low[-1]


def test_no_look_ahead_prefix_invariance():
    sb = {f"S{j}": _walk(100, 0.0005, 0.02, j) for j in range(5)}
    btc = _walk(100, 0.0005, 0.02, 99)
    full = rb.regime_beta_returns(sb, btc, beta_window=30, regime_window=24,
                                  top_frac=0.4, taker_bps=2.0, slippage_bps=2.0)
    K = 80
    sb_pre = {s: b[:K] for s, b in sb.items()}
    pre = rb.regime_beta_returns(sb_pre, btc[:K], beta_window=30, regime_window=24,
                                 top_frac=0.4, taker_bps=2.0, slippage_bps=2.0)
    for t in range(len(pre)):
        assert abs(pre[t] - full[t]) < 1e-12, (t, pre[t], full[t])


def test_returns_shape_and_finite():
    sb = {f"S{j}": _walk(120, 0.0, 0.02, j + 5) for j in range(6)}
    btc = _walk(120, 0.0, 0.02, 77)
    rets = rb.regime_beta_returns(sb, btc, beta_window=30, regime_window=24,
                                  top_frac=0.3, taker_bps=4.5, slippage_bps=5.0)
    assert len(rets) > 0 and all(r == r for r in rets)


def test_costs_reduce():
    sb = {f"S{j}": _walk(120, 0.0, 0.02, j) for j in range(6)}
    btc = _walk(120, 0.0, 0.02, 1)
    gross = sum(rb.regime_beta_returns(sb, btc, 30, 24, 0.3, 0.0, 0.0))
    net = sum(rb.regime_beta_returns(sb, btc, 30, 24, 0.3, 4.5, 5.0))
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
