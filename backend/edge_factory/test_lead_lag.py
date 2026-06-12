"""Tests pour lead_lag.py — lead-lag BTC→alts cross-sectional market-neutral.

Mécanisme : à chaque période, résiduel de chaque alt = son trailing return MOINS
beta·(trailing return de BTC), beta estimé sur une fenêtre PASSÉE séparée du signal.
Les retardataires (résiduel le + bas = ont sous-réagi au move de BTC) → LONG ;
les leaders (résiduel le + haut = ont sur-réagi) → SHORT. Dollar-neutral → le CRITIC
mesurera si le beta résiduel est ≈0 et s'il reste de l'alpha.

Run: cd backend/edge_factory && ../../.venv/bin/python test_lead_lag.py
"""
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adapter as ad  # noqa: E402
import lead_lag as ll  # noqa: E402


def _bars(closes):
    return [ad.Bar(ts=i, close=c) for i, c in enumerate(closes)]


def _walk(n, seed):
    random.seed(seed)
    p, out = 100.0, [100.0]
    for _ in range(n - 1):
        p *= 1 + random.uniform(-0.03, 0.03)
        out.append(p)
    return _bars(out)


def test_laggard_catches_up_makes_money():
    # BTC pump +10% ; B sous-réagit (+2% = retardataire) puis RATTRAPE période suivante,
    # D sur-réagit (+18% = leader) puis revient. Long B / short D -> PnL>0.
    # beta=1 fallback (historique plat avant l'event => var BTC nulle sur fenêtre beta).
    flat = [100.0] * 9
    A = _bars(flat + [110, 110, 110])
    B = _bars(flat + [102, 102, 110])   # retard puis rattrape à la période 10->11
    C = _bars(flat + [110, 110, 110])
    D = _bars(flat + [118, 118, 110])   # leader puis revient
    btc = _bars(flat + [110, 110, 110])
    out = ll.lead_lag_backtest({"A": A, "B": B, "C": C, "D": D}, btc,
                               lookback=1, top_frac=0.25, taker_bps=0.0,
                               beta_window=10, exec_lag=1)
    # décision à i=9 (signal visible), fill i+1=10, rattrapage 10->11
    assert out[9] > 0.1, out[9]


def test_no_look_ahead_prefix_invariance():
    # tronquer la série dans le futur ne doit PAS changer les returns passés.
    syms = {f"S{j}": _walk(120, seed=j) for j in range(6)}
    btc = _walk(120, seed=99)
    full = ll.lead_lag_backtest(syms, btc, lookback=3, top_frac=0.3,
                                taker_bps=2.0, beta_window=20, exec_lag=1)
    K = 110
    pre_syms = {s: b[:K] for s, b in syms.items()}
    pre = ll.lead_lag_backtest(pre_syms, btc[:K], lookback=3, top_frac=0.3,
                               taker_bps=2.0, beta_window=20, exec_lag=1)
    for t in range(len(pre)):
        assert abs(pre[t] - full[t]) < 1e-12, (t, pre[t], full[t])


def test_identical_symbols_zero_edge():
    # tous les symboles = MÊME série -> résiduels nuls -> long-short = 0 (hors coûts).
    same = _walk(80, seed=7)
    syms = {f"S{j}": same for j in range(5)}
    out = ll.lead_lag_backtest(syms, same, lookback=2, top_frac=0.4,
                               taker_bps=0.0, beta_window=20, exec_lag=1)
    assert max(abs(x) for x in out) < 1e-9, max(abs(x) for x in out)


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
