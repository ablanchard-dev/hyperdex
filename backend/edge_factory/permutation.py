#!/usr/bin/env python3
"""Bar-permutation significance test — 4e gate du CRITIC (pur-python).

Porté de a prior project (run_f2_permutation_test.py), réécrit sans numpy/pandas.

Null hypothesis : la performance vient du HASARD, pas de la structure temporelle.
On mélange les returns par-barre de chaque symbole (détruit l'autocorrélation /
le timing que le signal exploite), on reconstruit les prix (1ère barre préservée),
on RE-TOURNE la stratégie, et on compare le Sharpe réel à la distribution des
Sharpe mélangés. p_value = fraction des permutations qui battent le réel.

C'est un null STRINGENT : un vrai edge structurel doit battre ~95% des tirages
(p<0.05). Un Sharpe élevé qui échoue ici = data-mining (cf TSMOM a prior project p=0.88,
momentum J&T p=0.56). La stratégie est une callable `bars_by_symbol -> [returns]`.
"""
import random
import statistics
from typing import Callable, Dict, List

from adapter import Bar

Strategy = Callable[[Dict[str, List[Bar]]], List[float]]

P_THRESHOLD = 0.05


def _sharpe(xs: List[float]) -> float:
    if len(xs) < 2:
        return 0.0
    sd = statistics.pstdev(xs)
    return statistics.mean(xs) / sd if sd > 0 else 0.0


def _shuffle_bars(bars_by_symbol: Dict[str, List[Bar]],
                  rng: random.Random) -> Dict[str, List[Bar]]:
    """Mélange les returns par-barre de chaque symbole, reconstruit les prix.
    La 1ère barre (close de départ) et la longueur sont préservées."""
    out: Dict[str, List[Bar]] = {}
    for s, bars in bars_by_symbol.items():
        closes = [b.close for b in bars]
        rets = [(closes[i] - closes[i - 1]) / closes[i - 1] if closes[i - 1] else 0.0
                for i in range(1, len(closes))]
        rng.shuffle(rets)
        rebuilt = [closes[0]]
        for r in rets:
            rebuilt.append(rebuilt[-1] * (1 + r))
        out[s] = [Bar(ts=bars[i].ts, close=rebuilt[i]) for i in range(len(bars))]
    return out


def permutation_test(strategy: Strategy, bars_by_symbol: Dict[str, List[Bar]],
                     n_permutations: int = 500, seed: int = 42) -> dict:
    """Retourne p_value + diagnostics. significant = p_value < P_THRESHOLD."""
    real_sharpe = _sharpe(strategy(bars_by_symbol))
    rng = random.Random(seed)
    shuffled: List[float] = []
    for _ in range(n_permutations):
        perm = _shuffle_bars(bars_by_symbol, rng)
        try:
            shuffled.append(_sharpe(strategy(perm)))
        except Exception:
            shuffled.append(0.0)
    ge = sum(1 for s in shuffled if s >= real_sharpe)
    p_value = ge / len(shuffled) if shuffled else 1.0
    return {
        "p_value": p_value,
        "significant": p_value < P_THRESHOLD,
        "real_sharpe": real_sharpe,
        "mean_shuffled": statistics.mean(shuffled) if shuffled else 0.0,
        "n_permutations": n_permutations,
    }
