#!/usr/bin/env python3
"""EquitiesAdapter — venue actions small-cap US (la vraie niche riche).

Données = API chart Yahoo (JSON, gratuit, httpx, ZÉRO pandas → garde le profil
pur-python). fetch_bars injecté (DI) : Yahoo en prod, mock en test. Univers =
liste de tickers injectée (source small-cap, ex: Russell 2000, branchée à part).
benchmark = IWM (ETF Russell 2000) pour le test beta-neutral.

Live-check : python equities_adapter.py --live
"""
from typing import Callable, List, Optional

from adapter import Bar, Fees, VenueAdapter

# small-cap : commission ~0 (brokers modernes) mais SPREAD/slippage réel
# (illiquidité small-cap) → modélisé conservativement.
EQ_TAKER_BPS = 8.0
EQ_MAKER_BPS = 2.0
_YH = "https://query1.finance.yahoo.com/v8/finance/chart/"


def _yahoo_to_bars(payload: dict) -> List[Bar]:
    """Parse le JSON chart Yahoo -> List[Bar]. close = ADJCLOSE (ajusté splits +
    dividendes) pour des returns propres ; fallback close brut si absent.
    (open/high/low restent bruts — non utilisés par les signaux close-to-close.)
    """
    try:
        res = payload["chart"]["result"][0]
        ts = res["timestamp"]
        q = res["indicators"]["quote"][0]
    except (KeyError, IndexError, TypeError):
        return []
    try:
        adj = res["indicators"]["adjclose"][0]["adjclose"]
    except (KeyError, IndexError, TypeError):
        adj = None
    out = []
    for i, t in enumerate(ts):
        raw = q["close"][i]
        c = adj[i] if (adj is not None and i < len(adj) and adj[i] is not None) else raw
        if c is None:
            continue
        out.append(Bar(ts=int(t) * 1000, close=float(c),
                       open=float(q["open"][i] if q["open"][i] is not None else c),
                       high=float(q["high"][i] if q["high"][i] is not None else c),
                       low=float(q["low"][i] if q["low"][i] is not None else c),
                       volume=float(q["volume"][i] or 0)))
    out.sort(key=lambda b: b.ts)
    return out


def _yahoo_fetch_bars(symbol: str, start_ms: int, end_ms: int) -> List[Bar]:
    import httpx
    url = (f"{_YH}{symbol}?period1={start_ms // 1000}"
           f"&period2={end_ms // 1000}&interval=1d")
    with httpx.Client(timeout=30) as c:
        r = c.get(url, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        return _yahoo_to_bars(r.json())


class EquitiesAdapter(VenueAdapter):
    name = "equities_smallcap"

    def __init__(self, tickers: List[str],
                 fetch_bars: Optional[Callable[[str, int, int], List[Bar]]] = None,
                 benchmark: str = "IWM") -> None:
        self._tickers = list(tickers)
        self._fetch_bars = fetch_bars or _yahoo_fetch_bars
        self._benchmark = benchmark

    def universe(self) -> List[str]:
        return list(self._tickers)

    def history(self, symbol: str, start: int, end: int) -> List[Bar]:
        return self._fetch_bars(symbol, start, end)

    def fees(self, symbol: str) -> Fees:
        return Fees(taker_bps=EQ_TAKER_BPS, maker_bps=EQ_MAKER_BPS)

    def benchmark(self, start: int, end: int) -> List[Bar]:
        return self._fetch_bars(self._benchmark, start, end)


if __name__ == "__main__":
    import sys
    import time
    if "--live" in sys.argv:
        end = int(time.time() * 1000)
        start = end - 365 * 24 * 3600 * 1000
        a = EquitiesAdapter(["IWM", "PLUG", "FUBO"])  # 1 ETF + 2 small-caps
        for s in a.universe():
            b = a.history(s, start, end)
            print(f"{s}: {len(b)} barres j, dernier close="
                  f"{b[-1].close if b else 'NA'}")
    else:
        print("usage: python equities_adapter.py --live")
