#!/usr/bin/env python3
"""Modèle de coûts réaliste pour le backtest factory (standards institutionnels).

Au-delà du taker fixe : slippage/spread + BORROW COST du short (le long-short
market-neutral n'est pas gratuit ; certains small-caps sont hard-to-borrow).
Coûts en FRACTION de notional (1 unité de position = 1 de notional).
"""
TRADING_DAYS = 252
# Valeurs SOURCÉES (recherche 2026) — pas « au pif » :
#  - slippage small-cap : 0.75-1% (75-100 bps) pour les vrais small-caps ;
#    ~20-40 bps pour small-cap index-member (S&P600) liquide. Défaut conservateur 30.
#  - borrow : GC liquide ~30 bps ; small-cap HTB 5-50%/an ; <$100M cap >30%/an moyen ;
#    S&P600-tier ~5-15%. Défaut conservateur 800 bps (8%). Micro-cap = bien pire / un-borrowable.
DEFAULT_SLIPPAGE_BPS = 30.0
DEFAULT_BORROW_BPS_ANNUAL = 800.0


def transaction_cost(delta_position: float, taker_bps: float,
                     slippage_bps: float = DEFAULT_SLIPPAGE_BPS) -> float:
    """Coût de changer la position de |delta| unités (taker + slippage), fraction."""
    return abs(delta_position) * (taker_bps + slippage_bps) / 1e4


def borrow_cost(position: float, borrow_bps_annual: float = DEFAULT_BORROW_BPS_ANNUAL,
                period_days: int = 1) -> float:
    """Coût de portage d'un SHORT (position<0) sur period_days. 0 si long/flat."""
    if position >= 0:
        return 0.0
    return abs(position) * (borrow_bps_annual / 1e4) * (period_days / TRADING_DAYS)


def period_cost(prev_pos: float, new_pos: float, taker_bps: float,
                slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
                borrow_bps_annual: float = DEFAULT_BORROW_BPS_ANNUAL,
                period_days: int = 1) -> float:
    """Coût total d'une période : transaction (changement vers new_pos) + borrow
    (portage de new_pos si short)."""
    return (transaction_cost(new_pos - prev_pos, taker_bps, slippage_bps)
            + borrow_cost(new_pos, borrow_bps_annual, period_days))
