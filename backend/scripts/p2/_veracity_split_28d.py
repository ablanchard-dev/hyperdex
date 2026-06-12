#!/usr/bin/env python3
"""Pilier 1 — Test de véracité OOS train/test 28j (univers COMPLET).

Le test qui a démasqué le faux edge Polymarket (rating 44k wallets →
corrélation +0.09, top quartile = pire). On le rejoue sur HyperLiquid avec
règle de décision PRÉ-ENREGISTRÉE.

Méthode :
  - Stream 28j (20260430→20260527).
  - Split chaque fill : train (time < cutoff 20260514) vs test (>=).
  - Per-wallet, per-window : pnl_net, n_fills, n_wins, n_losses.
  - train_qualifiers = WR_train ∈ [55%,90%) & pnl_train>0 & n_train≥20.
  - Mesure leur perf OOS sur le TEST (jamais vu par la sélection).

Verdict pré-enregistré :
  - Spearman(score_train, pnl_test)  : PASS > 0.15 / FAIL |ρ|<0.10
  - top quartile vs bottom (pnl_test): PASS séparation positive
  - % qualifiers rentables en test    : PASS > 55% / FAIL ≈ 50%
  - lift vs baseline (all-active)      : qualifiers doivent battre la médiane
"""
import argparse
import gc
import json
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
import lz4.frame
from botocore.exceptions import ClientError

BUCKET = "hl-mainnet-node-data"
PREFIX = "node_fills_by_block/hourly"
DATA_DIR = Path("/home/dexter/hyperdex/backend/data/p2_reservoir")

# cutoff : train < 20260514, test >= 20260514
CUTOFF_MS = int(datetime(2026, 5, 14, tzinfo=timezone.utc).timestamp() * 1000)


def spearman(xs, ys):
    """Rank correlation sans scipy."""
    n = len(xs)
    if n < 3:
        return 0.0

    def ranks(v):
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx = sum(rx) / n
    my = sum(ry) / n
    cov = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    vx = sum((rx[i] - mx) ** 2 for i in range(n)) ** 0.5
    vy = sum((ry[i] - my) ** 2 for i in range(n)) ** 0.5
    if vx == 0 or vy == 0:
        return 0.0
    return cov / (vx * vy)


