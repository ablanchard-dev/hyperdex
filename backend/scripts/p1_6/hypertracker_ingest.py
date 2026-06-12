#!/usr/bin/env python3
"""Phase 1.6a — HyperTracker CDN ingest.

Pull les 16 segments (top 50 par segment) du CDN HyperTracker (gratuit, no-auth).
Dédoublonne vs HL official leaderboard et écrit la liste des uniques.
"""
import json
import sys
import time
from pathlib import Path
from typing import Any

import requests

CDN_BASE = "https://dw3ji7n7thadj.cloudfront.net/aggregator"
HL_LB = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
OUT = Path("/home/dexter/hyperdex/backend/data/p1_6/hypertracker_unique.json")
OUT.parent.mkdir(parents=True, exist_ok=True)

def fetch_hl_leaderboard() -> set[str]:
    """Set of lowercased addresses from HL official leaderboard."""
    print("[1/3] Fetching HL official leaderboard...")
    import httpx
    with httpx.Client(timeout=60) as c:
        r = c.get(HL_LB)
        r.raise_for_status()
    data = r.json()
    rows = data.get("leaderboardRows", [])
    addrs = {row["ethAddress"].lower() for row in rows if row.get("ethAddress")}
    print(f"  → {len(addrs)} HL official addresses")
    return addrs

def fetch_hypertracker_segments() -> list[dict[str, Any]]:
    """Pull 16 segments, return flat list of wallets with segment tag."""
    print("[2/3] Fetching HyperTracker 16 segments...")
    all_wallets = []
    for seg_id in range(1, 17):
        url = f"{CDN_BASE}/segment_{seg_id}_wallets.json"
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            data = r.json()
            # Schema varies — try common keys
            wallets = data if isinstance(data, list) else data.get("wallets", data.get("data", []))
            for w in wallets:
                if isinstance(w, dict):
                    addr = (w.get("address") or w.get("wallet") or w.get("ethAddress") or "").lower()
                    if addr.startswith("0x") and len(addr) == 42:
                        all_wallets.append({
                            "address": addr,
                            "segment_id": seg_id,
                            "perp_pnl": w.get("perpPnl") or w.get("totalPnl") or 0,
                            "total_equity": w.get("totalEquity") or 0,
                            "raw": {k: v for k, v in w.items() if k in 
                                    ["displayName", "earliestActivityAt", "segments", "favoriteCount"]}
                        })
            print(f"  seg {seg_id:2d}: +{len(wallets)} wallets")
            time.sleep(0.2)  # gentle on CDN
        except Exception as e:
            print(f"  seg {seg_id:2d}: ERROR {e}")
    return all_wallets

def main():
    hl_addrs = fetch_hl_leaderboard()
    ht_wallets = fetch_hypertracker_segments()
    
    print(f"[3/3] Cross-check vs HL official ({len(hl_addrs)} known)...")
    seen = set()
    unique_wallets = []
    for w in ht_wallets:
        addr = w["address"]
        if addr in seen:
            continue
        seen.add(addr)
        if addr not in hl_addrs:
            unique_wallets.append(w)
    
    print(f"  Total HyperTracker wallets (dedupe): {len(seen)}")
    print(f"  Unique vs HL official: {len(unique_wallets)} ({len(unique_wallets)*100/max(len(seen),1):.1f}%)")
    
    OUT.write_text(json.dumps({
        "timestamp": int(time.time()),
        "hl_official_count": len(hl_addrs),
        "hypertracker_total_dedup": len(seen),
        "unique_count": len(unique_wallets),
        "wallets": unique_wallets
    }, indent=2))
    print(f"  → wrote {OUT}")

if __name__ == "__main__":
    sys.exit(main())
