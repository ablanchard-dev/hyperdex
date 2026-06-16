"""PaperPosition — modèle perp HL avec leverage, funding accrued, liq_price.

Modèle de position perp continu :
- Perp continu (pas binary 0/1).
- Leverage : margin = notional / leverage.
- Funding accrual horaire (impact PnL sur holds long).
- Prix de liquidation calculé selon leverage + maint margin.
- Unrealized PnL = function du current price.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# HL maintenance margin standard ~5% pour la plupart des perps majeurs
DEFAULT_MAINT_MARGIN = 0.05


@dataclass
class PaperPosition:
    """Une position perp paper-tradée."""
    trader: str           # 0x... du wallet copié
    coin: str             # ex "BTC", "ETH", "HYPE"
    is_long: bool
    size: float           # base coin units (positive)
    entry_price: float
    leverage: float
    open_ts_ms: int
    open_fee_usd: float
    open_fill_id: str
    funding_accrued_usd: float = 0.0    # cumul funding payé (- si reçu)
    last_funding_ts_ms: int = 0
    maint_margin_pct: float = DEFAULT_MAINT_MARGIN

    # --- derived properties ---

    @property
    def notional_usd(self) -> float:
        return abs(self.size) * self.entry_price

    @property
    def margin_usd(self) -> float:
        if self.leverage <= 0:
            return self.notional_usd
        return self.notional_usd / self.leverage

    @property
    def liquidation_price(self) -> float:
        """Prix de liquidation simplifié (sans funding).

        Long  : entry × (1 - 1/lev + maint)
        Short : entry × (1 + 1/lev - maint)
        """
        if self.leverage <= 1:
            return 0.0 if self.is_long else float("inf")
        inv_lev = 1.0 / self.leverage
        if self.is_long:
            return self.entry_price * (1.0 - inv_lev + self.maint_margin_pct)
        return self.entry_price * (1.0 + inv_lev - self.maint_margin_pct)

    # --- PnL ---

    def unrealized_pnl(self, current_price: float) -> float:
        """PnL non réalisé en USD à `current_price`, net du funding accrued."""
        if self.is_long:
            gross = (current_price - self.entry_price) * self.size
        else:
            gross = (self.entry_price - current_price) * self.size
        return gross - self.funding_accrued_usd

    def realized_pnl(self, exit_price: float, exit_fee_usd: float) -> tuple[float, float, float]:
        """À la fermeture, retourne (net_pnl, gross_pnl, total_fees_incl_funding).

        net_pnl = gross - open_fee - exit_fee - funding_accrued
        """
        if self.is_long:
            gross = (exit_price - self.entry_price) * self.size
        else:
            gross = (self.entry_price - exit_price) * self.size
        total_fees = self.open_fee_usd + exit_fee_usd + self.funding_accrued_usd
        net = gross - total_fees
        return net, gross, total_fees

    # --- funding accrual ---

    def apply_funding(self, hourly_rate: float, ts_ms: int) -> float:
        """Applique 1 snapshot horaire de funding.

        HL convention : `funding` field = rate horaire signed decimal.
        - Si rate > 0 : longs paient (funding_cost +), shorts reçoivent (-).
        - Si rate < 0 : inverse.
        Returns delta funding cost in USD (positif = on a payé).
        """
        notional = self.notional_usd
        # long : paie si rate>0 ; short : reçoit si rate>0
        sign = 1.0 if self.is_long else -1.0
        delta_usd = sign * hourly_rate * notional
        self.funding_accrued_usd += delta_usd
        self.last_funding_ts_ms = ts_ms
        return delta_usd

    # --- to-dict pour JSONL ---

    def to_dict(self) -> dict:
        return dict(
            trader=self.trader, coin=self.coin, is_long=self.is_long,
            size=self.size, entry_price=self.entry_price,
            leverage=self.leverage, open_ts_ms=self.open_ts_ms,
            open_fee_usd=self.open_fee_usd, open_fill_id=self.open_fill_id,
            funding_accrued_usd=self.funding_accrued_usd,
            last_funding_ts_ms=self.last_funding_ts_ms,
            notional_usd=self.notional_usd,
            margin_usd=self.margin_usd,
            liquidation_price=self.liquidation_price,
        )
