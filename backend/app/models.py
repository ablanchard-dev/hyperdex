"""HyperDex DB models — scaffold P0.1, vide.

À remplir en P0/P1 :
- Trader  : un wallet HL suivi + métriques discovery (closedPnl cumul, Sharpe,
  hold_median_minutes, leverage_avg, max_drawdown, copyable_profile).
- Fill    : un fill HL récupéré (closedPnl, side, sz, px, ts, coin, hash).
- PaperPosition : position paper-trade ouverte/fermée (entry, side, leverage,
  size, liq_price, unrealized, funding_paid, copy_source_wallet).
- ValidationRun : un run de validation P1 (univers N, holdout window, traders
  survivants, multiple_testing_correction).
"""
from sqlmodel import SQLModel  # noqa: F401
