"""Tests pour costs.py — modèle de coûts réaliste (transaction + borrow short).

Le manque clé de l'audit : un long-short market-neutral n'est PAS gratuit — le
short paie un borrow cost (et certains small-caps sont hard-to-borrow / impossibles
à shorter). + slippage/spread au-delà du taker fixe.

Run: cd backend/edge_factory && ../../.venv/bin/python test_costs.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import costs as c  # noqa: E402


def test_transaction_cost_known():
    # changer la position de 2 unités (flip -1->+1), taker 4.5 + slip 5.5 = 10 bps
    assert abs(c.transaction_cost(2.0, taker_bps=4.5, slippage_bps=5.5) - 0.0020) < 1e-12
    # signe indifférent (coût sur |delta|)
    assert c.transaction_cost(-2.0, 4.5, 5.5) == c.transaction_cost(2.0, 4.5, 5.5)


def test_transaction_cost_no_change():
    assert c.transaction_cost(0.0, 4.5, 5.5) == 0.0


def test_borrow_only_on_shorts():
    # short 1 unité, 252 bps/an, 1 jour -> 1 * 0.0252 * (1/252) = 0.0001
    assert abs(c.borrow_cost(-1.0, borrow_bps_annual=252.0, period_days=1) - 0.0001) < 1e-12
    assert c.borrow_cost(1.0, 252.0) == 0.0    # long : pas de borrow
    assert c.borrow_cost(0.0, 252.0) == 0.0    # flat : rien


def test_borrow_scales_size_and_days():
    # short 1, 252 bps, 5 jours -> 5 * 0.0001
    assert abs(c.borrow_cost(-1.0, 252.0, period_days=5) - 0.0005) < 1e-12
    # short 0.5 unité -> moitié
    assert abs(c.borrow_cost(-0.5, 252.0, 1) - 0.00005) < 1e-12


def test_hard_to_borrow_is_expensive():
    # hard-to-borrow (5000 bps = 50%/an) >> general collateral
    hi = c.borrow_cost(-1.0, 5000.0, 1)
    lo = c.borrow_cost(-1.0, 252.0, 1)
    assert hi > lo * 15


def test_period_cost_flip_to_short():
    # prev=0 -> new=-1 : transaction (1*10bps) + borrow (1 jour, 252bps)
    pc = c.period_cost(0.0, -1.0, taker_bps=4.5, slippage_bps=5.5,
                       borrow_bps_annual=252.0)
    assert abs(pc - (0.0010 + 0.0001)) < 1e-12


def test_period_cost_hold_long_no_borrow():
    # rester long (prev=1,new=1) : pas de transaction, pas de borrow
    assert c.period_cost(1.0, 1.0, 4.5, 5.5, 252.0) == 0.0


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
