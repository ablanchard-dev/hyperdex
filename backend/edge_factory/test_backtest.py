"""Tests pour backtest.py — backtest de signal SANS look-ahead.

Le test crucial = no-look-ahead : les returns des barres anciennes ne doivent PAS
changer quand on ajoute des barres futures (sinon fuite = l'illusion classique).

Run: cd backend/edge_factory && ../../.venv/bin/python test_backtest.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adapter as ad  # noqa: E402
import backtest as bt  # noqa: E402


def _bars(closes):
    return [ad.Bar(ts=i, close=c) for i, c in enumerate(closes)]


def test_momentum_signal_basic():
    # close monte vs il y a `lookback` -> +1 ; baisse -> -1 ; pas assez d'hist -> 0
    assert bt.ts_momentum_signal([1, 2, 3], lookback=2) == 1   # 3 > 1
    assert bt.ts_momentum_signal([3, 2, 1], lookback=2) == -1  # 1 < 3
    assert bt.ts_momentum_signal([1, 2], lookback=2) == 0      # hist insuffisant


def test_no_lookahead_prefix():
    # les returns calculés sur les 1ères barres ne changent pas si on rajoute du futur
    closes = [10, 11, 12, 11, 13, 14, 12, 15, 16, 14, 13, 17]
    full = bt.backtest_symbol(_bars(closes), lookback=2, taker_bps=0.0)
    trunc = bt.backtest_symbol(_bars(closes[:7]), lookback=2, taker_bps=0.0)
    # trunc produit len(closes[:7])-1 = 6 returns ; ils doivent égaler le préfixe de full
    for i in range(len(trunc)):
        assert abs(full[i] - trunc[i]) < 1e-12, (i, full[i], trunc[i])


def test_uptrend_momentum_positive():
    # tendance haussière monotone -> momentum long -> somme des returns > 0
    closes = [100 * (1.01 ** i) for i in range(40)]
    rets = bt.backtest_symbol(_bars(closes), lookback=3, taker_bps=0.0)
    assert sum(rets) > 0


def test_return_calc_exact():
    # 2 barres : sig décidé à barre 0 (hist insuffisant -> 0) -> return 0
    # série [100, 110, 99] lookback=1 :
    #  i=0 sig(closes[:1],1)=0 (hist insuff) -> ret = 0*... = 0
    #  i=1 sig(closes[:2],1)= +1 (110>100) -> mkt (99-110)/110, cost change 0->1
    rets = bt.backtest_symbol(_bars([100, 110, 99]), lookback=1, taker_bps=10.0)
    assert abs(rets[0] - 0.0) < 1e-12
    mkt = (99 - 110) / 110
    expected = 1 * mkt - abs(1 - 0) * (10.0 / 1e4)
    assert abs(rets[1] - expected) < 1e-12


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
