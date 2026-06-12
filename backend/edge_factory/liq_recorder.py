#!/usr/bin/env python3
"""Recorder de liquidations Binance Futures — feed public gratuit !forceOrder@arr.

Source des papiers Hawkes (vraie data, standard académique). Le parsing (message
brut → LiqEvent propre) est PUR et testable sans réseau ; la connexion WS est une
fine couche I/O par-dessus (record_stream). Append-only JSONL pour accumulation
multi-jours reprenable.

Format forceOrder (doc Binance) : {"o":{"s":sym,"S":side,"p":price,"q":qty,
"ap":avg,"T":ts,...}}. ⚠️ side SELL = LONG liquidé (vente forcée) ; BUY = SHORT
liquidé → détermine le SIGNE de la pression (signal Hawkes plus tard).
"""
import json
import time
from typing import Dict, List, Optional

WS_URL = "wss://fstream.binance.com/ws/!forceOrder@arr"


def parse_force_order(msg: dict) -> Optional[Dict]:
    """Message brut forceOrder → événement normalisé, ou None si non pertinent.

    Retourne {ts, symbol, side, price, qty, notional, liquidated_side} où
    liquidated_side = 'long' si vente forcée (S=SELL), 'short' si S=BUY.
    """
    o = msg.get("o") if isinstance(msg, dict) else None
    if not o or "s" not in o:
        return None
    try:
        price = float(o.get("ap") or o.get("p"))
        qty = float(o["q"])
        ts = int(o["T"])
    except (TypeError, ValueError, KeyError):
        return None
    side = o.get("S")
    return {
        "ts": ts,
        "symbol": o["s"],
        "side": side,
        "price": price,
        "qty": qty,
        "notional": price * qty,
        "liquidated_side": "long" if side == "SELL" else "short",
    }


def to_jsonl(event: Dict) -> str:
    return json.dumps(event, separators=(",", ":"))


def load_events(path: str, symbol: Optional[str] = None) -> List[Dict]:
    """Relit un JSONL de liquidations (pour calibrer Hawkes hors-ligne)."""
    out: List[Dict] = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if symbol is None or ev.get("symbol") == symbol:
                    out.append(ev)
    except FileNotFoundError:
        return []
    return out


def event_times_seconds(events: List[Dict]) -> List[float]:
    """Timestamps (ms) → secondes relatives au 1er event (entrée du moteur Hawkes)."""
    if not events:
        return []
    t0 = min(e["ts"] for e in events)
    return sorted((e["ts"] - t0) / 1000.0 for e in events)


def record_stream(out_path: str, duration_s: float,
                  url: str = WS_URL) -> int:  # pragma: no cover (I/O réseau live)
    """Enregistre le flux live en JSONL append-only pendant duration_s. Retourne
    le nombre d'events capturés. Couche I/O fine — la logique est dans parse_*."""
    try:
        from websocket import create_connection  # websocket-client
    except ImportError:
        raise RuntimeError("websocket-client requis : pip install websocket-client")
    ws = create_connection(url, timeout=30)
    n = 0
    deadline = time.time() + duration_s
    try:
        with open(out_path, "a") as f:
            while time.time() < deadline:
                try:
                    raw = ws.recv()
                except Exception:
                    break
                if not raw:
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                ev = parse_force_order(msg)
                if ev:
                    f.write(to_jsonl(ev) + "\n")
                    f.flush()
                    n += 1
    finally:
        ws.close()
    return n


if __name__ == "__main__":  # pragma: no cover
    import sys
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
    path = sys.argv[2] if len(sys.argv) > 2 else "liq_binance.jsonl"
    print(f"Recording {dur}s → {path} ...", flush=True)
    print(f"captured {record_stream(path, dur)} liquidations", flush=True)
