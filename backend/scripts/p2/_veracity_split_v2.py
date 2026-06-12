#!/usr/bin/env python3
"""Pilier 1 v2 — véracité OOS train/test SURVIVABLE + copiabilité bps + hold.

Successeur de _veracity_split_28d.py (mort OOM jour 26/28 sur box 7.5 Go).
Corrections :
  MÉMOIRE (le run précédent gardait 216k dicts → OOM) :
    - accumulateur COMPACT : array('d', 28) par wallet (~290 B vs ~2.5 KB dict)
    - PRUNE au cutoff train/test : on jette les wallets tr_n<20 (ne peuvent pas
      qualifier) → phase test ~30× plus légère
    - CHECKPOINT par jour (pickle) → reprenable si kill
    - lancement détaché recommandé (setsid/nohup) → indépendant de la session
  MÉTHODO (failles auditées) :
    - faille #1 : WR sur CLOSES uniquement (closedPnl≠0), en + de l'ancien WR-fills
    - faille #3 : edge en BPS = Σgross / Σ(px·sz sur closes) ×1e4 (copiabilité réelle)
    - faille #4 : hold_med (state-machine A4) + first/last dumpés au CSV
    - gate copiable v2 : taker_ratio≥0.6 & ret_bps_train>9 (coût taker RT) & gross>0

Verdict pré-enregistré inchangé (PASS si ≥3/4) : Spearman, séparation quartile,
% rentables test >55%, lift vs baseline.

Usage:
    python scripts/p2/_veracity_split_v2.py --days 28 --end-date 20260527
    python scripts/p2/_veracity_split_v2.py --days 2  --end-date 20260501  # smoke
"""
import argparse
import csv as _csv
import gc
import json
import os
import pickle
import signal
import time
from array import array
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
import lz4.frame
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

BUCKET = "hl-mainnet-node-data"
PREFIX = "node_fills_by_block/hourly"
DATA_DIR = Path(os.environ.get(
    "VERACITY_DATA_DIR", "/opt/app/hyperdex/backend/data/p2_reservoir"))
CUTOFF_DATE = "20260514"
CUTOFF_MS = int(datetime(2026, 5, 14, tzinfo=timezone.utc).timestamp() * 1000)
TAKER_RT_BPS = 9.0          # coût taker round-trip (4.5 bps × 2)
COPY_TAKER_MIN = 0.6        # taker_ratio mini pour copiable
QUAL_N_MIN = 20             # fills train mini pour qualifier
PRUNE_N_MIN = 20            # on garde au cutoff si tr_n >= ce seuil

# index accumulateur array('d', 32) : 14 champs × (train=0, test=+14) + 4 ex-HYPE
PNL, GROSS, FEE, N, CW, CL, TAKER, MAKER, CLNOT, OBS, MEAN, M2, FIRST, LAST = range(14)
W = 14  # offset test
# contrôle régime : PnL/n hors-HYPE (faille régime : 70-83% PnL histo = HYPE seul)
TR_NET_EXH, TR_N_EXH, TE_NET_EXH, TE_N_EXH = 28, 29, 30, 31
ACC_SZ = 32

# S3 résilience (lien Paris dégradé observé : download_file managed/multipart
# wedge en demi-mort sans que read_timeout tire → on stream get_object avec
# deadline wall-clock + retry sur client NEUF ; client neuf/jour en plus).
_S3_CFG = Config(retries={"max_attempts": 8, "mode": "adaptive"},
                 connect_timeout=15, read_timeout=60, tcp_keepalive=True)
DL_MAX_SEC = 150  # deadline wall-clock par fichier (catch le crawl/wedge)
STALL_SEC = 45    # un read() bloqué > ça = socket muet → SIGALRM interrompt


