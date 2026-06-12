"""Tests pour funding.py — famille funding carry (cross-sectional, HL perps).

Carry : short les perps à funding ÉLEVÉ (reçoit le funding des longs), long ceux
à funding bas/négatif → collecte le spread de funding, market-neutral. Le return =
PnL prix long-short + funding collecté − coûts. Mécanisme ≠ momentum (= funding).

Run: cd backend/edge_factory && ../../.venv/bin/python test_funding.py
"""
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adapter as ad  # noqa: E402
import funding as fd  # noqa: E402


def _flat_bars(n, price=100.0):
    return [ad.Bar(ts=i, close=price) for i in range(n)]


def test_collects_funding_spread_when_prices_flat():
    # 4 coins, funding constant [+,+,-,-], prix PLATS -> PnL = spread de funding pur
    n = 30
    pb = {c: _flat_bars(n) for c in "ABCD"}
    fund = {"A": [0.001] * n, "B": [0.001] * n,
            "C": [-0.001] * n, "D": [-0.001] * n}
    rets = fd.funding_carry_backtest(pb, fund, top_frac=0.5, taker_bps=0.0,
                                     exec_lag=0)
    # short A,B (reçoit +0.001) ; long C,D (funding -0.001 -> reçoit +0.001)
    # spread = avg(short f) - avg(long f) = 0.001 - (-0.001) = 0.002
    assert abs(statistics.mean(rets) - 0.002) < 1e-9


def test_no_funding_no_pnl_flat_prices():
    n = 30
    pb = {c: _flat_bars(n) for c in "ABCD"}
    fund = {c: [0.0] * n for c in "ABCD"}
    rets = fd.funding_carry_backtest(pb, fund, top_frac=0.5, taker_bps=0.0,
                                     exec_lag=0)
    assert abs(statistics.mean(rets)) < 1e-12


def test_price_move_against_carry_hurts():
    # short le high-funding coin dont le PRIX MONTE -> perte prix qui ronge le carry
    n = 20
    pb = {"A": [ad.Bar(ts=i, close=100.0 * 1.02 ** i) for i in range(n)],  # A pump
          "B": _flat_bars(n), "C": _flat_bars(n), "D": _flat_bars(n)}
    fund = {"A": [0.001] * n, "B": [0.0] * n, "C": [0.0] * n, "D": [-0.001] * n}
    # A a le funding le + haut -> on le SHORT ; son prix monte -> perte prix
    rets = fd.funding_carry_backtest(pb, fund, top_frac=0.25, taker_bps=0.0,
                                     exec_lag=0)
    # le short de A (qui pump) doit créer des returns négatifs nets
    assert statistics.mean(rets) < 0


def test_neutral_carry_collects_funding_flat_basis():
    # delta-neutral : funding>0 const, premium PLAT (Δ=0) -> collecte le funding pur
    n = 30
    r = fd.carry_neutral_backtest([0.001] * n, [0.0] * n, fee_bps=0.0, exec_lag=0)
    assert abs(statistics.mean(r) - 0.001) < 1e-9


def test_neutral_carry_negative_funding_long_side():
    # funding<0 -> on prend la jambe LONG perp (collecte le funding négatif), base plate
    n = 30
    r = fd.carry_neutral_backtest([-0.001] * n, [0.0] * n, fee_bps=0.0, exec_lag=0)
    assert abs(statistics.mean(r) - 0.001) < 1e-9


def test_neutral_carry_basis_move_against():
    # funding>0 mais premium MONTE vite (base contre le short perp) -> carry négatif
    n = 30
    prem = [0.003 * i for i in range(n)]   # +0.003/période >> funding 0.001
    r = fd.carry_neutral_backtest([0.001] * n, prem, fee_bps=0.0, exec_lag=0)
    assert statistics.mean(r) < 0


def test_smoothing_reduces_flip_churn():
    # funding qui alterne de signe chaque heure : smooth grand = position stable
    # (moins de flips => moins de fees) => meilleur return net.
    n = 60
    fund = [0.001 if i % 2 == 0 else -0.001 for i in range(n)]
    prem = [0.0] * n
    churn = fd.carry_neutral_backtest(fund, prem, fee_bps=10.0, exec_lag=0, smooth=1)
    stable = fd.carry_neutral_backtest(fund, prem, fee_bps=10.0, exec_lag=0, smooth=12)
    assert statistics.mean(stable) > statistics.mean(churn)


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
