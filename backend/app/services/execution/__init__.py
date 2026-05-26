"""HyperDex execution module — order submission avec dry_run flag.

Le SEUL module qui distingue paper/live :
- Tout au-dessus du flag (l2Book mainnet, VWAP walk-the-book, risk checks,
  latence simulée) est PARTAGÉ entre paper et live.
- Le flag `dry_run` au dernier point bascule entre PaperFill et vrai submit.

Comme polyoracle (PAPER_LIVE_STRICT / LIVE_ENABLED), passer en live = flip un
flag, zéro autre changement de code. C'est la garantie structurelle paper=live.
"""
from app.services.execution.exchange import (
    ExchangeClient,
    Fill,
    LatencyModel,
)

__all__ = ["ExchangeClient", "Fill", "LatencyModel"]
