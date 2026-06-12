#!/usr/bin/env python3
"""HLSmallCapAdapter — niche small-cap / new-listing sur Hyperliquid.

Implémente VenueAdapter en branchant le client HL de HyperDex (InfoClient, qui
wrappe le SDK officiel). Niche ciblée = perps à FAIBLE volume notionnel/jour
(small money absent = inefficience potentielle), benchmark = BTC pour le test
beta-neutral du CRITIC.

Client injecté (DI) : tout objet exposant meta_and_asset_ctxs() et
candles(name, interval, start_ms, end_ms) — InfoClient en prod, mock en test.

Live-check : python hl_adapter.py --live
"""
from typing import List

from adapter import Bar, Fees, VenueAdapter

# Barème HL base (tier 0). Mesuré empiriquement ~4.66 bps taker dans le run
# véracité ; on garde le barème nominal, raffinable via user_fees plus tard.
HL_TAKER_BPS = 4.5
HL_MAKER_BPS = 1.5


class HLSmallCapAdapter(VenueAdapter):
    name = "hl_smallcap"

    def __init__(self, client, vol_max_usd: float = 50_000_000,
                 interval: str = "1h", benchmark: str = "BTC") -> None:
        self._c = client
        self._vol_max = vol_max_usd
        self._interval = interval
        self._benchmark = benchmark

    def universe(self) -> List[str]:
        """Perps small-cap = dayNtlVlm < seuil (small money absent)."""
        meta, ctxs = self._c.meta_and_asset_ctxs()
        names = []
        for asset, ctx in zip(meta.get("universe", []), ctxs):
            try:
                vol = float(ctx.get("dayNtlVlm", 0))
            except (TypeError, ValueError):
                continue
            if 0 < vol < self._vol_max:
                names.append(asset["name"])
        return names

    def _to_bars(self, raw) -> List[Bar]:
        out = []
        for k in raw:
            try:
                out.append(Bar(
                    ts=int(k["T"]),
                    close=float(k["c"]), open=float(k["o"]),
                    high=float(k["h"]), low=float(k["l"]),
                    volume=float(k.get("v", 0)),
                ))
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def history(self, symbol: str, start: int, end: int) -> List[Bar]:
        return self._to_bars(
            self._c.candles(symbol, self._interval, start, end))

    def fees(self, symbol: str) -> Fees:
        return Fees(taker_bps=HL_TAKER_BPS, maker_bps=HL_MAKER_BPS)

    def benchmark(self, start: int, end: int) -> List[Bar]:
        return self._to_bars(
            self._c.candles(self._benchmark, self._interval, start, end))


if __name__ == "__main__":
    import sys
    import time
    if "--live" in sys.argv:
        sys.path.insert(0, "/opt/app/hyperdex/backend")
        from app.services.hl_api.info_client import InfoClient
        a = HLSmallCapAdapter(InfoClient())
        u = a.universe()
        print(f"univers small-cap (<{a._vol_max:,.0f} $/j) : {len(u)} perps")
        print("ex:", u[:15])
        if u:
            end = int(time.time() * 1000)
            start = end - 30 * 24 * 3600 * 1000
            bars = a.history(u[0], start, end)
            print(f"{u[0]} : {len(bars)} barres {a._interval}, "
                  f"dernier close={bars[-1].close if bars else 'NA'}")
    else:
        print("usage: python hl_adapter.py --live")
