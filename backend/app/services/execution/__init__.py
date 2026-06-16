"""HyperDex execution module — order submission avec dry_run flag.

Le module qui distingue paper/live au niveau du client :
- Tout au-dessus du flag (l2Book mainnet, VWAP walk-the-book, risk checks,
  latence simulée) est PARTAGÉ entre paper et live.
- Le flag `dry_run` au dernier point bascule entre PaperFill et vrai submit.

NOTE : la branche live de `ExchangeClient` (dry_run=False) existe mais n'est
branchée à aucun runner — le projet tourne en paper-only. Aucun flag runtime ne
bascule en live ; il faudrait écrire un runner live explicite pour l'activer.
"""
from app.services.execution.exchange import (
    ExchangeClient,
    Fill,
    LatencyModel,
)

__all__ = ["ExchangeClient", "Fill", "LatencyModel"]
