#!/usr/bin/env python3
"""OI-divergence CROSS-SECTIONAL long-short — beta annulé par construction (P2-B).

À chaque barre : ranker l'univers par la divergence OI-prix (oi_signal, passé-only),
SHORT le top top_frac (les + crowded-long : OI monte >> prix) / LONG le bottom (les +
crowded-short). Portefeuille dollar-neutral → beta≈0 → un edge éventuel ressort en
ALPHA de positionnement. Réutilise oi_signal.oi_price_divergence + le squelette de
cross_sectional. No-look-ahead : divergence à i ≤ close i ; fill à i+exec_lag.
"""
from typing import Dict, List

from adapter import Bar
from oi_signal import oi_price_divergence


def oi_xs_backtest(symbol_bars: Dict[str, List[Bar]], oi: Dict[str, List[float]],
                   window: int = 48, top_frac: float = 0.3, taker_bps: float = 4.5,
                   slippage_bps: float = 5.0, exec_lag: int = 1) -> List[float]:
    """Returns long-short période-par-période sur la divergence OI-prix (dollar-neutral)."""
    syms = [s for s in symbol_bars if s in oi]
    closes = {s: [b.close for b in symbol_bars[s]] for s in syms}
    # pré-calcule la divergence par symbole (alignée aux barres, passé-only)
    div = {s: oi_price_divergence(symbol_bars[s], oi[s], window) for s in syms}
    n = min(min(len(closes[s]) for s in syms), min(len(div[s]) for s in syms))
    cost = (taker_bps + slippage_bps) / 1e4
    rets: List[float] = []
    prev_l: set = set()
    prev_s: set = set()
    for i in range(n - 1 - exec_lag):
        feats = {s: div[s][i] for s in syms}
        if len(feats) < 4:
            rets.append(0.0)
            continue
        ranked = sorted(feats, key=lambda s: feats[s])
        k = max(1, int(len(ranked) * top_frac))
        # div haute = crowded-long → SHORT ; div basse = crowded-short → LONG
        longs, shorts = set(ranked[:k]), set(ranked[-k:])

        def nret(s):
            p0 = closes[s][i + exec_lag]
            p1 = closes[s][i + 1 + exec_lag]
            return (p1 - p0) / p0 if p0 else 0.0

        long_r = sum(nret(s) for s in longs) / len(longs)
        short_r = sum(nret(s) for s in shorts) / len(shorts)
        gross = long_r - short_r
        turnover = len(longs ^ prev_l) + len(shorts ^ prev_s)
        trans = turnover * cost / max(1, len(longs) + len(shorts))
        rets.append(gross - trans)
        prev_l, prev_s = longs, shorts
    return rets
