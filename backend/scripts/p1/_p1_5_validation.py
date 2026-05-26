"""HyperDex Phase 1.5 — Discovery & Validation REFINÉE.

Améliorations vs P1 :
- Univers ÉLARGI : tous les wallets leaderboard actifs (filtre weeklyVolume>0,
  accountValue $5k-$50M, monthPnL>0). Hard cap 3000 par monthPnL desc.
- Filtre hold <= 48h (opérateur : pas de positions >48h).
- Métriques refinées : Sharpe, max drawdown, consistance sous-périodes (3 sub-
  windows train + holdout : positif partout).
- Tests combinés : Bonferroni per-wallet + sub-window consistency + Sharpe rank.

Pacing 1.1s (~55 req/min, sous le budget HL 1200 poids/min).
Cache fills_raw.json (réutilise P1 pour overlap).
"""
from __future__ import annotations

import json
import math
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, "/opt/app/hyperdex/backend")
from app.services.hl_api.info_client import InfoClient  # noqa: E402

# --- config univers ---
MIN_ACCOUNT_VALUE = 5_000.0
MAX_ACCOUNT_VALUE = 100_000_000.0
MIN_WEEKLY_VOLUME = 0.0  # juste actif (>0)
HARD_CAP = 10000  # pas de cap réel : univers actif ~7000

# --- fenêtre temporelle ---
WINDOW_DAYS = 90
HOLDOUT_DAYS = 30
TRAIN_DAYS = WINDOW_DAYS - HOLDOUT_DAYS  # 60
N_SUB_PERIODS = 3  # train splité en 3 sous-fenêtres pour test consistance

# --- filtres copiable ---
MIN_FILLS = 50
HOLD_MIN_MINUTES = 5  # exclut HFT pur
HOLD_MAX_MINUTES = 48 * 60  # ← opérateur : pas de position >48h
MIN_HOLD_N = 20

# --- chemins ---
OUT_DIR = Path("/opt/app/hyperdex/backend/data/p1")
OUT_DIR.mkdir(parents=True, exist_ok=True)
FILLS_JSONL = OUT_DIR / "fills_raw_p1_5.jsonl"  # incrémental (1 wallet par ligne)
LEGACY_CACHE = OUT_DIR / "fills_raw.json"  # cache P1, à réutiliser
VERDICT_MD = OUT_DIR / "phase1_5_verdict.md"


def load_jsonl(path: Path) -> dict:
    """Charge un cache JSONL en dict {wallet: fills}."""
    if not path.exists():
        return {}
    d = {}
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                d[obj["wallet"]] = obj["fills"]
            except Exception:
                continue
    return d


def append_jsonl(path: Path, wallet: str, fills: list) -> None:
    """Append une ligne JSONL pour un wallet — pas de re-sérialisation globale."""
    with open(path, "a") as fh:
        fh.write(json.dumps({"wallet": wallet, "fills": fills}) + "\n")

NOW = datetime.now(timezone.utc)
WINDOW_START_MS = int((NOW - timedelta(days=WINDOW_DAYS)).timestamp() * 1000)
HOLDOUT_CUTOFF_MS = int((NOW - timedelta(days=HOLDOUT_DAYS)).timestamp() * 1000)


def log(*a):
    print(*a, flush=True)


def monthly_pnl(row):
    for w, p in row.get("windowPerformances", []):
        if w == "month":
            try:
                return float(p.get("pnl", 0))
            except Exception:
                return 0.0
    return 0.0


def weekly_volume(row):
    for w, p in row.get("windowPerformances", []):
        if w == "week":
            try:
                return float(p.get("vlm", 0))
            except Exception:
                return 0.0
    return 0.0


def alltime_pnl(row):
    for w, p in row.get("windowPerformances", []):
        if w == "allTime":
            try:
                return float(p.get("pnl", 0))
            except Exception:
                return 0.0
    return 0.0


def compute_hold_times_minutes(fills):
    sf = sorted(fills, key=lambda f: int(f.get("time", 0)))
    opens = {}  # (coin, side) -> ts
    holds = []
    for f in sf:
        coin = f.get("coin", "")
        d = (f.get("dir") or "").lower()
        ts = int(f.get("time", 0))
        if "open" in d:
            side = "long" if "long" in d else "short"
            opens.setdefault((coin, side), ts)
        elif "close" in d:
            side = "long" if "long" in d else "short"
            start = opens.pop((coin, side), None)
            if start is not None and ts > start:
                holds.append((ts - start) / 60000.0)
    return holds