def _alarm(signum, frame):
    # SIGALRM interrompt un read() bloqué sur socket muet (read_timeout boto3
    # ne tire pas de façon fiable sur lien dégradé ; la deadline applicative ne
    # peut pas s'exécuter si read() ne rend jamais la main).
    raise TimeoutError(f"read stall >{STALL_SEC}s (SIGALRM)")


def _make_s3():
    return boto3.client("s3", region_name="us-east-1", config=_S3_CFG)


def _rss_mb():
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except Exception:
        pass
    return 0.0


def spearman(xs, ys):
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
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    vx = sum((rx[i] - mx) ** 2 for i in range(n)) ** 0.5
    vy = sum((ry[i] - my) ** 2 for i in range(n)) ** 0.5
    return cov / (vx * vy) if vx and vy else 0.0


def step_hold(st, side, size, ts, holds):
    """state-machine A4 incrémentale (réplique compute_hold_ms_logical)."""
    if st[0] == 0:  # flat
        st[0] = side
        st[1] = ts
        st[2] = size
    elif st[0] == side:
        st[2] += size
    else:
        st[2] -= size
        if st[2] <= 1e-9:
            if st[1]:
                holds.append(ts - st[1])
            overshoot = -st[2]
            if overshoot > 1e-9:
                st[0], st[1], st[2] = side, ts, overshoot
            else:
                st[0], st[1], st[2] = 0, 0, 0.0


