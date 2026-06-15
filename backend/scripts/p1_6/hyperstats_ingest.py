#!/usr/bin/env python3
"""Phase 1.6b — HyperStats v2 API ingest.

Pull grades S+ à B (100 wallets max per grade). Gratuit no-auth.
Dédoublonne vs HL official et écrit la liste des uniques + métadata winRate.
"""
import json
import sys
import time
from pathlib import Path

import httpx

API_BASE = "https://v2-api.hyperstats.org/api"
HL_LB = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
OUT = Path(__file__).resolve().parents[2] / "data" / "p1_6" / "hyperstats_unique.json"
OUT.parent.mkdir(parents=True, exist_ok=True)

GRADES = ["S+", "S", "A+", "A", "B+", "B"]

def fetch_hl_addrs() -> set[str]:
    print("[1/3] HL official...")
    with httpx.Client(timeout=60) as c:
        r = c.get(HL_LB)
        r.raise_for_status()
        rows = r.json().get("leaderboardRows", [])
    return {row["ethAddress"].lower() for row in rows if row.get("ethAddress")}

def fetch_hyperstats_grades() -> list[dict]:
    print("[2/3] HyperStats grades S+ → B...")
    all_traders = []
    with httpx.Client(timeout=30) as c:
        for grade in GRADES:
            url = f"{API_BASE}/traders/top"
            params = {"grade": grade, "limit": 100}
            try:
                r = c.get(url, params=params)
                r.raise_for_status()
                data = r.json()
                traders = data if isinstance(data, list) else data.get("traders", data.get("data", []))
                for t in traders:
                    if isinstance(t, dict):
                        addr = (t.get("address") or t.get("wallet") or "").lower()
                        if addr.startswith("0x") and len(addr) == 42:
                            all_traders.append({
                                "address": addr,
                                "grade": grade,
                                "rank": t.get("rank"),
                                "qualityScore": t.get("qualityScore"),
                                "winRate": t.get("winRate"),
                                "totalPnl": t.get("totalPnl"),
                                "pnl30d": t.get("pnl30d"),
                                "totalTrades": t.get("totalTrades"),
                                "mainToken": t.get("mainToken"),
                                "lastActivityAt": t.get("lastActivityAt"),
                            })
                print(f"  grade {grade:3s}: +{len(traders)} traders")
                time.sleep(0.3)  # rate limit 100 req/5s = OK
            except Exception as e:
                print(f"  grade {grade:3s}: ERROR {e}")
    return all_traders

def main():
    hl_addrs = fetch_hl_addrs()
    print(f"  → {len(hl_addrs)} HL addresses")
    hs_traders = fetch_hyperstats_grades()
    
    print(f"[3/3] Cross-check ({len(hl_addrs)} HL known)...")
    seen = set()
    unique = []
    for t in hs_traders:
        addr = t["address"]
        if addr in seen: continue
        seen.add(addr)
        if addr not in hl_addrs:
            unique.append(t)
    
    print(f"  Total HyperStats (dedupe): {len(seen)}")
    print(f"  Unique vs HL: {len(unique)} ({len(unique)*100/max(len(seen),1):.1f}%)")
    
    OUT.write_text(json.dumps({
        "timestamp": int(time.time()),
        "hl_count": len(hl_addrs),
        "hyperstats_total_dedup": len(seen),
        "unique_count": len(unique),
        "traders": unique,
    }, indent=2))
    print(f"  → wrote {OUT}")

if __name__ == "__main__":
    sys.exit(main())
