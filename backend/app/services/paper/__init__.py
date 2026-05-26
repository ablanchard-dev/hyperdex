"""HyperDex paper engine — modèle perp + fill simulator + funding + tracker.

Composants P2 :
- PaperPosition : modèle perp HL (size, leverage, entry, funding_accrued, liq_price).
- FillSimulator : walk-the-book + slippage + book freshness. Pur, testable.
- PnLTracker   : positions ouvertes + JSONL append-only.
- FundingAccrual : loop hourly snapshot.
"""
from app.services.paper.position import PaperPosition
from app.services.paper.fill_simulator import FillSimulator
from app.services.paper.pnl_tracker import PnLTracker
from app.services.paper.funding import FundingAccrual

__all__ = ["PaperPosition", "FillSimulator", "PnLTracker", "FundingAccrual"]
