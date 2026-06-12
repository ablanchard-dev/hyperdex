#!/usr/bin/env python3
"""Rotation de beta conditionnelle au régime BTC — angle distinct (timing du beta).

Recherche : le beta des alts se DILATE en risk-on (BTC haussier+volatil) et se COMPRIME
en risk-off. Idée : en risk-on, détenir les alts HAUT beta (ils amplifient) ; en risk-off,
préférer BAS beta. On ranke par beta trailing (passé only) et on choisit le côté selon le
régime BTC trailing (passé only). Distinct du lead-lag (rattrapage) et de la TE (flux info).

⚠️ honnêteté : ceci est probablement du BETA TIMÉ → le CRITIC (beta-neutral) le rejettera
sans doute en beta_deguise. C'est justement le test : timer le beta produit-il de l'ALPHA
résiduel, ou juste de l'exposition marché ? No-look-ahead, fill i+exec_lag.
"""
from typing import Dict, List

from adapter import Bar


def _returns(closes: List[float]) -> List[float]:
    return [(closes[i] - closes[i - 1]) / closes[i - 1] if closes[i - 1] else 0.0
            for i in range(1, len(closes))]


def btc_regime(btc_bars: List[Bar], window: int = 24) -> List[float]:
    """Régime BTC par barre (passé only) : +1 risk-on (return trailing > 0), -1 sinon.
    Aligné sur len(btc_bars) ; 0 tant que la fenêtre n'est pas pleine."""
    closes = [b.close for b in btc_bars]
    n = len(closes)
    out = [0.0] * n
    for i in range(window, n):
        trail = (closes[i] - closes[i - window]) / closes[i - window] if closes[i - window] else 0.0
        out[i] = 1.0 if trail > 0 else -1.0
    return out


def trailing_beta(alt_bars: List[Bar], btc_bars: List[Bar], window: int = 48) -> List[float]:
    """beta = cov(alt,btc)/var(btc) sur fenêtre trailing PASSÉE (aligné len(alt_bars))."""
    ar = [0.0] + _returns([b.close for b in alt_bars])
    br = [0.0] + _returns([b.close for b in btc_bars])
    n = min(len(ar), len(br))
    out = [0.0] * n
    for i in range(window, n):
        a = ar[i - window:i]
        b = br[i - window:i]
        mb = sum(b) / len(b)
        var = sum((x - mb) ** 2 for x in b)
        if var <= 1e-12:
            out[i] = 0.0
            continue
        ma = sum(a) / len(a)
        cov = sum((a[k] - ma) * (b[k] - mb) for k in range(len(b)))
        out[i] = cov / var
    return out


def regime_beta_returns(symbol_bars: Dict[str, List[Bar]], btc_bars: List[Bar],
                        beta_window: int = 48, regime_window: int = 24,
                        top_frac: float = 0.3, taker_bps: float = 4.5,
                        slippage_bps: float = 5.0, exec_lag: int = 1) -> List[float]:
    """Dollar-neutral : risk-on → LONG haut-beta / SHORT bas-beta ; risk-off → l'inverse.
    Le régime et les betas sont trailing (passé only). Fill i+exec_lag."""
    syms = list(symbol_bars)
    closes = {s: [b.close for b in symbol_bars[s]] for s in syms}
    betas = {s: trailing_beta(symbol_bars[s], btc_bars, beta_window) for s in syms}
    regime = btc_regime(btc_bars, regime_window)
    n = min(min(len(closes[s]) for s in syms), len(regime),
            min(len(betas[s]) for s in syms))
    cost = (taker_bps + slippage_bps) / 1e4
    rets: List[float] = []
    prev_l: set = set()
    prev_s: set = set()
    for i in range(n - 1 - exec_lag):
        reg = regime[i]
        if reg == 0.0:
            rets.append(0.0)
            continue
        feats = {s: betas[s][i] for s in syms}
        ranked = sorted(feats, key=lambda s: feats[s])  # bas beta → haut beta
        k = max(1, int(len(ranked) * top_frac))
        low_beta, high_beta = set(ranked[:k]), set(ranked[-k:])
        # risk-on (+1) : long haut-beta / short bas-beta ; risk-off : l'inverse
        if reg > 0:
            longs, shorts = high_beta, low_beta
        else:
            longs, shorts = low_beta, high_beta

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
