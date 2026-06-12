#!/usr/bin/env python3
"""Lead-lag BTC→alts cross-sectional market-neutral — dernière idée data-gratuite.

Hypothèse : les alts qui ont SOUS-réagi au dernier move de BTC (résiduel négatif
après beta-ajustement) rattrapent → LONG ; ceux qui ont SUR-réagi → SHORT.
Le beta est estimé sur une fenêtre PASSÉE *séparée* de la fenêtre-signal (sinon le
beta absorbe le signal). Dollar-neutral → beta-portefeuille ≈0 attendu ; le CRITIC
juge s'il reste de l'alpha net de coûts (turnover horaire = coûts lourds).
No-look-ahead : décision à close i (passé only), fill à i+exec_lag.
"""
from typing import Dict, List

from adapter import Bar


def _returns(closes: List[float]) -> List[float]:
    return [(closes[i] - closes[i - 1]) / closes[i - 1] if closes[i - 1] else 0.0
            for i in range(1, len(closes))]


def _beta(ar: List[float], br: List[float]) -> float:
    """beta = cov(alt, btc)/var(btc) sur la fenêtre ; fallback 1.0 si dégénéré."""
    if len(br) < 2:
        return 1.0
    mb = sum(br) / len(br)
    var = sum((b - mb) ** 2 for b in br)
    if var <= 1e-12:
        return 1.0
    ma = sum(ar) / len(ar)
    cov = sum((a - ma) * (b - mb) for a, b in zip(ar, br))
    return cov / var


def lead_lag_backtest(symbol_bars: Dict[str, List[Bar]], btc_bars: List[Bar],
                      lookback: int, top_frac: float, taker_bps: float,
                      slippage_bps: float = 0.0, beta_window: int = 48,
                      exec_lag: int = 1) -> List[float]:
    closes = {s: [b.close for b in bars] for s, bars in symbol_bars.items()}
    btc = [b.close for b in btc_bars]
    n = min(min(len(c) for c in closes.values()), len(btc))
    syms = list(closes)
    rets = {s: _returns(closes[s][:n]) for s in syms}
    bret = _returns(btc[:n])

    out: List[float] = []
    prev_l: set = set()
    prev_s: set = set()
    for i in range(n - 1 - exec_lag):
        if i <= lookback:
            out.append(0.0)
            continue
        b0 = btc[i - lookback]
        btc_tr = (btc[i] - b0) / b0 if b0 else 0.0
        # fenêtre beta : returns PASSÉS finissant AVANT la fenêtre-signal (exclut le signal)
        we = i - lookback                      # rets indices [w0, we) ⇒ ≤ close(i-lookback)
        w0 = max(0, we - beta_window)
        feats = {}
        for s in syms:
            p0 = closes[s][i - lookback]
            alt_tr = (closes[s][i] - p0) / p0 if p0 else 0.0
            beta = _beta(rets[s][w0:we], bret[w0:we])
            feats[s] = alt_tr - beta * btc_tr  # résiduel
        if len(feats) < 4:
            out.append(0.0)
            continue
        ranked = sorted(feats, key=lambda s: feats[s])
        k = max(1, int(len(ranked) * top_frac))
        longs, shorts = set(ranked[:k]), set(ranked[-k:])  # LONG retardataires / SHORT leaders

        def nret(s):
            q0 = closes[s][i + exec_lag]
            q1 = closes[s][i + 1 + exec_lag]
            return (q1 - q0) / q0 if q0 else 0.0

        long_r = sum(nret(s) for s in longs) / len(longs)
        short_r = sum(nret(s) for s in shorts) / len(shorts)
        gross = long_r - short_r
        turnover = len(longs ^ prev_l) + len(shorts ^ prev_s)
        trans = turnover * ((taker_bps + slippage_bps) / 1e4) / max(1, len(longs) + len(shorts))
        out.append(gross - trans)
        prev_l, prev_s = longs, shorts
    return out
