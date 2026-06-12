#!/usr/bin/env python3
"""Effets calendaires horaires — angle distinct (recherche : BTC 21-23h UTC +, 3-4h −).

On apprend le return moyen par heure-UTC sur le TRAIN seulement (profil), puis on trade
la barre suivante selon le signe du profil de SON heure (long si l'heure est
historiquement positive au-delà d'un min_abs). ANTI-LOOK-AHEAD : le profil n'est JAMAIS
réajusté sur le test. exec_lag=1 (décision à close i sur le profil train, fill i+1).

Réf : QuantPedia intraday anomalies BTC, mlquants day-of-week. La source prévient que
ces effets ne sont « peut-être pas exploitables à l'échelle » → le CRITIC durci tranche.
"""
import datetime
from typing import Dict, List

from adapter import Bar

_UTC = datetime.timezone.utc


def hour_of(ts_ms: int) -> int:
    return datetime.datetime.fromtimestamp(ts_ms / 1000, _UTC).hour


def weekday_of(ts_ms: int) -> int:
    return datetime.datetime.fromtimestamp(ts_ms / 1000, _UTC).weekday()


def _returns(closes: List[float]) -> List[float]:
    return [(closes[i] - closes[i - 1]) / closes[i - 1] if closes[i - 1] else 0.0
            for i in range(1, len(closes))]


def hourly_profile(bars: List[Bar]) -> Dict[int, float]:
    """Return moyen par heure-UTC. Le return à la barre i est attribué à l'heure de la
    barre i (le mouvement DURANT cette heure). Profil = moyenne sur les barres fournies."""
    closes = [b.close for b in bars]
    rets = _returns(closes)
    acc: Dict[int, list] = {}
    for i in range(1, len(bars)):
        h = hour_of(bars[i].ts)
        acc.setdefault(h, []).append(rets[i - 1])
    return {h: (sum(v) / len(v) if v else 0.0) for h, v in acc.items()}


def seasonality_returns(bars: List[Bar], train_frac: float = 0.7,
                        taker_bps: float = 4.5, slippage_bps: float = 5.0,
                        min_abs: float = 0.0, exec_lag: int = 1) -> List[float]:
    """Profil horaire appris sur train → trade le test. À la barre i (test), on regarde
    l'heure de la barre i+exec_lag (celle qu'on va trader) ; si |profil[h]| > min_abs,
    position = signe(profil[h]) ; return = pos * ret(i+lag→i+1+lag) − coûts."""
    closes = [b.close for b in bars]
    n = len(closes)
    cut = int(n * train_frac)
    if cut < 24 or n - cut < 3:
        return []
    profile = hourly_profile(bars[:cut])  # TRAIN ONLY (no-look-ahead)
    cost = (taker_bps + slippage_bps) / 1e4
    rets: List[float] = []
    for i in range(cut, n - 1 - exec_lag):
        # le mouvement tradé close[i+lag]→close[i+1+lag] est attribué (convention du
        # profil) à l'heure de bars[i+1+lag]. Heure = calendaire/déterministe → connue
        # d'avance sans regarder le prix (pas de look-ahead).
        h = hour_of(bars[i + 1 + exec_lag].ts)
        p = profile.get(h, 0.0)
        if abs(p) <= min_abs:
            rets.append(0.0)
            continue
        direction = 1.0 if p > 0 else -1.0
        p0 = closes[i + exec_lag]
        p1 = closes[i + 1 + exec_lag]
        raw = direction * (p1 - p0) / p0 if p0 else 0.0
        rets.append(raw - cost)
    return rets
