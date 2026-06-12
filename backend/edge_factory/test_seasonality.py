"""Tests pour seasonality.py — effets calendaires horaires/jour (angle distinct).

Hypothèse (recherche groundée : BTC returns 21-23h UTC positifs significatifs, 3-4h
pires ; Monday Asia Open). On apprend le return moyen par heure-UTC sur le TRAIN
uniquement, puis on trade la barre suivante selon le signe du profil appris (long si
l'heure est historiquement positive). ANTI-LOOK-AHEAD CRUCIAL : profil = train only,
jamais réajusté sur le test (sinon fuite). exec_lag=1.

Run: cd backend/edge_factory && ../../.venv/bin/python test_seasonality.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adapter as ad  # noqa: E402
import seasonality as sz  # noqa: E402

HOUR_MS = 3600_000


def _bars_hourly(closes, start_ts=0):
    return [ad.Bar(ts=start_ts + i * HOUR_MS, close=c) for i, c in enumerate(closes)]


def test_hour_of_ts():
    # 1779944400000 = 2026-05-28 05:00 UTC
    assert sz.hour_of(1779944400000) == 5
    assert sz.weekday_of(1779944400000) == 3  # jeudi


def test_hourly_profile_learns_positive_hours():
    # construit une série où l'heure 22 a TOUJOURS un return +, l'heure 3 toujours -
    closes = [100.0]
    for i in range(1, 240):  # 10 jours
        h = sz.hour_of(i * HOUR_MS)
        if h == 22:
            closes.append(closes[-1] * 1.01)
        elif h == 3:
            closes.append(closes[-1] * 0.99)
        else:
            closes.append(closes[-1] * (1 + (0.0001 if i % 2 else -0.0001)))
    bars = _bars_hourly(closes)
    prof = sz.hourly_profile(bars)
    assert prof[22] > 0, prof[22]
    assert prof[3] < 0, prof[3]


def test_no_look_ahead_profile_train_only():
    # le profil est appris sur train ; appliqué au test sans réajustement
    closes = [100.0 * (1.0001 ** i) for i in range(300)]
    bars = _bars_hourly(closes)
    rets_full = sz.seasonality_returns(bars, train_frac=0.7, taker_bps=2.0,
                                       slippage_bps=2.0, min_abs=0.0)
    # tronquer le futur APRÈS le cut ne doit pas changer les returns déjà émis
    n = len(bars)
    cut = int(n * 0.7)
    bars_short = bars[:cut + 30]
    rets_short = sz.seasonality_returns(bars_short, train_frac=cut / (cut + 30),
                                        taker_bps=2.0, slippage_bps=2.0, min_abs=0.0)
    # les premiers returns test doivent coïncider (même profil train, même barres)
    k = min(len(rets_full), len(rets_short), 20)
    for t in range(k):
        assert abs(rets_full[t] - rets_short[t]) < 1e-9, (t, rets_full[t], rets_short[t])


def test_profitable_when_pattern_persists():
    # heure 22 systématiquement + sur TOUTE la série (train ET test) -> profil appris
    # sur train capte +, trade test gagne.
    closes = [100.0]
    for i in range(1, 400):
        h = sz.hour_of(i * HOUR_MS)
        closes.append(closes[-1] * (1.008 if h == 22 else 0.9999))
    bars = _bars_hourly(closes)
    rets = sz.seasonality_returns(bars, train_frac=0.6, taker_bps=0.0,
                                  slippage_bps=0.0, min_abs=0.0)
    assert sum(rets) > 0, sum(rets)


def test_min_abs_filters_weak_hours():
    # min_abs élevé -> aucune heure ne dépasse le seuil -> 0 trade
    closes = [100.0 * (1 + 0.00001 * (i % 5 - 2)) for i in range(300)]
    bars = _bars_hourly(closes)
    rets = sz.seasonality_returns(bars, train_frac=0.7, taker_bps=4.5,
                                  slippage_bps=5.0, min_abs=1.0)  # seuil 100%/barre
    assert all(r == 0.0 for r in rets)


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