def classify_profile(hold_med):
    if hold_med < HOLD_MIN_MINUTES:
        return "HFT"
    if hold_med < 240:
        return "intraday"
    if hold_med < HOLD_MAX_MINUTES:
        return "swing<=48h"
    return "long>48h"


def sharpe_per_fill(pnls):
    if len(pnls) < 5:
        return 0.0
    m = statistics.mean(pnls)
    s = statistics.stdev(pnls)
    if s == 0:
        return 0.0
    return m / s * math.sqrt(len(pnls))


def max_drawdown(pnls_chrono):
    """Max DD sur la courbe cumulée."""
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
    """Vrai si pnl positif dans chacune des n_sub sous-fenêtres."""
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


def main():
    log("=== HyperDex P1.5 — Discovery + Validation REFINÉE ===")
    log(f"window={WINDOW_DAYS}d  holdout={HOLDOUT_DAYS}d  train_sub_periods={N_SUB_PERIODS}")
    log(f"univers filtres : accountVal ${MIN_ACCOUNT_VALUE:,.0f}-${MAX_ACCOUNT_VALUE:,.0f}, "
        f"(monthPnL>0 OU allTimePnL>0), weeklyVol>${MIN_WEEKLY_VOLUME:,.0f}, hard_cap={HARD_CAP}")
    log(f"hold filter : {HOLD_MIN_MINUTES}min <= median <= {HOLD_MAX_MINUTES//60}h (>48h EXCLU)")

    ic = InfoClient(mainnet=True, min_interval_s=1.1)

    # --- Step 1 : leaderboard + filtres ---
    log("\n[1] fetch leaderboard...")
    lb = ic.fetch_leaderboard()
    log(f"  {len(lb)} traders total")

    universe = []
    for r in lb:
        av = float(r.get("accountValue", 0) or 0)
        mp = monthly_pnl(r)
        ap = alltime_pnl(r)
        wv = weekly_volume(r)
        # Union : monthPnL>0 OU allTimePnL>0, actif dernière semaine
        if (MIN_ACCOUNT_VALUE <= av <= MAX_ACCOUNT_VALUE
                and (mp > 0 or ap > 0)
                and wv > MIN_WEEKLY_VOLUME):
            universe.append(r)
    universe.sort(key=lambda r: -monthly_pnl(r))
    log(f"  après filtres : {len(universe)} traders actifs")
    if len(universe) > HARD_CAP:
        universe = universe[:HARD_CAP]
        log(f"  hard_cap appliqué -> {HARD_CAP}")

    # --- Step 2 : fetch fills STREAM (jamais tout en RAM) ---
    # On scanne JSONL pour le SET des wallets déjà fetchés (pas le contenu).
    fetched_addrs: set[str] = set()
    if FILLS_JSONL.exists():
        with open(FILLS_JSONL) as fh:
            for line in fh:
                try:
                    fetched_addrs.add(json.loads(line)["wallet"])
                except Exception:
                    continue
        log(f"\n[2] JSONL cache : {len(fetched_addrs)} wallets déjà fetchés (RAM only set, pas le contenu)")

    universe_addrs = [r["ethAddress"].lower() for r in universe]
    to_fetch = [a for a in universe_addrs if a not in fetched_addrs]
    log(f"  à fetcher : {len(to_fetch)} (sur {len(universe_addrs)} univers)")

    t0 = time.time()
    for i, addr in enumerate(to_fetch):
        try:
            fills = ic.user_fills_by_time(addr, WINDOW_START_MS)
        except Exception:
            fills = []
        append_jsonl(FILLS_JSONL, addr, fills)
        fetched_addrs.add(addr)
        # FILLS DROPPED FROM RAM IMMEDIATEMENT (pas de per_wallet dict)
        del fills
        if (i + 1) % 50 == 0:
            el = time.time() - t0
            eta = el / (i + 1) * (len(to_fetch) - i - 1)
            log(f"  ...{i+1}/{len(to_fetch)}  elapsed={el:.0f}s  ETA={eta:.0f}s")
    log(f"  fetch terminé. wallets en JSONL : {len(fetched_addrs)}")

    # --- Step 3 : métriques STREAM depuis JSONL (1 wallet à la fois) ---
    log("\n[3] métriques en streaming depuis JSONL...")
    universe_set = set(universe_addrs)
    metrics = []
    n_processed = 0
    with open(FILLS_JSONL) as fh:
        for line in fh:
            try:
                obj = json.loads(line)
            except Exception:
                continue
            addr = obj.get("wallet")
            if not addr or addr not in universe_set:
                continue
            fills = obj.get("fills", [])
            n = len(fills)
            if n < MIN_FILLS:
                del fills
                continue
            holds = compute_hold_times_minutes(fills)
            hold_med = statistics.median(holds) if holds else 0.0
            profile = classify_profile(hold_med)
            if not (HOLD_MIN_MINUTES <= hold_med <= HOLD_MAX_MINUTES):
                del fills
                continue
            fills_sorted = sorted(fills, key=lambda f: int(f.get("time", 0)))
            ts_pnl = [(int(f.get("time", 0)), float(f.get("closedPnl", 0)))
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
            metrics.append(dict(
                addr=addr, n=n, profile=profile, hold_med=hold_med,
                train_n=len(train_pnls), train_pnl=sum(train_pnls),
                hold_n=len(hold_pnls), hold_pnl=sum(hold_pnls),
                sharpe=sharpe, max_dd=dd,
                sub_ok=sub_ok, hold_ok=hold_ok, t_stat=t_stat,
            ))
            # drop fills immédiatement
            del fills, fills_sorted, ts_pnl, train_tp, hold_tp, train_pnls, hold_pnls
            n_processed += 1
            if n_processed % 500 == 0:
                log(f"  ...metrics processed: {n_processed}")
    log(f"  metrics finales : {len(metrics)} wallets (n>={MIN_FILLS}, hold<=48h)")

    # --- Step 4 : sélection multi-tests ---
    copyables = [m for m in metrics
                 if m["train_pnl"] > 0 and m["hold_pnl"] > 0
                 and m["hold_n"] >= MIN_HOLD_N]
    log(f"\n[4] sélection multi-couches")
    log(f"  copyables (train+, holdout+, hold_n>=20) : {len(copyables)}")

    consistent = [m for m in copyables if m["sub_ok"] and m["hold_ok"]]
    log(f"  + consistance sub-windows (train 3/3 + holdout +) : {len(consistent)}")

    N = len(metrics)
    alpha = 0.05 / max(1, N)
    z_crit = statistics.NormalDist().inv_cdf(1 - alpha / 2)
    bonf = [m for m in consistent
            if m["t_stat"] is not None and m["t_stat"] > z_crit]
    log(f"  + Bonferroni (z>{z_crit:.2f}, alpha={alpha:.6f}) : {len(bonf)}")

    # Top par Sharpe parmi les Bonferroni-survivants
    bonf.sort(key=lambda x: -x["sharpe"])
    validated = bonf  # tous les Bonferroni qui passent

    # Test agrégé sur metrics complets
    eligible = [m for m in metrics if m["train_n"] >= 20 and m["hold_n"] >= 5]
    eligible.sort(key=lambda x: -x["train_pnl"])
    q = max(1, len(eligible) // 4)
    top_q_hold = sum(m["hold_pnl"] for m in eligible[:q])
    bot_q_hold = sum(m["hold_pnl"] for m in eligible[-q:])
    spread = top_q_hold - bot_q_hold

    eligible_sh = sorted(metrics, key=lambda x: -x["sharpe"])
    top_q_sh = eligible_sh[:q]
    bot_q_sh = eligible_sh[-q:]
    top_sh_hold = sum(m["hold_pnl"] for m in top_q_sh)
    bot_sh_hold = sum(m["hold_pnl"] for m in bot_q_sh)
    spread_sh = top_sh_hold - bot_sh_hold

    log(f"\n[5] agrégés :")
    log(f"  rang train PnL : top-Q hold=${top_q_hold:+,.0f}  bot-Q=${bot_q_hold:+,.0f}  "
        f"spread=${spread:+,.0f}")
    log(f"  rang Sharpe   : top-Q hold=${top_sh_hold:+,.0f}  bot-Q=${bot_sh_hold:+,.0f}  "
        f"spread=${spread_sh:+,.0f}")

    # --- Step 6 : verdict ---
    write_verdict(metrics, copyables, consistent, validated,
                  spread, spread_sh, q, N, z_crit, alpha,
                  top_q_hold, bot_q_hold, top_sh_hold, bot_sh_hold)


def write_verdict(metrics, copyables, consistent, validated,
                  spread, spread_sh, q, N, z_crit, alpha,
                  top_q_hold, bot_q_hold, top_sh_hold, bot_sh_hold):
    lines = []
    lines.append("# HyperDex Phase 1.5 — Verdict GATE refiné\n")
    lines.append(f"_Run : {datetime.now(timezone.utc).isoformat()}_\n")
    lines.append("## Méthodo")
    lines.append(f"- Univers : leaderboard filtré (accountVal ${MIN_ACCOUNT_VALUE:,.0f}-${MAX_ACCOUNT_VALUE:,.0f}, monthPnL>0 OU allTimePnL>0, weeklyVol>${MIN_WEEKLY_VOLUME:,.0f}, hard_cap {HARD_CAP})")
    lines.append(f"- Fenêtre : {WINDOW_DAYS}j  /  Holdout OOS : {HOLDOUT_DAYS}j")
    lines.append(f"- Filtre hold : {HOLD_MIN_MINUTES}min ≤ median ≤ {HOLD_MAX_MINUTES//60}h (>48h **EXCLU** par doctrine opérateur)")
    lines.append(f"- Métriques : closedPnl total, Sharpe par fill, max DD, hold-time, sub-window consistency ({N_SUB_PERIODS} sous-périodes train)")
    lines.append(f"- Bonferroni : alpha=0.05/{N}={alpha:.6f}, z_crit={z_crit:.2f}\n")

    lines.append(f"## Résultats")
    lines.append(f"- Wallets retenus (n>={MIN_FILLS}, hold ≤ 48h) : **{N}**")
    lines.append(f"- Copyables (train+/holdout+/hold_n>=20) : **{len(copyables)}**")
    lines.append(f"- + Sub-window consistency (train 3/3 + holdout +) : **{len(consistent)}**")
    lines.append(f"- + Bonferroni : **{len(validated)} VALIDÉS**\n")

    lines.append(f"## Test agrégé")
    lines.append(f"- Top quartile par **train PnL** → holdout : ${top_q_hold:+,.0f}  vs bottom : ${bot_q_hold:+,.0f}  spread=${spread:+,.0f}")
    lines.append(f"- Top quartile par **Sharpe** → holdout : ${top_sh_hold:+,.0f}  vs bottom : ${bot_sh_hold:+,.0f}  spread=${spread_sh:+,.0f}\n")

    lines.append("## VERDICT")
    if len(validated) >= 5 and spread_sh > 0:
        lines.append(f"### **OUI — {len(validated)} traders validés** (consistance + Bonferroni + rang Sharpe prédit OOS). → Phase 2.\n")
    elif len(validated) >= 3:
        lines.append(f"### **OUI faible — {len(validated)} traders validés** par les 3 tests. Petit échantillon, à confirmer en P2 paper avant scaling.\n")
    elif len(consistent) >= 5:
        lines.append(f"### **TIÈDE — {len(consistent)} traders consistants train+holdout mais aucun ne passe le Bonferroni strict.** Sample insuffisant pour conclure.\n")
    else:
        lines.append(f"### **NON — pas d'edge copiable robuste sous {HOLD_MAX_MINUTES//60}h détecté.**")
        lines.append(f"consistance : {len(consistent)}, Bonferroni : {len(validated)}. Doctrine : pas de live sans preuve.\n")

    if validated:
        lines.append("## Traders VALIDÉS (passent les 3 tests)")
        lines.append("| wallet | n | profile | hold_med (min) | train | holdout | Sharpe | max_DD | t_stat |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for m in validated[:30]:
            lines.append(
                f"| {m['addr'][:14]} | {m['n']} | {m['profile']} | "
                f"{m['hold_med']:.0f} | ${m['train_pnl']:+,.0f} | "
                f"${m['hold_pnl']:+,.0f} | {m['sharpe']:.2f} | "
                f"${m['max_dd']:,.0f} | {m['t_stat']:.2f} |")

    if consistent and consistent != validated:
        lines.append("\n## Traders consistants (sub-window OK + train+/holdout+) non-Bonferroni")
        lines.append("| wallet | n | profile | hold_med | train | holdout | Sharpe | t_stat |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for m in consistent[:20]:
            if m in validated:
                continue
            ts = f"{m['t_stat']:.2f}" if m["t_stat"] is not None else "—"
            lines.append(
                f"| {m['addr'][:14]} | {m['n']} | {m['profile']} | "
                f"{m['hold_med']:.0f} | ${m['train_pnl']:+,.0f} | "
                f"${m['hold_pnl']:+,.0f} | {m['sharpe']:.2f} | {ts} |")

    from collections import Counter
    pc = Counter(m["profile"] for m in metrics)
    lines.append(f"\n## Distribution profile (population N={N})")
    for p, c in pc.most_common():
        lines.append(f"- {p} : {c}")

    txt = "\n".join(lines)
    VERDICT_MD.write_text(txt)
    log(f"\n=> verdict -> {VERDICT_MD}\n")
    log(txt)


if __name__ == "__main__":
    main()
