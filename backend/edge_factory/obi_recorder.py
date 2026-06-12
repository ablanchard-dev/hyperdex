#!/usr/bin/env python3
"""Recorder OBI — accumule des snapshots de carnet HL (REST l2_snapshot) dans le temps.

Frontière microstructure sous-horaire : on poll le carnet périodiquement et on logge
des records COMPACTS {coin, time, obi, mid, spread} en JSONL append-only (pas le carnet
entier = trop gros). Une fois assez de records accumulés (plusieurs jours), on testera si
OBI[t] prédit le move de mid → CRITIC durci. record_from_snapshot est pur/testable ;
record_loop est l'I/O (REST poll, accessible Paris contrairement au WS Binance).
"""
import json
import time
from typing import Dict, List, Optional

import obi_signal as obi


def record_from_snapshot(book: dict, depth: int = 5) -> Optional[Dict]:
    """Snapshot l2 → record compact {coin, time, obi, mid, spread_bps}. None si vide."""
    levels = book.get("levels") if isinstance(book, dict) else None
    if not levels or len(levels) < 2 or not levels[0] or not levels[1]:
        return None
    mid = obi.mid_price(book)
    try:
        bid = float(levels[0][0]["px"])
        ask = float(levels[1][0]["px"])
        spread_bps = (ask - bid) / mid * 1e4 if mid > 0 else 0.0
    except (KeyError, TypeError, ValueError, IndexError):
        spread_bps = 0.0
    return {"coin": book.get("coin", "?"), "time": int(book.get("time", 0)),
            "obi": round(obi.compute_obi(book, depth), 6),
            "mid": mid, "spread_bps": round(spread_bps, 3)}


def to_jsonl(rec: Dict) -> str:
    return json.dumps(rec, separators=(",", ":"))


def load_records(path: str, coin: Optional[str] = None) -> List[Dict]:
    out: List[Dict] = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if coin is None or r.get("coin") == coin:
                    out.append(r)
    except FileNotFoundError:
        return []
    return out


def record_loop(coins, out_path, duration_s, interval_s=5.0,
                depth=5):  # pragma: no cover (I/O réseau)
    """Poll l2_snapshot des `coins` toutes interval_s pendant duration_s → JSONL.
    REST HL (accessible Paris). Retourne le nb de records écrits."""
    from hyperliquid.info import Info
    from hyperliquid.utils import constants
    info = Info(constants.MAINNET_API_URL, skip_ws=True)
    n = 0
    deadline = time.time() + duration_s
    with open(out_path, "a") as f:
        while time.time() < deadline:
            for coin in coins:
                try:
                    rec = record_from_snapshot(info.l2_snapshot(coin), depth)
                    if rec:
                        f.write(to_jsonl(rec) + "\n")
                        n += 1
                except Exception:
                    pass
            f.flush()
            time.sleep(interval_s)
    return n


if __name__ == "__main__":  # pragma: no cover
    import sys
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 3600.0
    out = sys.argv[2] if len(sys.argv) > 2 else "obi_data.jsonl"
    coins = sys.argv[3].split(",") if len(sys.argv) > 3 else ["BTC", "ETH", "SOL", "HYPE"]
    print(f"recording OBI {coins} {dur}s → {out}", flush=True)
    print(f"records: {record_loop(coins, out, dur)}", flush=True)