def process_lz4(s3, key, stats):
    local = DATA_DIR / f"_tmpv_{key.split('/')[-2]}_{key.split('/')[-1]}"
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
                        if not user.startswith("0x"):
                            continue
                        try:
                            pnl = float(fill.get("closedPnl", 0))
                            fee = float(fill.get("fee", 0))
                            t = int(fill.get("time", 0))
                        except (TypeError, ValueError):
                            continue
                        net = pnl - fee
                        crossed = fill.get("crossed")  # True=taker, False=maker
                        w = stats[user]
                        pfx = "tr_" if t < CUTOFF_MS else "te_"
                        w[pfx + "pnl"] += net          # net (closedPnl - fee)
                        w[pfx + "gross"] += pnl         # directionnel (copiable)
                        w[pfx + "fee"] += fee           # fees (rebate si <0)
                        w[pfx + "n"] += 1
                        if net > 0:
                            w[pfx + "w"] += 1
                        elif net < 0:
                            w[pfx + "l"] += 1
                        # copiabilité : taker vs maker
                        if crossed is True:
                            w[pfx + "taker"] += 1
                        elif crossed is False:
                            w[pfx + "maker"] += 1
                        # Welford sur net per-fill (Sharpe)
                        w[pfx + "obs"] += 1
                        d = net - w[pfx + "mean"]
                        w[pfx + "mean"] += d / w[pfx + "obs"]
                        w[pfx + "m2"] += d * (net - w[pfx + "mean"])
                        if t > 0:
                            if w[pfx + "first"] == 0 or t < w[pfx + "first"]:
                                w[pfx + "first"] = t
                            if t > w[pfx + "last"]:
                                w[pfx + "last"] = t
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
    ap.add_argument("--days", type=int, default=28)
    ap.add_argument("--end-date", type=str, default="20260527")
    args = ap.parse_args()

    days = [
        (datetime.strptime(args.end_date, "%Y%m%d") - timedelta(days=i)).strftime("%Y%m%d")
        for i in range(args.days)
    ]
    days.sort()
    print(f"=== Pilier 1 véracité : {args.days}d ({days[0]} → {days[-1]}) ===",
          flush=True)
    print(f"cutoff train/test : 20260514 (train<, test>=)", flush=True)

    s3 = boto3.client("s3", region_name="us-east-1")

    def _w():
        d = {}
        for p in ("tr_", "te_"):
            d[p + "pnl"] = 0.0
            d[p + "gross"] = 0.0
            d[p + "fee"] = 0.0
            d[p + "n"] = 0
            d[p + "w"] = 0
            d[p + "l"] = 0
            d[p + "taker"] = 0
            d[p + "maker"] = 0
            d[p + "obs"] = 0
            d[p + "mean"] = 0.0
            d[p + "m2"] = 0.0
            d[p + "first"] = 0
            d[p + "last"] = 0
        return d

    stats = defaultdict(_w)
    total_mb = 0.0
    total_events = 0
    t0 = time.time()

    for di, day in enumerate(days):
        r = s3.list_objects_v2(Bucket=BUCKET, Prefix=f"{PREFIX}/{day}/",
                               MaxKeys=100, RequestPayer="requester")
        files = [o["Key"] for o in r.get("Contents", [])]
        day_mb = sum(o["Size"] for o in r.get("Contents", [])) / 1024 / 1024
        print(f"\n[{di+1}/{len(days)}] day {day}: {len(files)} files, "
              f"{day_mb:.0f} MB", flush=True)
        for j, key in enumerate(files):
            if j % 8 == 0:
                el = time.time() - t0
                print(f"  [{j+1}/{len(files)}] (elapsed {el:.0f}s, "
                      f"events={total_events:,}, wallets={len(stats):,})",
                      flush=True)
            mb, ne = process_lz4(s3, key, stats)
            total_mb += mb
            total_events += ne
        gc.collect()

    def med(xs):
        s = sorted(xs)
        return s[len(s) // 2] if s else 0.0

    def mean(xs):
        return sum(xs) / len(xs) if xs else 0.0

    def sharpe(m, m2, n):
        if n < 2:
            return 0.0
        var = m2 / (n - 1)
        return m / (var ** 0.5) if var > 0 else 0.0

    # ---- build per-wallet records (substrat multi-pipeline) ----
    print(f"\n=== Build records sur {len(stats):,} wallets ===", flush=True)
    recs = []
    all_active_test = []  # baseline : tous actifs en test (n_tr>=20)
    for addr, w in stats.items():
        tr_res = w["tr_w"] + w["tr_l"]
        te_res = w["te_w"] + w["te_l"]
        if w["tr_n"] < 10 and w["te_n"] < 10:
            continue
        tr_class = w["tr_taker"] + w["tr_maker"]
        taker_ratio = w["tr_taker"] / tr_class if tr_class > 0 else None
        rec = {
            "addr": addr,
            "tr_n": w["tr_n"], "tr_net": round(w["tr_pnl"], 2),
            "tr_gross": round(w["tr_gross"], 2), "tr_fee": round(w["tr_fee"], 2),
            "tr_wr": round(w["tr_w"] / tr_res, 4) if tr_res else None,
            "tr_sharpe": round(sharpe(w["tr_mean"], w["tr_m2"], w["tr_obs"]), 4),
            "tr_fpd": round(w["tr_n"] / 14.0, 2),
            "tr_taker": w["tr_taker"], "tr_maker": w["tr_maker"],
            "taker_ratio": round(taker_ratio, 3) if taker_ratio is not None else None,
            "te_n": w["te_n"], "te_net": round(w["te_pnl"], 2),
            "te_gross": round(w["te_gross"], 2),
            "te_wr": round(w["te_w"] / te_res, 4) if te_res else None,
            "te_sharpe": round(sharpe(w["te_mean"], w["te_m2"], w["te_obs"]), 4),
        }
        recs.append(rec)
        if w["tr_n"] >= 20 and tr_res > 0 and w["te_n"] > 0:
            all_active_test.append(w["te_pnl"])

    # dump CSV substrat
    import csv as _csv
    csv_path = DATA_DIR / "veracity_perwallet_28d.csv"
    if recs:
        with csv_path.open("w", newline="") as f:
            wcsv = _csv.DictWriter(f, fieldnames=list(recs[0].keys()))
            wcsv.writeheader()
            wcsv.writerows(recs)
    print(f"  dump {len(recs):,} wallets → {csv_path.name}", flush=True)

    med_baseline = med(all_active_test)

    def run_oos(quals, label):
        """OOS verdict sur un set de qualifiers (déjà actifs en test)."""
        active = [q for q in quals if q["n_te"] > 0]
        n_active = len(active)
        if n_active < 10:
            print(f"\n[{label}] trop peu de qualifiers actifs ({n_active}) "
                  f"→ non concluant", flush=True)
            return {"label": label, "n_qualifiers": len(quals),
                    "active_in_test": n_active, "verdict": "INCONCLUSIVE"}
        rho_wr = spearman([q["wr_tr"] for q in active],
                          [q["pnl_te"] for q in active])
        rho_pnl = spearman([q["pnl_tr"] for q in active],
                           [q["pnl_te"] for q in active])
        by_wr = sorted(active, key=lambda q: q["wr_tr"])
        qn = len(by_wr) // 4
        top_te = [q["pnl_te"] for q in by_wr[-qn:]] if qn else []
        bot_te = [q["pnl_te"] for q in by_wr[:qn]] if qn else []
        pct_profit = sum(1 for q in active if q["pnl_te"] > 0) / n_active * 100
        med_qual = med([q["pnl_te"] for q in active])
        pass_rho = rho_wr > 0.15 or rho_pnl > 0.15
        pass_q = med(top_te) > med(bot_te) and med(top_te) > 0
        pass_p = pct_profit > 55
        pass_l = med_qual > med_baseline
        n_pass = sum([pass_rho, pass_q, pass_p, pass_l])
        verdict = "PASS" if n_pass >= 3 else "FAIL"
        print(f"\n{'='*56}\n  [{label}] OOS — {len(quals):,} quals, "
              f"{n_active:,} actifs test\n{'='*56}", flush=True)
        print(f"  Spearman WR_tr  vs PnL_te : {rho_wr:+.4f} "
              f"({'P' if rho_wr>0.15 else '-'})", flush=True)
        print(f"  Spearman PnL_tr vs PnL_te : {rho_pnl:+.4f} "
              f"({'P' if rho_pnl>0.15 else '-'})", flush=True)
        print(f"  Top quartile PnL_te médian: ${med(top_te):+,.0f} "
              f"(mean ${mean(top_te):+,.0f})", flush=True)
        print(f"  Bot quartile PnL_te médian: ${med(bot_te):+,.0f} "
              f"(mean ${mean(bot_te):+,.0f})  sep={'P' if pass_q else '-'}",
              flush=True)
        print(f"  % rentables en test       : {pct_profit:.1f}% "
              f"({'P' if pass_p else '-'})", flush=True)
        print(f"  Médiane PnL_te qual/base  : ${med_qual:+,.2f} / "
              f"${med_baseline:+,.2f}  lift={'P' if pass_l else '-'}", flush=True)
        print(f"  → VERDICT {label} : {verdict} ({n_pass}/4)", flush=True)
        return {
            "label": label, "n_qualifiers": len(quals), "active_in_test": n_active,
            "spearman_wr": round(rho_wr, 4), "spearman_pnl": round(rho_pnl, 4),
            "top_q_med_te": round(med(top_te), 2), "bot_q_med_te": round(med(bot_te), 2),
            "pct_profitable_test": round(pct_profit, 1),
            "med_te_qual": round(med_qual, 2), "med_te_baseline": round(med_baseline, 2),
            "n_criteria_pass": n_pass, "verdict": verdict,
        }

    # qualifiers = WR 55-90% & net>0 & n>=20 (sur train)
    def to_qual(r):
        return {"addr": r["addr"], "wr_tr": r["tr_wr"], "pnl_tr": r["tr_net"],
                "n_tr": r["tr_n"], "pnl_te": r["te_net"], "n_te": r["te_n"]}

    base_q = [r for r in recs if r["tr_n"] >= 20 and r["tr_wr"] is not None
              and 0.55 <= r["tr_wr"] < 0.90 and r["tr_net"] > 0]
    # copiables : taker-dominant + edge directionnel (gross>0)
    copy_q = [r for r in base_q if (r["taker_ratio"] or 0) >= 0.5
              and r["tr_gross"] > 0]

    print(f"\ntrain qualifiers WR55-90 : {len(base_q):,}  | "
          f"dont copiables (taker≥0.5 & gross>0) : {len(copy_q):,}", flush=True)

    res_all = run_oos([to_qual(r) for r in base_q], "TOUS qualifiers")
    res_copy = run_oos([to_qual(r) for r in copy_q], "COPIABLES seulement")

    out = {
        "days": args.days, "cutoff": "20260514",
        "total_events": total_events, "total_mb_dl": round(total_mb, 1),
        "wallets_seen": len(stats), "wallets_dumped": len(recs),
        "n_qualifiers_all": len(base_q), "n_qualifiers_copyable": len(copy_q),
        "oos_all": res_all, "oos_copyable": res_copy,
        "perwallet_csv": csv_path.name,
    }
    out_path = DATA_DIR / "veracity_split_28d.json"
    out_path.write_text(json.dumps(out, indent=2))

    print(f"\n  DL: {total_mb:.0f} MB → ${total_mb/1024*0.09:.2f} | "
          f"elapsed {time.time()-t0:.0f}s", flush=True)
    print(f"→ {out_path}", flush=True)
    print(f"→ {csv_path} (substrat multi-pipeline scalp/swing/position)",
          flush=True)


if __name__ == "__main__":
    main()
