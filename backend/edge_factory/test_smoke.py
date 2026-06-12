"""Smoke end-to-end Phase 1 : adapter -> returns -> CRITIC, sans venue réelle.

But : prouver que le TUYAU complet tourne, ET que le juge fait son travail.
Test décisif : un buy-and-hold (exposition marché pure) est par définition du
BETA — le CRITIC DOIT le rejeter (reason 'beta_deguise'). Si le smoke voyait
buy-and-hold comme un edge, le juge serait cassé.

Run: cd backend/edge_factory && ../../.venv/bin/python test_smoke.py
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adapter as ad  # noqa: E402
import smoke as sm  # noqa: E402


class _SyntheticVenue(ad.VenueAdapter):
    """Symbole = beta*benchmark + idiosyncrasie -> buy-hold = beta pur."""
    name = "synthetic"

    def __init__(self, n=300, beta=1.0, seed=0):
        rng = random.Random(seed)
        bench_px, sym_px = [100.0], [100.0]
        for _ in range(n):
            br = rng.gauss(0, 0.01)
            bench_px.append(bench_px[-1] * (1 + br))
            sr = beta * br + rng.gauss(0, 0.002)
            sym_px.append(sym_px[-1] * (1 + sr))
        self._b = [ad.Bar(ts=i, close=p) for i, p in enumerate(bench_px)]
        self._s = [ad.Bar(ts=i, close=p) for i, p in enumerate(sym_px)]

    def universe(self):
        return ["SYNTH"]

    def history(self, symbol, start, end):
        return self._s

    def fees(self, symbol):
        return ad.Fees(taker_bps=4.5, maker_bps=-1.0)

    def benchmark(self, start, end):
        return self._b


def test_smoke_pipe_runs_and_returns_verdict():
    v = sm.smoke_evaluate(_SyntheticVenue(seed=1), "SYNTH")
    assert "pass" in v and "reasons" in v and "gates" in v
    assert v["gates"]["beta_neutral"]["n"] > 0


def test_smoke_buyhold_is_rejected_as_beta():
    # buy-and-hold = exposition marché pure -> le juge DOIT crier beta_deguise
    v = sm.smoke_evaluate(_SyntheticVenue(beta=1.0, seed=2), "SYNTH")
    assert v["pass"] is False
    assert "beta_deguise" in v["reasons"]
    assert abs(v["gates"]["beta_neutral"]["beta"] - 1.0) < 0.25  # beta ~1 détecté


if __name__ == "__main__":
    fns = [val for k, val in sorted(globals().items()) if k.startswith("test_")]
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
