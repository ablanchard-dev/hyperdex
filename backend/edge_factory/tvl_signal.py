#!/usr/bin/env python3
"""TVL growth → token returns — fondamental on-chain cross-sectional (angle neuf, daily).

21 réfutations = prix/microstructure HORAIRE. Ici : signal FONDAMENTAL on-chain (DeFiLlama
TVL, gratuit) en DAILY. Hypothèse : le capital qui afflue dans un protocole (TVL ↑) précède
l'appréciation de son token → long forte-croissance-TVL / short décroissance, dollar-neutral.
Recherche mitigée (TVL/MCAP bands +15% vs Algorand non-prédictif, Granger non-causal) → le
CRITIC durci tranche. No-look-ahead : growth[j] = TVL[j]/TVL[j-lb] (passé), fill à j+exec_lag.
"""
from typing import Dict, List, Tuple


def tvl_growth(series: List[float], lookback: int = 7) -> List[float]:
    """Croissance relative de TVL sur `lookback` jours (len = len(series))."""
    out = [0.0] * len(series)
    for i in range(lookback, len(series)):
        prev = series[i - lookback]
        out[i] = (series[i] - prev) / prev if prev else 0.0
    return out


def align(tvl: Dict[int, float], px: Dict[int, float]) -> Tuple[List[int], List[float], List[float]]:
    """Aligne TVL et prix sur leurs dates communes (epoch daily), triées."""
    dates = sorted(set(tvl) & set(px))
    return dates, [tvl[d] for d in dates], [px[d] for d in dates]


def tvl_xs_backtest(tvl_by_token: Dict[str, Dict[int, float]],
                    px_by_token: Dict[str, Dict[int, float]],
                    lookback: int = 7, top_frac: float = 0.3,
                    taker_bps: float = 4.5, slippage_bps: float = 5.0,
                    exec_lag: int = 1) -> List[float]:
    """Cross-sectional long-short sur la croissance de TVL (dollar-neutral, no-look-ahead).
    À chaque jour : rank par growth TVL ; LONG top top_frac, SHORT bottom. Fill j+exec_lag."""
    # aligne chaque token sur ses dates TVL∩prix, puis sur les dates communes à TOUS
    aligned = {}
    for s in tvl_by_token:
        if s not in px_by_token:
            continue
        dates, t, p = align(tvl_by_token[s], px_by_token[s])
        if len(dates) > lookback + 2:
            aligned[s] = (dates, t, p)
    if len(aligned) < 4:
        return []
    common = sorted(set.intersection(*[set(v[0]) for v in aligned.values()]))
    if len(common) < lookback + 3:
        return []
    idx = {s: {d: k for k, d in enumerate(aligned[s][0])} for s in aligned}
    tvl_c = {s: [aligned[s][1][idx[s][d]] for d in common] for s in aligned}
    px_c = {s: [aligned[s][2][idx[s][d]] for d in common] for s in aligned}
    growth = {s: tvl_growth(tvl_c[s], lookback) for s in aligned}

    syms = list(aligned)
    n = len(common)
    cost = (taker_bps + slippage_bps) / 1e4
    rets: List[float] = []
    prev_l: set = set()
    prev_s: set = set()
    for i in range(lookback, n - 1 - exec_lag):
        feats = {s: growth[s][i] for s in syms}
        ranked = sorted(feats, key=lambda s: feats[s])
        k = max(1, int(len(ranked) * top_frac))
        longs, shorts = set(ranked[-k:]), set(ranked[:k])

        def nret(s):
            p0 = px_c[s][i + exec_lag]
            p1 = px_c[s][i + 1 + exec_lag]
            return (p1 - p0) / p0 if p0 else 0.0

        long_r = sum(nret(s) for s in longs) / len(longs)
        short_r = sum(nret(s) for s in shorts) / len(shorts)
        gross = long_r - short_r
        turnover = len(longs ^ prev_l) + len(shorts ^ prev_s)
        trans = turnover * cost / max(1, len(longs) + len(shorts))
        rets.append(gross - trans)
        prev_l, prev_s = longs, shorts
    return rets
