#!/usr/bin/env python3
"""Phase 1.7 — hold_med pour les candidats spike v2 (WR 55-90%).

Re-stream l'archive S3 HL 14j (mêmes fichiers que spike v2) mais ne tracke
l'état position QUE pour les 2342 candidats. Calcule hold_med logique (A4)
par wallet via state-machine incrémentale (fichiers triés jour/heure asc →
ordre temporel global préservé, pas besoin de buffer 20M fills).

Filtre opérateur 2026-05-28 : hold_med ≥ 10s (test ; on montera si entrées
nulles). Merge avec stats v2, rank, output cohort élargie Tokyo.

Usage:
    python scripts/p2/_reservoir_hold_v3.py --days 14 --end-date 20260527
    python scripts/p2/_reservoir_hold_v3.py --days 1   # smoke
"""
import argparse
import gc
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
import lz4.frame
from botocore.exceptions import ClientError

# A4 logic parity (réplique inline pour le streaming, mais on importe la
# fonction batch pour les tests de cohérence si besoin).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

BUCKET = "hl-mainnet-node-data"
PREFIX = "node_fills_by_block/hourly"
DATA_DIR = Path("/opt/app/hyperdex/backend/data/p2_reservoir")
V2_JSON = DATA_DIR / "reservoir_spike_14d_v2.json"

HOLD_MIN_MS = 10_000  # filtre test (montera si entrées nulles)


def load_candidates():
    """Load 2342 candidats spike v2 -> {addr_lower: v2_stats_dict}."""
    d = json.loads(V2_JSON.read_text())
    out = {}
    for w in d["all_scored"]:
        a = w["addr"].lower()
        if a.startswith("0x") and len(a) == 42:
            out[a] = w
    return out


def step_state(st, side, size, ts, holds):
    """Une transition A4 incrémentale sur l'état position d'un (wallet,coin).

    st = {'side': 'flat'|'long'|'short', 'entry_ts': int|None, 'size': float}
    Append hold_ms à `holds` quand un cycle non-flat → flat se ferme.
    Réplique compute_hold_ms_logical() (size-tracking, scale-in, flip).
    """
    if st["side"] == "flat":
        st["side"] = side
        st["entry_ts"] = ts
        st["size"] = size
    elif st["side"] == side:
        st["size"] += size
    else:
        st["size"] -= size
        if st["size"] <= 1e-9:
            if st["entry_ts"] is not None:
                holds.append(ts - st["entry_ts"])
            overshoot = -st["size"]
            if overshoot > 1e-9:
                st["side"] = side
                st["entry_ts"] = ts
                st["size"] = overshoot
            else:
                st["side"] = "flat"
                st["entry_ts"] = None
                st["size"] = 0.0