def process_lz4(s3, key, stats, hstate, holds, track_hold, meta):
    local = DATA_DIR / f"_tmpv2_{key.split('/')[-2]}_{key.split('/')[-1]}"
    n_events = 0
    size_mb = 0.0
    try:
        # stream get_object + deadline wall-clock + retry client NEUF.
        # (download_file managed/multipart wedge en demi-mort sur lien dégradé
        #  sans que read_timeout ne tire ; un client neuf récupère à tous coups.)
        for attempt in range(4):
            cli = s3 if attempt == 0 else _make_s3()
            try:
                signal.alarm(STALL_SEC)
                body = cli.get_object(Bucket=BUCKET, Key=key,
                                      RequestPayer="requester")["Body"]
                signal.alarm(0)
                deadline = time.time() + DL_MAX_SEC
                with open(local, "wb") as out:
                    while True:
                        signal.alarm(STALL_SEC)
                        chunk = body.read(1 << 20)
                        signal.alarm(0)
                        if not chunk:
                            break
                        out.write(chunk)
                        if time.time() > deadline:
                            raise TimeoutError(f"wall-clock >{DL_MAX_SEC}s")
                body.close()
                break
            except (BotoCoreError, ConnectionError, OSError, TimeoutError) as e:
                signal.alarm(0)
                if local.exists():
                    local.unlink()
                if attempt == 3:
                    print(f"  ERR DL {key}: {type(e).__name__} "
                          f"(skip après 4 essais)", flush=True)
                    return 0.0, 0
                print(f"  retry DL {key} (#{attempt+1}): "
                      f"{type(e).__name__}", flush=True)
                time.sleep(2 * (attempt + 1))
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
                            px = float(fill.get("px", 0))
                            sz = float(fill.get("sz", 0))
                        except (TypeError, ValueError):
                            continue
                        net = pnl - fee
                        crossed = fill.get("crossed")
                        coin = fill.get("coin", "")
                        is_tr = t < CUTOFF_MS
                        o = 0 if is_tr else W
                        a = stats[user]
                        # coût taker EMPIRIQUE (mesuré sur fee réelle, pas supposé)
                        if crossed is True and px > 0 and sz > 0:
                            meta[0] += fee
                            meta[1] += px * sz
                        # contrôle régime : PnL hors-HYPE
                        if coin != "HYPE":
                            if is_tr:
                                a[TR_NET_EXH] += net
                                a[TR_N_EXH] += 1
                            else:
                                a[TE_NET_EXH] += net
                                a[TE_N_EXH] += 1
                        a[o + PNL] += net
                        a[o + GROSS] += pnl
                        a[o + FEE] += fee
                        a[o + N] += 1
                        # WR sur CLOSES (closedPnl != 0) — faille #1
                        if pnl > 0:
                            a[o + CW] += 1
                        elif pnl < 0:
                            a[o + CL] += 1
                        # bps : notional des fills de clôture — faille #3
                        if pnl != 0 and px > 0 and sz > 0:
                            a[o + CLNOT] += px * sz
                        if crossed is True:
                            a[o + TAKER] += 1
                        elif crossed is False:
                            a[o + MAKER] += 1
                        # Welford sur net
                        a[o + OBS] += 1
                        d = net - a[o + MEAN]
                        a[o + MEAN] += d / a[o + OBS]
                        a[o + M2] += d * (net - a[o + MEAN])
                        if t > 0:
                            if a[o + FIRST] == 0 or t < a[o + FIRST]:
                                a[o + FIRST] = t
                            if t > a[o + LAST]:
                                a[o + LAST] = t
                        # hold state-machine (faille #4)
                        if track_hold:
                            raw = fill.get("side", "")
                            if coin and raw and sz > 0 and t > 0:
                                side = 1 if raw == "B" else -1  # B=long
                                step_hold(hstate[(user, coin)], side, sz, t,
                                          holds[user])
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
    ap.add_argument("--no-hold", action="store_true",
                    help="désactive hold state-machine (smoke RAM accumulateur seul)")
    ap.add_argument("--resume", action="store_true",
                    help="reprend depuis checkpoint si présent")
    args = ap.parse_args()
    track_hold = not args.no_hold

    days = [
        (datetime.strptime(args.end_date, "%Y%m%d") - timedelta(days=i)).strftime("%Y%m%d")
        for i in range(args.days)
    ]
    days.sort()
    tag = f"{args.days}d_{args.end_date}"
    ckpt_path = DATA_DIR / f"_veracity_v2_ckpt_{tag}.pkl"
    print(f"=== Véracité v2 SURVIVABLE : {args.days}d ({days[0]}→{days[-1]}) ===",
          flush=True)
    print(f"cutoff={CUTOFF_DATE} | hold={'ON' if track_hold else 'OFF'} | "
          f"RSS départ={_rss_mb():.0f} MB", flush=True)

    signal.signal(signal.SIGALRM, _alarm)  # SIGALRM = anti-stall socket muet
    s3 = _make_s3()
    stats = defaultdict(lambda: array("d", [0.0] * ACC_SZ))
    hstate = defaultdict(lambda: [0, 0, 0.0])   # [side, entry_ts, size]
    holds = defaultdict(list)
    total_mb = total_events = 0.0
    meta = [0.0, 0.0]  # [taker_fee_sum, taker_notional_sum] — coût taker mesuré
    start_idx = 0
    pruned = False
    t0 = time.time()

    if args.resume and ckpt_path.exists():
        ck = pickle.loads(ckpt_path.read_bytes())
        stats = defaultdict(lambda: array("d", [0.0] * ACC_SZ), ck["stats"])
        if track_hold:
            hstate = defaultdict(lambda: [0, 0, 0.0], ck.get("hstate", {}))
            holds = defaultdict(list, ck.get("holds", {}))
        total_mb, total_events = ck["total_mb"], ck["total_events"]
        meta = ck.get("meta", [0.0, 0.0])
        start_idx, pruned = ck["next_day_idx"], ck["pruned"]
        print(f"  RESUME depuis jour idx {start_idx} ({len(stats):,} wallets)",
              flush=True)

    for di in range(start_idx, len(days)):
        day = days[di]
        s3 = _make_s3()  # client neuf/jour : évite l'empoisonnement du pool sur run long
        if not pruned and day >= CUTOFF_DATE:
            before = len(stats)
            for addr in [a for a, v in stats.items() if v[N] < PRUNE_N_MIN]:
                del stats[addr]
                if track_hold:
                    holds.pop(addr, None)
            if track_hold:
                for k in [k for k in hstate if k[0] not in stats]:
                    del hstate[k]
            pruned = True
            gc.collect()
            print(f"\n*** PRUNE au cutoff {CUTOFF_DATE} : {before:,} → "
                  f"{len(stats):,} wallets (tr_n≥{PRUNE_N_MIN}) | "
                  f"RSS={_rss_mb():.0f} MB ***", flush=True)

        r = s3.list_objects_v2(Bucket=BUCKET, Prefix=f"{PREFIX}/{day}/",
                               MaxKeys=100, RequestPayer="requester")
        contents = r.get("Contents", [])
        contents.sort(key=lambda o: int(o["Key"].split("/")[-1].split(".")[0])
                      if o["Key"].split("/")[-1].split(".")[0].isdigit() else 9999)
        files = [o["Key"] for o in contents]
        day_mb = sum(o["Size"] for o in contents) / 1024 / 1024
        print(f"\n[{di+1}/{len(days)}] day {day}: {len(files)} files, "
              f"{day_mb:.0f} MB | wallets={len(stats):,} RSS={_rss_mb():.0f} MB",
              flush=True)
        for j, key in enumerate(files):
            mb, ne = process_lz4(s3, key, stats, hstate, holds, track_hold, meta)
            total_mb += mb
            total_events += ne
        gc.collect()
        # checkpoint par jour
        ck = {"stats": dict(stats), "total_mb": total_mb,
              "total_events": total_events, "meta": meta,
              "next_day_idx": di + 1, "pruned": pruned}
        if track_hold:
            ck["hstate"] = dict(hstate)
            ck["holds"] = dict(holds)
        ckpt_path.write_bytes(pickle.dumps(ck, protocol=4))
        print(f"  day {day} done. wallets={len(stats):,} "
              f"(wxc hold states={len(hstate):,}) RSS={_rss_mb():.0f} MB "
              f"events={int(total_events):,} elapsed={time.time()-t0:.0f}s",
              flush=True)

    # ---------- build records ----------
    def med(xs):
        s = sorted(xs)
        return s[len(s) // 2] if s else 0.0

    def mean(xs):
        return sum(xs) / len(xs) if xs else 0.0

    def sharpe(a, o):
        if a[o + OBS] < 2:
            return 0.0
        var = a[o + M2] / (a[o + OBS] - 1)
        return a[o + MEAN] / (var ** 0.5) if var > 0 else 0.0

    # coût taker RT EMPIRIQUE (mesuré sur fee réelle des fills taker)
    eff_taker_bps = (meta[0] / meta[1] * 1e4) if meta[1] > 0 else 4.5
    rt_cost_bps = round(2 * eff_taker_bps, 2)
    print(f"\n=== Coût taker mesuré : {eff_taker_bps:.3f} bps/côté → RT "
          f"{rt_cost_bps} bps (vs hyp. {TAKER_RT_BPS}) ===", flush=True)

    print(f"=== Build sur {len(stats):,} wallets (RSS={_rss_mb():.0f} MB) ===",
          flush=True)
    recs = []
    for addr, a in stats.items():
        if a[N] < 10 and a[W + N] < 10:
            continue
        tr_res = a[CW] + a[CL]
        te_res = a[W + CW] + a[W + CL]
        cls = a[TAKER] + a[MAKER]
        taker_ratio = a[TAKER] / cls if cls else None
        hl = sorted(holds.get(addr, [])) if track_hold else []
        rec = {
            "addr": addr,
            "tr_n": int(a[N]), "tr_net": round(a[PNL], 2),
            "tr_gross": round(a[GROSS], 2), "tr_fee": round(a[FEE], 2),
            "tr_wr_close": round(a[CW] / tr_res, 4) if tr_res else None,
            "tr_ret_bps": round(a[GROSS] / a[CLNOT] * 1e4, 2) if a[CLNOT] else None,
            "tr_sharpe": round(sharpe(a, 0), 4),
            "taker_ratio": round(taker_ratio, 3) if taker_ratio is not None else None,
            "tr_first": int(a[FIRST]), "tr_last": int(a[LAST]),
            "te_n": int(a[W + N]), "te_net": round(a[W + PNL], 2),
            "te_gross": round(a[W + GROSS], 2),
            "te_wr_close": round(a[W + CW] / te_res, 4) if te_res else None,
            "te_ret_bps": round(a[W + GROSS] / a[W + CLNOT] * 1e4, 2) if a[W + CLNOT] else None,
            "hold_med_s": round(hl[len(hl) // 2] / 1000, 1) if hl else None,
            "n_holds": len(hl),
            # contrôle régime hors-HYPE
            "tr_net_exH": round(a[TR_NET_EXH], 2), "tr_n_exH": int(a[TR_N_EXH]),
            "te_net_exH": round(a[TE_NET_EXH], 2), "te_n_exH": int(a[TE_N_EXH]),
        }
        recs.append(rec)

    csv_path = DATA_DIR / f"veracity_v2_perwallet_{tag}.csv"
    if recs:
        with csv_path.open("w", newline="") as f:
            wc = _csv.DictWriter(f, fieldnames=list(recs[0].keys()))
            wc.writeheader()
            wc.writerows(recs)

    def run_oos(quals, label, med_base, metric="te_net", n_field="te_n"):
        active = [q for q in quals if q[n_field] > 0]
        na = len(active)
        if na < 10:
            print(f"\n[{label}] {na} actifs test → INCONCLUSIVE", flush=True)
            return {"label": label, "n_qualifiers": len(quals),
                    "active_in_test": na, "verdict": "INCONCLUSIVE"}
        rho_wr = spearman([q["tr_wr_close"] for q in active],
                          [q[metric] for q in active])
        rho_pnl = spearman([q["tr_net"] for q in active],
                           [q[metric] for q in active])
        bywr = sorted(active, key=lambda q: q["tr_wr_close"])
        qn = len(bywr) // 4
        top = [q[metric] for q in bywr[-qn:]] if qn else []
        bot = [q[metric] for q in bywr[:qn]] if qn else []
        pct = sum(1 for q in active if q[metric] > 0) / na * 100
        mq = med([q[metric] for q in active])
        p_rho = rho_wr > 0.15 or rho_pnl > 0.15
        p_q = med(top) > med(bot) and med(top) > 0
        p_p = pct > 55
        p_l = mq > med_base
        npass = sum([p_rho, p_q, p_p, p_l])
        verdict = "PASS" if npass >= 3 else "FAIL"
        print(f"\n{'='*56}\n  [{label}] {len(quals):,} quals, {na:,} actifs "
              f"(métrique={metric})\n{'='*56}", flush=True)
        print(f"  Spearman WRclose/PnL : {rho_wr:+.4f} ({'P' if rho_wr>0.15 else '-'})", flush=True)
        print(f"  Spearman PnLtr/PnL   : {rho_pnl:+.4f} ({'P' if rho_pnl>0.15 else '-'})", flush=True)
        print(f"  Top/Bot quartile     : ${med(top):+,.0f} / ${med(bot):+,.0f} ({'P' if p_q else '-'})", flush=True)
        print(f"  %% rentables test    : {pct:.1f}% ({'P' if p_p else '-'})", flush=True)
        print(f"  médiane qual/base    : ${mq:+,.2f} / ${med_base:+,.2f} ({'P' if p_l else '-'})", flush=True)
        print(f"  → VERDICT {label} : {verdict} ({npass}/4)", flush=True)
        return {"label": label, "metric": metric, "n_qualifiers": len(quals),
                "active_in_test": na, "spearman_wr": round(rho_wr, 4),
                "spearman_pnl": round(rho_pnl, 4), "top_q_med_te": round(med(top), 2),
                "bot_q_med_te": round(med(bot), 2), "pct_profitable_test": round(pct, 1),
                "med_te_qual": round(mq, 2), "med_te_baseline": round(med_base, 2),
                "n_criteria_pass": npass, "verdict": verdict}

    base_q = [r for r in recs if r["tr_n"] >= QUAL_N_MIN and r["tr_wr_close"] is not None
              and 0.55 <= r["tr_wr_close"] < 0.90 and r["tr_net"] > 0]
    copy_q = [r for r in base_q if (r["taker_ratio"] or 0) >= COPY_TAKER_MIN
              and r["tr_gross"] > 0 and (r["tr_ret_bps"] or 0) > rt_cost_bps]
    base_addrs = {r["addr"] for r in base_q}
    # baseline = NON-qualifiers actifs (faille baseline contaminée corrigée)
    baseline_te = [r["te_net"] for r in recs if r["tr_n"] >= QUAL_N_MIN
                   and r["tr_wr_close"] is not None and r["te_n"] > 0
                   and r["addr"] not in base_addrs]
    baseline_exH = [r["te_net_exH"] for r in recs if r["tr_n"] >= QUAL_N_MIN
                    and r["tr_wr_close"] is not None and r["te_n_exH"] > 0
                    and r["addr"] not in base_addrs]
    med_base = med(baseline_te)
    med_base_exH = med(baseline_exH)
    print(f"\nqualifiers WRclose55-90 : {len(base_q):,} | copiables v2 "
          f"(taker≥{COPY_TAKER_MIN} & ret_bps>{rt_cost_bps} mesuré & gross>0) : "
          f"{len(copy_q):,} | baseline non-qual N={len(baseline_te):,}", flush=True)
    res_all = run_oos(base_q, "TOUS qualifiers", med_base)
    res_copy = run_oos(copy_q, "COPIABLES v2", med_base)
    # CONTRÔLE RÉGIME : les copiables persistent-ils OOS SANS HYPE ?
    res_copy_exH = run_oos(copy_q, "COPIABLES v2 hors-HYPE", med_base_exH,
                           metric="te_net_exH", n_field="te_n_exH")
    regime_robust = (res_copy.get("verdict") == "PASS"
                     and res_copy_exH.get("verdict") == "PASS")
    print(f"\n  >>> ROBUSTESSE RÉGIME : copiables {'SURVIVENT' if regime_robust else 'NE SURVIVENT PAS'} "
          f"au retrait de HYPE → {'edge copiable réel' if regime_robust else 'SUSPICION pari-régime HYPE'}",
          flush=True)

    out = {"days": args.days, "end_date": args.end_date, "cutoff": CUTOFF_DATE,
           "total_events": int(total_events), "total_mb_dl": round(total_mb, 1),
           "wallets_final": len(stats), "wallets_dumped": len(recs),
           "eff_taker_bps": round(eff_taker_bps, 4), "rt_cost_bps": rt_cost_bps,
           "n_qualifiers_all": len(base_q), "n_qualifiers_copyable": len(copy_q),
           "binding_verdict": "COPIABLES v2", "regime_robust_exHYPE": regime_robust,
           "oos_all": res_all, "oos_copyable": res_copy,
           "oos_copyable_exHYPE": res_copy_exH, "perwallet_csv": csv_path.name,
           "peak_rss_mb": round(_rss_mb(), 1)}
    out_path = DATA_DIR / f"veracity_v2_{tag}.json"
    out_path.write_text(json.dumps(out, indent=2))
    if ckpt_path.exists():
        ckpt_path.unlink()
    print(f"\n  DL {total_mb:.0f} MB ≈ ${total_mb/1024*0.09:.2f} | "
          f"elapsed {time.time()-t0:.0f}s | RSS pic {_rss_mb():.0f} MB", flush=True)
    print(f"→ {out_path}\n→ {csv_path}", flush=True)


if __name__ == "__main__":
    main()
