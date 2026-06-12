#!/usr/bin/env python3
"""Famille FUNDING CARRY (cross-sectional, HL perps) — edge documenté non-testé.

Short les perps à funding ÉLEVÉ (les longs sur-leveragés paient les shorts), long
ceux à funding bas/négatif → collecte le spread de funding, market-neutral.
Return période = PnL prix long-short + funding collecté − coûts. Le carry est le
plus robuste de la littérature (Sharpe 3-6) mais modeste ; jugé par le CRITIC.

Données : InfoClient.funding_history (fundingRate horaire), aligné aux candles.
"""
import statistics
from typing import Dict, List

import costs as _costs
from adapter import Bar


def funding_carry_backtest(price_bars: Dict[str, List[Bar]],
                           funding: Dict[str, List[float]], top_frac: float,
                           taker_bps: float, slippage_bps: float = 0.0,
                           exec_lag: int = 1) -> List[float]:
    """Returns période-par-période du carry cross-sectional (no-look-ahead).

    Décision à i (rank par funding[i]) : SHORT top (funding haut), LONG bottom.
    Sur la période détenue [i+lag, i+1+lag] : PnL prix long-short + funding collecté
    (short reçoit +f, long paie -f → spread = avg(f_short) − avg(f_long)) − coûts.
    """
    coins = list(price_bars)
    closes = {c: [b.close for b in price_bars[c]] for c in coins}
    n = min(min(len(closes[c]) for c in coins),
            min(len(funding[c]) for c in coins))
    rets, prev_l, prev_s = [], set(), set()
    for i in range(n - 1 - exec_lag):
        fi = {c: funding[c][i] for c in coins}            # funding connu à i
        ranked = sorted(coins, key=lambda c: fi[c])
        k = max(1, int(len(ranked) * top_frac))
        longs, shorts = set(ranked[:k]), set(ranked[-k:])  # long bas funding, short haut

        def pret(c):
            p0 = closes[c][i + exec_lag]
            p1 = closes[c][i + 1 + exec_lag]
            return (p1 - p0) / p0 if p0 else 0.0

        price_ls = (sum(pret(c) for c in longs) / len(longs)
                    - sum(pret(c) for c in shorts) / len(shorts))
        # funding collecté sur la période détenue (taux à i+lag)
        f_short = sum(funding[c][i + exec_lag] for c in shorts) / len(shorts)
        f_long = sum(funding[c][i + exec_lag] for c in longs) / len(longs)
        funding_pnl = f_short - f_long          # short reçoit, long paie
        turnover = len(longs ^ prev_l) + len(shorts ^ prev_s)
        trans = turnover * ((taker_bps + slippage_bps) / 1e4) / max(1, len(longs) + len(shorts))
        rets.append(price_ls + funding_pnl - trans)
        prev_l, prev_s = longs, shorts
    return rets


def carry_neutral_backtest(funding: List[float], premium: List[float],
                           fee_bps: float = 4.5, exec_lag: int = 1,
                           smooth: int = 24) -> List[float]:
    """Carry DELTA-NEUTRAL d'un coin (long spot + short perp, prix hedgé).

    carry = funding − Δpremium (le premium = base perp/spot, dispo via
    funding_history → pas besoin de spot externe). Côté décidé sur le funding
    PERSISTANT (moyenne glissante `smooth`, défaut 24h) pour éviter le fee-churn
    d'un flip à chaque bruit horaire. Réalisé sur [i+lag, i+1+lag].
    """
    n = min(len(funding), len(premium))
    rets, prev_pos = [], 0
    for i in range(n - 1 - exec_lag):
        w = funding[max(0, i - smooth + 1):i + 1]
        avg_f = sum(w) / len(w)                       # funding persistant (anti-churn)
        pos = -1 if avg_f > 0 else 1                  # short perp si funding moyen>0
        j = i + exec_lag
        dprem = premium[j + 1] - premium[j]
        f = funding[j]
        carry = pos * (dprem - f)                    # short: f − Δprem
        fee = abs(pos - prev_pos) * (2 * fee_bps / 1e4)  # 2 jambes (spot+perp) au flip
        rets.append(carry - fee)
        prev_pos = pos
    return rets


def _sharpe(xs):
    if len(xs) < 2:
        return 0.0
    sd = statistics.pstdev(xs)
    return statistics.mean(xs) / sd if sd > 0 else 0.0
