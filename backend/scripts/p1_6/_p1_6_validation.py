"""HyperDex Phase 1.6 — Validation pool 531 wallets (cohort + HyperTracker + HyperStats).

Méthodologie Phase 1.5 v2 sur pool ÉLARGI :
- Pool fusionné 333 baseline (cohort consistent_set) + 198 nouveaux candidats.
- A4 fix : hold_logical via `compute_hold_ms_logical()` (agrège fills atomiques
  d'un même ordre, pas le delta inter-fills 800-1000 ms).
- Filtre hold ÉLARGI : [10s, 48h] (vs [5min, 48h] v1.5) — autorise scalp/HFT
  borderline pour exposer les segments Tokyo scalp + HFT.
- Tagging par tier : pure_HFT / hft_borderline / scalp_tokyo_eligible / scalp /
  swing / excluded.
- Bonferroni N=531, alpha=0.05/531 ≈ 9.4e-5, z_crit ≈ 3.88.
- Sub-window consistency 3/3 (train).
- Test agrégé Sharpe top vs bottom quartile.

Cache fills :
- Baseline 333 : réutilise `data/p1/fills_raw_p1_5.jsonl`.
- Nouveaux 198 : append dans `data/p1_6/fills_raw_p1_6_new.jsonl`.

Stream-processing : iter ligne par ligne le JSONL, jamais tout en RAM.

Idempotence :
- Skip wallets déjà fetchés (présents dans un des deux JSONL).
- Filtres univers `accountValue` $5k-$100M, monthPnL>0 OR allTimePnL>0
  via `user_state()` — appliqués UNIQUEMENT aux 198 nouveaux (les 333 baseline
  sont déjà filtrés en Phase 1.5).

Outputs :
- `data/p1_6/p1_6_verdict.md`
- `data/p1_6/validated_pool.csv`
- `data/p1_6/all_metrics.json`

Usage :
    python3 scripts/p1_6/_p1_6_validation.py [--pool-file PATH] [--dry-run-n N]
    python3 scripts/p1_6/_p1_6_validation.py --sanity-only
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, "/home/dexter/hyperdex/backend")
from app.services.hl_api.info_client import InfoClient  # noqa: E402
from app.services.paper.wallet_perf import (  # noqa: E402
    compute_hold_ms_logical,
)

# --- config univers (uniquement appliqué aux nouveaux 198) ---
MIN_ACCOUNT_VALUE = 5_000.0
MAX_ACCOUNT_VALUE = 100_000_000.0
MIN_WEEKLY_VOLUME = 0.0

# --- fenêtre temporelle (identique P1.5) ---
WINDOW_DAYS = 90
HOLDOUT_DAYS = 30
TRAIN_DAYS = WINDOW_DAYS - HOLDOUT_DAYS  # 60
N_SUB_PERIODS = 3

# --- filtres copiable (hold ÉLARGI v1.6) ---
MIN_FILLS = 50
HOLD_MIN_MS = 10 * 1000  # 10 secondes
HOLD_MAX_MS = 48 * 60 * 60 * 1000  # 48h
MIN_HOLD_N = 20

# --- tiers (hold_logical en ms) ---
TIER_PURE_HFT_MAX_MS = 10_000  # < 10s
TIER_BORDERLINE_MAX_MS = 60_000  # 10s-1min
TIER_TOKYO_SCALP_MAX_MS = 5 * 60 * 1000  # 1min-5min
TIER_SCALP_MAX_MS = 60 * 60 * 1000  # 5min-1h
TIER_SWING_MAX_MS = 48 * 60 * 60 * 1000  # 1h-48h

# --- chemins ---
ROOT = Path("/home/dexter/hyperdex/backend")
OUT_DIR = ROOT / "data" / "p1_6"
OUT_DIR.mkdir(parents=True, exist_ok=True)
BASELINE_CSV = ROOT / "data" / "p1" / "consistent_set.csv"
BASELINE_FILLS_JSONL = ROOT / "data" / "p1" / "fills_raw_p1_5.jsonl"
NEW_FILLS_JSONL = OUT_DIR / "fills_raw_p1_6_new.jsonl"
DEFAULT_POOL_FILE = OUT_DIR / "merged_pool.json"
VERDICT_MD = OUT_DIR / "p1_6_verdict.md"
VALIDATED_CSV = OUT_DIR / "validated_pool.csv"
ALL_METRICS_JSON = OUT_DIR / "all_metrics.json"

NOW = datetime.now(timezone.utc)
WINDOW_START_MS = int((NOW - timedelta(days=WINDOW_DAYS)).timestamp() * 1000)
HOLDOUT_CUTOFF_MS = int((NOW - timedelta(days=HOLDOUT_DAYS)).timestamp() * 1000)


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------------------
# Pool loading
# ---------------------------------------------------------------------------

def load_baseline_meta(path: Path) -> dict[str, dict]:
    """Load 333 baseline wallets from consistent_set.csv."""
    out = {}
    with path.open() as f:
        r = csv.DictReader(f)
        for row in r:
            a = (row.get("addr") or "").lower()
            if not a.startswith("0x") or len(a) != 42:
                continue
            out[a] = {
                "source": "baseline",
                "p1_5_n": int(row.get("n", 0)),
                "p1_5_hold_med_min": float(row.get("hold_med", 0)),
                "p1_5_sharpe": float(row.get("sharpe", 0)),
                "p1_5_top_coin": row.get("top_coin", ""),
            }
    return out


def load_new_candidates(pool_file: Path) -> dict[str, dict]:
    """Load 198 new candidates from merged_pool.json."""
    raw = json.loads(pool_file.read_text())
    out = {}
    for c in raw.get("new_candidates", []):
        a = (c.get("address") or "").lower()
        if not a.startswith("0x") or len(a) != 42:
            continue
        src = []
        if c.get("source_hypertracker"):
            src.append("hypertracker")
        if c.get("source_hyperstats"):
            src.append("hyperstats")
        out[a] = {
            "source": "new:" + "+".join(src) if src else "new:unknown",
            "ht_perp_pnl": c.get("ht_perp_pnl"),
            "ht_equity": c.get("ht_equity"),
            "hs_grade": c.get("hs_grade"),
            "hs_winRate": c.get("hs_winRate"),
            "hs_totalPnl": c.get("hs_totalPnl"),
            "hs_mainToken": c.get("hs_mainToken"),
        }
    return out


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def scan_cached_addrs(path: Path) -> set[str]:
    """Scan JSONL for already-fetched wallet addresses (set only, no fills)."""
    if not path.exists():
        return set()
    out = set()
    with path.open() as fh:
        for line in fh:
            try:
                obj = json.loads(line)
                w = obj.get("wallet")
                if w:
                    out.add(w.lower())
            except Exception:
                continue
    return out


def append_jsonl(path: Path, wallet: str, fills: list) -> None:
    with path.open("a") as fh:
        fh.write(json.dumps({"wallet": wallet, "fills": fills}) + "\n")


# ---------------------------------------------------------------------------
# Univers filters (only applied to new 198)
# ---------------------------------------------------------------------------

def passes_universe_filters(state: dict) -> tuple[bool, str]:
    """Apply Phase 1.5 univers filters from user_state response.

    Returns (passes, reason_if_reject).
    Tolerant: missing fields → pass (don't silently exclude on missing data).
    """
    try:
        ms = state.get("marginSummary") or {}
        av = float(ms.get("accountValue", 0) or 0)
    except (TypeError, ValueError):
        av = 0.0
    if av and not (MIN_ACCOUNT_VALUE <= av <= MAX_ACCOUNT_VALUE):
        return False, f"accountValue={av:.0f} out of range"
    # monthPnL / allTimePnL data lives in leaderboard, not user_state.
    # We skip this check since new candidates already filtered upstream by
    # HyperTracker/HyperStats discovery (positive perp_pnl / totalPnl).
    return True, ""


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def classify_tier(hold_ms: float) -> str:
    if hold_ms < TIER_PURE_HFT_MAX_MS:
        return "pure_HFT"
    if hold_ms < TIER_BORDERLINE_MAX_MS:
        return "hft_borderline"
    if hold_ms < TIER_TOKYO_SCALP_MAX_MS:
        return "scalp_tokyo_eligible"
    if hold_ms < TIER_SCALP_MAX_MS:
        return "scalp"
    if hold_ms < TIER_SWING_MAX_MS:
        return "swing"
    return "excluded"


def sharpe_per_fill(pnls):
    if len(pnls) < 5:
        return 0.0
    m = statistics.mean(pnls)
    s = statistics.stdev(pnls)
    if s == 0:
        return 0.0
    return m / s * math.sqrt(len(pnls))


def max_drawdown(pnls_chrono):
    if not pnls_chrono:
        return 0.0
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls_chrono:
        cum += p
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
    return max_dd


def sub_window_positive(fills_with_ts, n_sub, t_start_ms, t_end_ms):
    if t_end_ms <= t_start_ms:
        return False
    edges = [t_start_ms + i * (t_end_ms - t_start_ms) // n_sub
             for i in range(n_sub + 1)]
    for i in range(n_sub):
        lo, hi = edges[i], edges[i + 1]
        s = sum(p for (ts, p) in fills_with_ts if lo <= ts < hi)
        if s <= 0:
            return False
    return True


def top_coin(fills: list) -> str:
    from collections import Counter
    c = Counter(f.get("coin", "") for f in fills if f.get("coin"))
    if not c:
        return ""
    return c.most_common(1)[0][0]


def compute_wallet_metrics(addr: str, fills: list) -> dict | None:
    """Compute full metric set from raw fills. Returns None if disqualified."""
    n = len(fills)
    if n < MIN_FILLS:
        return {"addr": addr, "n": n, "reject": "n<50"}

    closures = compute_hold_ms_logical(fills)
    if not closures:
        return {"addr": addr, "n": n, "reject": "no_closed_cycles"}

    holds_ms = sorted(c["hold_ms"] for c in closures)
    hold_med_ms = holds_ms[len(holds_ms) // 2]
    tier = classify_tier(hold_med_ms)

    fills_sorted = sorted(fills, key=lambda f: int(f.get("time", 0)))
    ts_pnl = [(int(f.get("time", 0)), float(f.get("closedPnl", 0) or 0))
              for f in fills_sorted]
    train_tp = [x for x in ts_pnl if x[0] < HOLDOUT_CUTOFF_MS]
    hold_tp = [x for x in ts_pnl if x[0] >= HOLDOUT_CUTOFF_MS]
    train_pnls = [p for _, p in train_tp]
    hold_pnls = [p for _, p in hold_tp]

    sharpe = sharpe_per_fill([p for _, p in ts_pnl])
    dd = max_drawdown([p for _, p in ts_pnl])
    sub_ok = sub_window_positive(train_tp, N_SUB_PERIODS,
                                  WINDOW_START_MS, HOLDOUT_CUTOFF_MS)
    hold_ok = sum(hold_pnls) > 0

    t_stat = None
    if len(hold_pnls) >= 5:
        m = statistics.mean(hold_pnls)
        s = statistics.stdev(hold_pnls) if len(hold_pnls) > 1 else 0
        if s > 0:
            t_stat = m / (s / math.sqrt(len(hold_pnls)))

    return {
        "addr": addr,
        "n": n,
        "n_closures": len(closures),
        "hold_logical_med_ms": hold_med_ms,
        "hold_logical_med_min": hold_med_ms / 60_000.0,
        "tier": tier,
        "train_n": len(train_pnls),
        "train_pnl": sum(train_pnls),
        "hold_n": len(hold_pnls),
        "hold_pnl": sum(hold_pnls),
        "sharpe": sharpe,
        "max_dd": dd,
        "sub_ok": sub_ok,
        "hold_ok": hold_ok,
        "t_stat": t_stat,
        "top_coin": top_coin(fills),
    }


def metric_passes_hold_filter(m: dict) -> bool:
    if "reject" in m:
        return False
    h = m.get("hold_logical_med_ms", 0)
    return HOLD_MIN_MS <= h <= HOLD_MAX_MS


# ---------------------------------------------------------------------------
# Sanity tests
# ---------------------------------------------------------------------------

KNOWN_SANITY_WALLETS = [
    "0x25608292189bb759333d62bb2e776d32e997659d",  # top P1.5 baseline
    "0x1005996ecc88a05f1e1a059d22fc7a4d6c5b32fb",  # HYPE scalper (close enough)
    "0x0e3f5bb797e3953fed2a319544d0e821b4a11e1c",  # high-volume baseline
]


def run_sanity(addrs: list[str]):
    log("\n=== SANITY TESTS ===")
    # Scan baseline cache for these addrs
    found: dict[str, list] = {}
    if BASELINE_FILLS_JSONL.exists():
        with BASELINE_FILLS_JSONL.open() as fh:
            for line in fh:
                try:
                    obj = json.loads(line)
                    w = (obj.get("wallet") or "").lower()
                    if w in {a.lower() for a in addrs}:
                        found[w] = obj.get("fills", [])
                        if len(found) == len(addrs):
                            break
                except Exception:
                    continue
    for a in addrs:
        al = a.lower()
        fills = found.get(al)
        if fills is None:
            log(f"  [SANITY] {al[:14]}... NOT_IN_CACHE  → will fetch later")
            continue
        m = compute_wallet_metrics(al, fills)
        if not m:
            log(f"  [SANITY] {al[:14]}... no metrics")
            continue
        log(
            f"  [SANITY] {al[:14]}... n={m['n']} "
            f"hold_logical_med={m.get('hold_logical_med_min', 0):.1f}min "
            f"tier={m.get('tier')} sharpe={m.get('sharpe', 0):.2f} "
            f"t_stat={m.get('t_stat')}"
        )


# ---------------------------------------------------------------------------
# Verdict writer
# ---------------------------------------------------------------------------

def write_outputs(metrics_eligible: list[dict], copyables: list[dict],
                   consistent: list[dict], validated: list[dict],
                   spread: float, spread_sh: float, q: int, N_pool: int,
                   z_crit: float, alpha: float,
                   top_q_hold: float, bot_q_hold: float,
                   top_sh_hold: float, bot_sh_hold: float,
                   meta_by_addr: dict, all_metrics: list[dict]):
    # --- markdown ---
    lines: list[str] = []
    lines.append("# HyperDex Phase 1.6 — Verdict pool élargi 531\n")
    lines.append(f"_Run : {datetime.now(timezone.utc).isoformat()}_\n")
    lines.append("## Méthodologie")
    lines.append(f"- Pool : 333 cohort baseline (consistent_set.csv) + 198 new "
                 f"candidates (HyperTracker/HyperStats merged_pool.json) = **{N_pool}**")
    lines.append(f"- Fenêtre {WINDOW_DAYS}j / Holdout OOS {HOLDOUT_DAYS}j / "
                 f"Train {TRAIN_DAYS}j en {N_SUB_PERIODS} sous-périodes.")
    lines.append("- A4 fix : `compute_hold_ms_logical()` agrège fills atomiques "
                 "(plus de hold artificiel ~870 ms).")
    lines.append(f"- Filtre hold ÉLARGI : [{HOLD_MIN_MS/1000:.0f}s, "
                 f"{HOLD_MAX_MS/3_600_000:.0f}h] (vs [5min, 48h] v1.5).")
    lines.append("- Tiers : pure_HFT (<10s) / hft_borderline (<1min) / "
                 "scalp_tokyo_eligible (<5min) / scalp (<1h) / swing (<48h) / excluded.")
    lines.append(f"- Bonferroni N={N_pool}, alpha=0.05/{N_pool}={alpha:.2e}, "
                 f"z_crit={z_crit:.2f}.\n")

    lines.append("## Résultats")
    lines.append(f"- Pool total : **{N_pool}**")
    lines.append(f"- Wallets metrics computés (hold filter, n>=50) : **{len(metrics_eligible)}**")
    lines.append(f"- Copyables (train+, holdout+, hold_n>=20) : **{len(copyables)}**")
    lines.append(f"- + Sub-window consistency 3/3 : **{len(consistent)}**")
    lines.append(f"- + Bonferroni z>{z_crit:.2f} : **{len(validated)} VALIDÉS**\n")

    # Distribution par tier
    from collections import Counter
    tier_count = Counter(m.get("tier", "?") for m in metrics_eligible)
    lines.append("## Distribution par tier (eligible après hold filter)")
    for t, c in tier_count.most_common():
        lines.append(f"- {t} : {c}")
    lines.append("")

    lines.append("## Test agrégé Sharpe top/bot quartile")
    lines.append(f"- Top quartile par train PnL → holdout : ${top_q_hold:+,.0f}  "
                 f"vs bottom : ${bot_q_hold:+,.0f}  spread=${spread:+,.0f}")
    lines.append(f"- Top quartile par Sharpe → holdout : ${top_sh_hold:+,.0f}  "
                 f"vs bottom : ${bot_sh_hold:+,.0f}  spread=${spread_sh:+,.0f}\n")

    lines.append("## VERDICT")
    if len(validated) >= 5 and spread_sh > 0:
        lines.append(f"### **OUI — {len(validated)} traders validés Bonferroni N=531** "
                     f"(consistance 3/3 + sub-window train + holdout positif + "
                     f"Sharpe top-Q prédit OOS).\n")
    elif len(validated) >= 3:
        lines.append(f"### **OUI faible — {len(validated)} traders validés.** "
                     f"Échantillon mince, à confirmer en P2 paper avant scaling.\n")
    elif len(consistent) >= 5:
        lines.append(f"### **TIÈDE — {len(consistent)} consistants mais aucun ne passe "
                     f"Bonferroni N={N_pool} (z>{z_crit:.2f}).** Sample insuffisant.\n")
    else:
        lines.append(f"### **NON — pas d'edge robuste sous {HOLD_MAX_MS//3_600_000}h "
                     f"sur le pool 531.** consistants : {len(consistent)}, "
                     f"Bonferroni : {len(validated)}.\n")

    if validated:
        lines.append("## Traders VALIDÉS (sortie par Sharpe desc)")
        lines.append("| wallet | source | n | tier | hold_med | train | holdout | "
                     "Sharpe | max_DD | t_stat |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|")
        validated_sorted = sorted(validated, key=lambda m: -m["sharpe"])
        for m in validated_sorted[:30]:
            src = (meta_by_addr.get(m["addr"], {}).get("source") or "")[:20]
            hold_min = m["hold_logical_med_min"]
            hold_str = (f"{hold_min:.0f}min" if hold_min >= 1
                        else f"{hold_min*60:.0f}s")
            lines.append(
                f"| {m['addr'][:14]} | {src} | {m['n']} | {m['tier']} | "
                f"{hold_str} | ${m['train_pnl']:+,.0f} | "
                f"${m['hold_pnl']:+,.0f} | {m['sharpe']:.2f} | "
                f"${m['max_dd']:,.0f} | {m['t_stat']:.2f} |"
            )

    if consistent and len(consistent) != len(validated):
        lines.append("\n## Consistants mais non-Bonferroni")
        lines.append("| wallet | source | n | tier | hold_med | train | holdout | "
                     "Sharpe | t_stat |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        validated_set = {m["addr"] for m in validated}
        consist_sorted = sorted(consistent, key=lambda m: -m["sharpe"])
        c2 = 0
        for m in consist_sorted:
            if m["addr"] in validated_set:
                continue
            src = (meta_by_addr.get(m["addr"], {}).get("source") or "")[:20]
            hold_min = m["hold_logical_med_min"]
            hold_str = (f"{hold_min:.0f}min" if hold_min >= 1
                        else f"{hold_min*60:.0f}s")
            ts_s = f"{m['t_stat']:.2f}" if m["t_stat"] is not None else "—"
            lines.append(
                f"| {m['addr'][:14]} | {src} | {m['n']} | {m['tier']} | "
                f"{hold_str} | ${m['train_pnl']:+,.0f} | "
                f"${m['hold_pnl']:+,.0f} | {m['sharpe']:.2f} | {ts_s} |"
            )
            c2 += 1
            if c2 >= 20:
                break

    VERDICT_MD.write_text("\n".join(lines))
    log(f"\n=> verdict -> {VERDICT_MD}")

    # --- validated_pool.csv ---
    with VALIDATED_CSV.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["addr", "n", "hold_logical_med_min", "tier", "train_pnl",
                    "holdout_pnl", "sharpe", "t_stat", "top_coin", "source"])
        for m in sorted(validated, key=lambda m: -m["sharpe"]):
            src = meta_by_addr.get(m["addr"], {}).get("source", "")
            w.writerow([
                m["addr"], m["n"],
                round(m["hold_logical_med_min"], 2),
                m["tier"],
                round(m["train_pnl"], 2),
                round(m["hold_pnl"], 2),
                round(m["sharpe"], 4),
                round(m["t_stat"], 4) if m.get("t_stat") is not None else "",
                m.get("top_coin", ""),
                src,
            ])
    log(f"=> validated csv -> {VALIDATED_CSV}")

    # --- all_metrics.json (full debug) ---
    ALL_METRICS_JSON.write_text(json.dumps({
        "pool_size": N_pool,
        "alpha": alpha,
        "z_crit": z_crit,
        "n_eligible": len(metrics_eligible),
        "n_copyables": len(copyables),
        "n_consistent": len(consistent),
        "n_validated": len(validated),
        "metrics": all_metrics,
    }, indent=2, default=str))
    log(f"=> all metrics -> {ALL_METRICS_JSON}")


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool-file", default=str(DEFAULT_POOL_FILE))
    ap.add_argument("--dry-run-n", type=int, default=0,
                    help="If >0, fetch+process only N new candidates (test mode)")
    ap.add_argument("--sanity-only", action="store_true")
    ap.add_argument("--no-fetch", action="store_true",
                    help="Skip HL fetch; only process already-cached fills")
    args = ap.parse_args()

    log("=== HyperDex Phase 1.6 — Validation pool 531 ===")
    log(f"window={WINDOW_DAYS}d  holdout={HOLDOUT_DAYS}d")
    log(f"hold filter : {HOLD_MIN_MS/1000:.0f}s ≤ median ≤ "
        f"{HOLD_MAX_MS/3_600_000:.0f}h")

    # --- Load pool meta ---
    baseline_meta = load_baseline_meta(BASELINE_CSV)
    new_meta = load_new_candidates(Path(args.pool_file))
    meta_by_addr: dict[str, dict] = {**baseline_meta, **new_meta}
    pool_addrs = list(meta_by_addr.keys())
    N_pool = len(pool_addrs)
    log(f"[1] pool : {len(baseline_meta)} baseline + {len(new_meta)} new = {N_pool}")

    # --- Sanity ---
    run_sanity(KNOWN_SANITY_WALLETS)
    if args.sanity_only:
        return

    # --- Fetch new candidates ---
    fetched_baseline = scan_cached_addrs(BASELINE_FILLS_JSONL)
    fetched_new = scan_cached_addrs(NEW_FILLS_JSONL)
    log(f"[2] cache : baseline={len(fetched_baseline)} new={len(fetched_new)}")

    new_addrs = list(new_meta.keys())
    missing_baseline = [a for a in baseline_meta if a not in fetched_baseline]
    missing_new = [a for a in new_addrs if a not in fetched_new]
    if missing_baseline:
        log(f"  WARN : {len(missing_baseline)} baseline missing from cache "
            f"-- will be fetched into NEW jsonl as fallback")

    to_fetch = missing_baseline + missing_new
    if args.dry_run_n > 0:
        to_fetch = to_fetch[:args.dry_run_n]
        log(f"  DRY-RUN : limiting fetch to {len(to_fetch)} wallets")

    if to_fetch and not args.no_fetch:
        ic = InfoClient(mainnet=True, min_interval_s=1.1)
        t0 = time.time()
        for i, addr in enumerate(to_fetch):
            # --- univers filter via user_state (only for new candidates) ---
            if addr in new_meta:
                try:
                    state = ic.user_state(addr)
                except Exception as e:
                    log(f"  [{i+1}/{len(to_fetch)}] {addr[:14]} user_state FAIL "
                        f"{type(e).__name__} -- skip")
                    append_jsonl(NEW_FILLS_JSONL, addr, [])
                    continue
                passes, reason = passes_universe_filters(state)
                if not passes:
                    log(f"  [{i+1}/{len(to_fetch)}] {addr[:14]} REJ univers : {reason}")
                    append_jsonl(NEW_FILLS_JSONL, addr, [])
                    continue

            try:
                fills = ic.user_fills_by_time(addr, WINDOW_START_MS)
            except Exception as e:
                log(f"  [{i+1}/{len(to_fetch)}] {addr[:14]} fetch FAIL "
                    f"{type(e).__name__} -- empty fills")
                fills = []
            append_jsonl(NEW_FILLS_JSONL, addr, fills)
            n_f = len(fills)
            if (i + 1) % 10 == 0 or n_f < MIN_FILLS:
                el = time.time() - t0
                eta = el / (i + 1) * (len(to_fetch) - i - 1) if (i + 1) else 0
                log(f"  [{i+1}/{len(to_fetch)}] {addr[:14]} fills={n_f} "
                    f"el={el:.0f}s eta={eta:.0f}s")
            del fills

        log(f"  fetch done in {time.time()-t0:.0f}s")

    # --- STREAM metrics from BOTH jsonl files ---
    log("\n[3] metrics streaming from JSONL caches...")
    metrics: list[dict] = []
    pool_set = set(pool_addrs)
    processed = set()

    for path in (BASELINE_FILLS_JSONL, NEW_FILLS_JSONL):
        if not path.exists():
            continue
        with path.open() as fh:
            for line in fh:
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                addr = (obj.get("wallet") or "").lower()
                if not addr or addr not in pool_set or addr in processed:
                    continue
                fills = obj.get("fills", [])
                m = compute_wallet_metrics(addr, fills)
                del fills
                if m is None:
                    continue
                metrics.append(m)
                processed.add(addr)
                idx = len(metrics)
                if idx <= 20 or idx % 50 == 0:
                    if "reject" in m:
                        log(f"  [{idx}/{N_pool}] {addr[:14]} REJ {m['reject']}")
                    else:
                        log(
                            f"  [{idx}/{N_pool}] {addr[:14]} n={m['n']} "
                            f"hold={m['hold_logical_med_min']:.1f}min "
                            f"tier={m['tier']} sharpe={m['sharpe']:.2f}"
                        )
    missing = pool_set - processed
    if missing:
        log(f"  WARN : {len(missing)} pool wallets not found in any cache")

    # --- Filter eligibility ---
    eligible = [m for m in metrics if metric_passes_hold_filter(m)]
    log(f"\n[4] eligible after hold filter [10s, 48h] + n>=50 + closed_cycles : "
        f"{len(eligible)}/{len(metrics)}")

    copyables = [m for m in eligible
                 if m["train_pnl"] > 0 and m["hold_pnl"] > 0
                 and m["hold_n"] >= MIN_HOLD_N]
    log(f"  copyables (train+/holdout+/hold_n>=20) : {len(copyables)}")

    consistent = [m for m in copyables if m["sub_ok"] and m["hold_ok"]]
    log(f"  + sub-window 3/3 + holdout+ : {len(consistent)}")

    # Bonferroni N = pool size 531
    alpha = 0.05 / max(1, N_pool)
    z_crit = statistics.NormalDist().inv_cdf(1 - alpha / 2)
    validated = [m for m in consistent
                 if m["t_stat"] is not None and m["t_stat"] > z_crit]
    log(f"  + Bonferroni z>{z_crit:.2f} (alpha={alpha:.2e}) : {len(validated)}")

    # --- Aggregate quartile test ---
    pool_for_q = [m for m in eligible
                  if m.get("train_n", 0) >= 20 and m.get("hold_n", 0) >= 5]
    pool_for_q.sort(key=lambda x: -x["train_pnl"])
    q = max(1, len(pool_for_q) // 4)
    top_q_hold = sum(m["hold_pnl"] for m in pool_for_q[:q])
    bot_q_hold = sum(m["hold_pnl"] for m in pool_for_q[-q:])
    spread = top_q_hold - bot_q_hold

    by_sh = sorted(pool_for_q, key=lambda x: -x["sharpe"])
    top_q_sh = by_sh[:q]
    bot_q_sh = by_sh[-q:]
    top_sh_hold = sum(m["hold_pnl"] for m in top_q_sh)
    bot_sh_hold = sum(m["hold_pnl"] for m in bot_q_sh)
    spread_sh = top_sh_hold - bot_sh_hold

    log("\n[5] aggregate :")
    log(f"  rang train PnL : top-Q hold=${top_q_hold:+,.0f}  bot-Q=${bot_q_hold:+,.0f} "
        f" spread=${spread:+,.0f}")
    log(f"  rang Sharpe   : top-Q hold=${top_sh_hold:+,.0f}  bot-Q=${bot_sh_hold:+,.0f}"
        f"  spread=${spread_sh:+,.0f}")

    write_outputs(eligible, copyables, consistent, validated,
                  spread, spread_sh, q, N_pool, z_crit, alpha,
                  top_q_hold, bot_q_hold, top_sh_hold, bot_sh_hold,
                  meta_by_addr, metrics)


if __name__ == "__main__":
    main()
