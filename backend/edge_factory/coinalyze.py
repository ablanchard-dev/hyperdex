#!/usr/bin/env python3
"""Connecteur liquidations Coinalyze (REST, gratuit 40 req/min) — accessible Paris.

Contourne le blocage WebSocket Binance Futures. L'API renvoie par marché un
'history' array de bougies de liquidation : {symbol, history:[{t,l,s}]} où
t=epoch secondes, l=long-liq USD, s=short-liq USD sur l'intervalle.

Parsing PUR (réponse → events normalisés compatibles hawkes/liq_recorder) testable
sans clé. fetch_liquidation_history = couche I/O (auth api_key) — attend la clé
opérateur. Réf : api.coinalyze.net/v1, auth param 'api_key', rate 40/min.
"""
import os
import time
from typing import Dict, List, Optional

BASE = "https://api.coinalyze.net/v1"


def hl_symbol(coin: str) -> str:
    """Symbol Coinalyze des liquidations HYPERLIQUID natives ('{coin}.H', exchange H).
    ⚠️ Coinalyze LISTE les marchés HL mais ne fournit PAS leur feed de liquidations
    (payload vide) — utiliser binance_liq_symbol comme proxy market-wide à la place."""
    return f"{coin}.H"


def binance_liq_symbol(coin: str) -> str:
    """Proxy liquidations Binance ('{coin}USDT_PERP.A', exchange A = Binance).

    HL n'expose pas ses liquidations via Coinalyze → on prend Binance comme PROXY :
    une cascade de liquidations est un signal de MARCHÉ (même sous-jacent), pas
    exchange-spécifique. L'EXÉCUTION reste sur HL (« univers = live » préservé pour
    l'exécution ; le SIGNAL est sourcé cross-exchange, ce qui est honnête et étiqueté)."""
    return f"{coin}USDT_PERP.A"


def parse_liquidation_history(payload: list) -> List[Dict]:
    """Réponse Coinalyze → liste d'events {ts(ms),symbol,liquidated_side,notional}.

    Une bougie peut produire 0, 1 ou 2 events (long et/ou short non nuls). Trié
    par temps (entrée du moteur Hawkes). Robuste aux champs manquants/0."""
    events: List[Dict] = []
    if not isinstance(payload, list):
        return events
    for market in payload:
        if not isinstance(market, dict):
            continue
        symbol = market.get("symbol", "?")
        history = market.get("history")
        if not isinstance(history, list):
            continue
        for bucket in history:
            t = bucket.get("t")
            if t is None:
                continue
            ts_ms = int(t) * 1000
            for side_key, side in (("l", "long"), ("s", "short")):
                val = bucket.get(side_key)
                try:
                    notional = float(val)
                except (TypeError, ValueError):
                    continue
                if notional > 0:
                    events.append({
                        "ts": ts_ms,
                        "symbol": symbol,
                        "liquidated_side": side,
                        "notional": notional,
                    })
    events.sort(key=lambda e: e["ts"])
    return events


def parse_oi_history(payload: list, bar_ts_ms: List[int]) -> List[float]:
    """OI history Coinalyze ({symbol,history:[{t,o,h,l,c}]}) → close d'OI aligné aux
    barres (bar_ts en ms). Forward-fill de la dernière OI connue ≤ barre (no-look-ahead) ;
    0.0 avant la 1ère OI connue. Retourne une liste de len(bar_ts_ms)."""
    out = [0.0] * len(bar_ts_ms)
    if not isinstance(payload, list) or not payload:
        return out
    hist = payload[0].get("history") if isinstance(payload[0], dict) else None
    if not hist:
        return out
    # (ts_ms, close) triés
    pts = []
    for b in hist:
        t = b.get("t")
        c = b.get("c")
        if t is None or c is None:
            continue
        try:
            pts.append((int(t) * 1000, float(c)))
        except (TypeError, ValueError):
            continue
    pts.sort()
    if not pts:
        return out
    import bisect
    ts_only = [p[0] for p in pts]
    for i, bt in enumerate(bar_ts_ms):
        j = bisect.bisect_right(ts_only, bt) - 1  # dernière OI connue ≤ barre
        out[i] = pts[j][1] if j >= 0 else 0.0
    return out


def fetch_oi_history(symbols: str, interval: str, frm: int, to: int,
                     api_key: Optional[str] = None,
                     convert_to_usd: bool = True) -> list:  # pragma: no cover (I/O réseau)
    """GET /open-interest-history (HL natif {coin}.H disponible). Même auth/retry que liq."""
    import httpx
    key = api_key or os.environ.get("COINALYZE_API_KEY")
    if not key:
        raise RuntimeError("clé API manquante")
    params = {"symbols": symbols, "interval": interval, "from": frm, "to": to,
              "convert_to_usd": "true" if convert_to_usd else "false", "api_key": key}
    for attempt in range(5):
        r = httpx.get(f"{BASE}/open-interest-history", params=params, timeout=30)
        if r.status_code == 429:
            time.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("rate-limited après retries")


def fetch_liquidation_history(symbols: str, interval: str, frm: int, to: int,
                              api_key: Optional[str] = None,
                              convert_to_usd: bool = True
                              ) -> list:  # pragma: no cover (I/O réseau)
    """GET /liquidation-history. api_key depuis arg ou env COINALYZE_API_KEY.
    interval ∈ {1min,5min,15min,30min,1hour,...}. frm/to = epoch secondes."""
    import httpx
    key = api_key or os.environ.get("COINALYZE_API_KEY")
    if not key:
        raise RuntimeError("clé API manquante : COINALYZE_API_KEY ou arg api_key")
    params = {
        "symbols": symbols, "interval": interval,
        "from": frm, "to": to,
        "convert_to_usd": "true" if convert_to_usd else "false",
        "api_key": key,
    }
    for attempt in range(5):
        r = httpx.get(f"{BASE}/liquidation-history", params=params, timeout=30)
        if r.status_code == 429:  # rate limit 40/min
            time.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("rate-limited après retries")


def fetch_future_markets(api_key: Optional[str] = None
                         ) -> list:  # pragma: no cover (I/O réseau)
    """Liste des marchés futures (pour trouver les symbols, ex BTCUSDT_PERP.A)."""
    import httpx
    key = api_key or os.environ.get("COINALYZE_API_KEY")
    if not key:
        raise RuntimeError("clé API manquante")
    r = httpx.get(f"{BASE}/future-markets", params={"api_key": key}, timeout=30)
    r.raise_for_status()
    return r.json()