def process_lz4(s3, key, candidates, state, holds_by_wallet):
    """Stream 1 LZ4, applique state-machine aux fills des candidats."""
    local = DATA_DIR / f"_tmph_{key.split('/')[-2]}_{key.split('/')[-1]}"
    n_events = 0
    size_mb = 0.0
    try:
        s3.download_file(BUCKET, key, str(local),
                         ExtraArgs={"RequestPayer": "requester"})
        size_mb = local.stat().st_size / 1024 / 1024
        with lz4.frame.open(local, "rb") as f:
            buf = b""
            for chunk in iter(lambda: f.read(65536), b""):
                buf += chunk
                while b"\n" in buf:
                    line, _, buf = buf.partition(b"\n")
                    if not line.strip():
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    for event in obj.get("events", []):
                        if len(event) != 2:
                            continue
                        user_addr, fill = event
                        if not isinstance(fill, dict):
                            continue
                        user = user_addr.lower() if user_addr else ""
                        if user not in candidates:
                            continue
                        coin = fill.get("coin", "")
                        raw_side = fill.get("side", "")
                        if not coin or not raw_side:
                            continue
                        side = "long" if raw_side == "B" else "short"
                        try:
                            size = float(fill.get("sz", 0))
                            ts = int(fill.get("time", 0))
                        except (TypeError, ValueError):
                            continue
                        if size <= 0 or ts <= 0:
                            continue
                        st = state[(user, coin)]
                        step_state(st, side, size, ts,
                                   holds_by_wallet[user])
                        n_events += 1
        return size_mb, n_events
    except ClientError as e:
        print(f"  ERR DL {key}: {e.response['Error']['Code']}", flush=True)
        return 0.0, 0
    finally:
        if local.exists():
            local.unlink()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--end-date", type=str, default="20260527")
    args = ap.parse_args()

    end_date = args.end_date or (
        datetime.now(timezone.utc) - timedelta(days=1)
    ).strftime("%Y%m%d")
    days = [
        (datetime.strptime(end_date, "%Y%m%d") - timedelta(days=i)).strftime("%Y%m%d")
        for i in range(args.days)
    ]
    days.sort()
    print(f"=== Phase 1.7 hold spike : {args.days}d ({days[0]} → {days[-1]}) ===",
          flush=True)

    candidates = load_candidates()
    print(f"Candidats spike v2 : {len(candidates)}", flush=True)

    s3 = boto3.client("s3", region_name="us-east-1")

    # état position par (wallet, coin) ; holds par wallet
    state = defaultdict(lambda: {"side": "flat", "entry_ts": None, "size": 0.0})
    holds_by_wallet = defaultdict(list)

    total_mb = 0.0
    total_events = 0
    t0 = time.time()

    for di, day in enumerate(days):
        r = s3.list_objects_v2(Bucket=BUCKET, Prefix=f"{PREFIX}/{day}/",
                               MaxKeys=100, RequestPayer="requester")
        contents = r.get("Contents", [])
        # tri horaire asc (filename "13.lz4" -> 13)
        def hour_key(o):
            try:
                return int(o["Key"].split("/")[-1].split(".")[0])
            except ValueError:
                return 9999
        contents.sort(key=hour_key)
        files = [o["Key"] for o in contents]
        day_mb = sum(o["Size"] for o in contents) / 1024 / 1024
        print(f"\n[{di+1}/{len(days)}] day {day}: {len(files)} files, "
              f"{day_mb:.0f} MB", flush=True)

        for j, key in enumerate(files):
            if j % 6 == 0:
                el = time.time() - t0
                print(f"  [{j+1}/{len(files)}] {key.split('/')[-1]} "
                      f"(elapsed {el:.0f}s, events={total_events:,}, "
                      f"states={len(state):,})", flush=True)
            mb, ne = process_lz4(s3, key, candidates, state, holds_by_wallet)
            total_mb += mb
            total_events += ne
        gc.collect()
        n_holds = sum(len(h) for h in holds_by_wallet.values())
        print(f"  day {day} done. holds accumulés: {n_holds:,}", flush=True)

    # ---- compute hold_med par wallet + merge v2 + filtre ----
    print(f"\n=== Scoring hold sur {len(candidates)} candidats ===", flush=True)
    rows = []
    no_holds = 0
    for addr, v2 in candidates.items():
        holds = holds_by_wallet.get(addr, [])
        if not holds:
            no_holds += 1
            continue
        holds.sort()
        n = len(holds)
        hold_med = holds[n // 2]
        hold_mean = sum(holds) / n
        hold_p25 = holds[n // 4]
        hold_p75 = holds[(3 * n) // 4]
        rows.append({
            "addr": v2["addr"],
            "tier": v2["tier"],
            "hold_med_ms": hold_med,
            "hold_med_s": round(hold_med / 1000, 1),
            "hold_mean_s": round(hold_mean / 1000, 1),
            "hold_p25_s": round(hold_p25 / 1000, 1),
            "hold_p75_s": round(hold_p75 / 1000, 1),
            "n_holds": n,
            "pnl_net": round(v2["pnl_net"], 2),
            "avg_pnl_per_trade": round(v2["avg_pnl_per_trade"], 2),
            "sharpe_approx": round(v2["sharpe_approx"], 3),
            "n_fills": v2["n_fills"],
            "wr": round(v2["wr"], 4),
            "n_coins": v2["n_coins"],
            "fills_per_day": round(v2["fills_per_day"], 1),
            "composite_score": round(v2["composite_score"], 4),
            "in_cohort_232": v2["in_cohort_232"],
        })

    kept = [r for r in rows if r["hold_med_ms"] >= HOLD_MIN_MS]
    kept.sort(key=lambda r: -r["composite_score"])

    # distribution hold buckets (sur tous ceux avec holds)
    buckets = {"<10s": 0, "10-60s": 0, "1-5min": 0, "5-30min": 0,
               "30min-2h": 0, ">2h": 0}
    for r in rows:
        s = r["hold_med_ms"] / 1000
        if s < 10:
            buckets["<10s"] += 1
        elif s < 60:
            buckets["10-60s"] += 1
        elif s < 300:
            buckets["1-5min"] += 1
        elif s < 1800:
            buckets["5-30min"] += 1
        elif s < 7200:
            buckets["30min-2h"] += 1
        else:
            buckets[">2h"] += 1

    out = {
        "days": args.days,
        "end_date": end_date,
        "total_events": total_events,
        "total_mb_dl": round(total_mb, 1),
        "candidates": len(candidates),
        "with_holds": len(rows),
        "no_holds": no_holds,
        "hold_min_ms": HOLD_MIN_MS,
        "kept_hold_ge_10s": len(kept),
        "hold_buckets": buckets,
        "all_with_hold": sorted(rows, key=lambda r: -r["composite_score"]),
        "kept": kept,
    }
    out_path = DATA_DIR / f"reservoir_hold_{args.days}d.json"
    out_path.write_text(json.dumps(out, indent=2))

    # CSV cohort élargie (kept)
    csv_path = DATA_DIR / f"cohort_elargie_tokyo_{args.days}d.csv"
    if kept:
        import csv as _csv
        with csv_path.open("w", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=list(kept[0].keys()))
            w.writeheader()
            w.writerows(kept)

    # ---- report ----
    print(f"\n=== Hold buckets (med, {len(rows)} candidats avec holds) ===",
          flush=True)
    for k, v in buckets.items():
        print(f"  {k:>10}: {v}", flush=True)
    print(f"\nFiltre hold_med ≥ {HOLD_MIN_MS/1000:.0f}s : {len(kept)} KEPT",
          flush=True)
    print(f"  no_holds (jamais clos cycle 14j): {no_holds}", flush=True)

    print(f"\n=== Top 20 cohort élargie (composite_score) ===", flush=True)
    print(f"{'addr':<16}{'tier':<22}{'hold_med':>10}{'wr':>7}"
          f"{'n_fills':>9}{'avg/tr':>9}{'in232':>7}", flush=True)
    for r in kept[:20]:
        print(f"{r['addr'][:14]:<16}{r['tier']:<22}"
              f"{r['hold_med_s']:>9.0f}s{r['wr']*100:>6.0f}%"
              f"{r['n_fills']:>9}{r['avg_pnl_per_trade']:>9.0f}"
              f"{str(r['in_cohort_232']):>7}", flush=True)

    print(f"\n=== Stats ===", flush=True)
    print(f"  DL: {total_mb:.0f} MB → coût AWS ≈ ${total_mb/1024*0.09:.2f}",
          flush=True)
    print(f"  elapsed: {time.time()-t0:.0f}s", flush=True)
    print(f"  events: {total_events:,}", flush=True)
    print(f"→ {out_path}", flush=True)
    if kept:
        print(f"→ {csv_path}", flush=True)


if __name__ == "__main__":
    main()
