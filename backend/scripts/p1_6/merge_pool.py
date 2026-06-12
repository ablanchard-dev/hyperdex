#!/usr/bin/env python3
"""Phase 1.6c — Merge discovery pool.

Combine cohort 232 actuelle + HyperTracker unique + HyperStats unique.
Dédoublonne et écrit le pool final candidate à valider.
"""
import json
import csv
from pathlib import Path

DATA = Path("/opt/app/hyperdex/backend/data")
COHORT_CSV = DATA / "p1" / "consistent_set.csv"  # 333 wallets baseline
HT_UNIQUE = DATA / "p1_6" / "hypertracker_unique.json"
HS_UNIQUE = DATA / "p1_6" / "hyperstats_unique.json"
OUT = DATA / "p1_6" / "merged_pool.json"

def load_cohort_232() -> set[str]:
    """Load existing cohort addresses from CSV."""
    addrs = set()
    if COHORT_CSV.exists():
        with COHORT_CSV.open() as f:
            r = csv.DictReader(f)
            for row in r:
                a = (row.get("addr") or row.get("address") or row.get("wallet") or "").lower()
                if a.startswith("0x") and len(a) == 42:
                    addrs.add(a)
    return addrs

def main():
    cohort = load_cohort_232()
    print(f"Cohort baseline (CSV consistent_set): {len(cohort)} addresses")
    
    ht = json.loads(HT_UNIQUE.read_text())
    hs = json.loads(HS_UNIQUE.read_text())
    ht_addrs = {w["address"] for w in ht["wallets"]}
    hs_addrs = {t["address"] for t in hs["traders"]}
    
    print(f"HyperTracker unique: {len(ht_addrs)}")
    print(f"HyperStats unique:   {len(hs_addrs)}")
    print(f"HT ∩ HS:             {len(ht_addrs & hs_addrs)}")
    print(f"HT ∪ HS:             {len(ht_addrs | hs_addrs)}")
    
    new_candidates = (ht_addrs | hs_addrs) - cohort
    print(f"\nNew candidates (not in cohort baseline): {len(new_candidates)}")
    
    overlap_with_cohort = (ht_addrs | hs_addrs) & cohort
    print(f"Already in cohort baseline:              {len(overlap_with_cohort)}")
    
    # Merge metadata for new candidates
    by_addr = {}
    for w in ht["wallets"]:
        addr = w["address"]
        if addr in new_candidates:
            by_addr[addr] = {
                "address": addr,
                "source_hypertracker": True,
                "ht_segment": w.get("segment_id"),
                "ht_perp_pnl": w.get("perp_pnl"),
                "ht_equity": w.get("total_equity"),
            }
    for t in hs["traders"]:
        addr = t["address"]
        if addr in new_candidates:
            entry = by_addr.setdefault(addr, {"address": addr})
            entry["source_hyperstats"] = True
            entry["hs_grade"] = t.get("grade")
            entry["hs_winRate"] = t.get("winRate")
            entry["hs_totalPnl"] = t.get("totalPnl")
            entry["hs_totalTrades"] = t.get("totalTrades")
            entry["hs_mainToken"] = t.get("mainToken")
    
    OUT.write_text(json.dumps({
        "cohort_baseline_size": len(cohort),
        "ht_unique": len(ht_addrs),
        "hs_unique": len(hs_addrs),
        "ht_hs_intersection": len(ht_addrs & hs_addrs),
        "new_candidates_count": len(new_candidates),
        "new_candidates": list(by_addr.values()),
    }, indent=2))
    print(f"\n→ wrote {OUT}")
    print(f"\nFINAL POOL = {len(cohort)} cohort + {len(new_candidates)} new candidates = {len(cohort) + len(new_candidates)}")

if __name__ == "__main__":
    main()
