"""HyperDex Phase 1 — Discovery + Validation = LE GATE.

Pipeline :
  1. Leaderboard (36k+ traders) -> filtre univers : accountValue $10k-$10M,
     monthPnL > 0, sorted by monthPnL.
  2. Top N=300 wallets : fetch user_fills_by_time sur 90 jours.
  3. Métriques par trader : closedPnl total, n fills, hold-time depuis dir
     "Open X" / "Close X", profil HFT/intraday/swing/position.
  4. Filtre copiable : n>=50, profil intraday|swing.
  5. Split temporel : train -90j à -30j, holdout -30j à 0.
  6. Per-wallet : train_pnl>0 AND holdout_pnl>0 AND holdout n>=20.
  7. Bonferroni : t-stat holdout > z(alpha=0.05/N).
  8. Test agrégé : top quartile vs bottom quartile en train -> holdout.
  9. Verdict : phase1_verdict.md.

L'edge doit survivre le holdout out-of-sample + correction multiple-testing.
Pas de raccourci.
"""
from __future__ import annotations

import json
import math
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, "/home/dexter/hyperdex/backend")
from app.services.hl_api.info_client import InfoClient  # noqa: E402

# --- config ---
TOP_N = 300
MIN_ACCOUNT_VALUE = 10_000.0
MAX_ACCOUNT_VALUE = 10_000_000.0
WINDOW_DAYS = 90
HOLDOUT_DAYS = 30
MIN_FILLS = 50

OUT_DIR = Path("/home/dexter/hyperdex/backend/data/p1")
OUT_DIR.mkdir(parents=True, exist_ok=True)
FILLS_CACHE = OUT_DIR / "fills_raw.json"
VERDICT_MD = OUT_DIR / "phase1_verdict.md"

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


def compute_hold_times_minutes(fills):
    """Pour chaque transition 'Open X' -> 'Close X' (matching coin/side),
    calcule la durée en minutes. Utilise startPosition pour gérer les fills
    partiels."""
    # Sort by time asc
    sf = sorted(fills, key=lambda f: int(f.get("time", 0)))
    open_starts = {}  # (coin, long_or_short) -> ts (premier Open)
    holds = []
    for f in sf:
        coin = f.get("coin", "")
        d = (f.get("dir") or "").lower()
        ts = int(f.get("time", 0))
        if "open" in d:
            side = "long" if "long" in d else "short"
            key = (coin, side)
            if key not in open_starts:
                open_starts[key] = ts
        elif "close" in d:
            side = "long" if "long" in d else "short"
            key = (coin, side)
            start = open_starts.pop(key, None)
            if start is not None and ts > start:
                holds.append((ts - start) / 60000.0)
    return holds


def classify_profile(hold_med_min: float) -> str:
    if hold_med_min < 5:
        return "HFT"
    if hold_med_min < 240:
        return "intraday"
    if hold_med_min < 4320:  # 3 days
        return "swing"
    return "position"


def main():
    log("=== HyperDex Phase 1 — Discovery + Validation GATE ===")
    log(f"window {WINDOW_DAYS}d  holdout {HOLDOUT_DAYS}d  TOP_N={TOP_N}")
    log(f"now={NOW.isoformat()}")
    ic = InfoClient(mainnet=True)

    # === STEP 1 : leaderboard + filtre univers ===
    log("\n[1] fetch leaderboard...")
    lb = ic.fetch_leaderboard()
    log(f"  reçu {len(lb)} traders")
    universe = [
        r for r in lb
        if MIN_ACCOUNT_VALUE <= float(r.get("accountValue", 0) or 0) <= MAX_ACCOUNT_VALUE
        and monthly_pnl(r) > 0
    ]
    universe.sort(key=monthly_pnl, reverse=True)
    universe = universe[:TOP_N]
    log(f"  univers : top {TOP_N} (${MIN_ACCOUNT_VALUE:,.0f}-${MAX_ACCOUNT_VALUE:,.0f}, monthPnL>0) = {len(universe)} wallets")

    # === STEP 2 : fetch fills (avec cache) ===
    if FILLS_CACHE.exists():
        log(f"\n[2] cache présent : {FILLS_CACHE} — chargé")
        per_wallet = json.loads(FILLS_CACHE.read_text())
        log(f"  {len(per_wallet)} wallets, ~{sum(len(v) for v in per_wallet.values())} fills total")
    else:
        per_wallet = {}
        log(f"\n[2] fetch fills sur {WINDOW_DAYS}j pour {len(universe)} wallets...")
        t0 = time.time()
        for i, row in enumerate(universe):
            addr = row["ethAddress"].lower()
            try:
                fills = ic.user_fills_by_time(addr, WINDOW_START_MS)
                per_wallet[addr] = fills
            except Exception as e:
                log(f"  err {addr[:14]}: {e}")
                per_wallet[addr] = []
            if (i + 1) % 25 == 0:
                el = time.time() - t0
                eta = el / (i + 1) * (len(universe) - i - 1)
                log(f"  ...{i+1}/{len(universe)}  elapsed={el:.0f}s  ETA={eta:.0f}s  total_fills={sum(len(v) for v in per_wallet.values())}")
                FILLS_CACHE.write_text(json.dumps(per_wallet))  # snapshot incrémental
        FILLS_CACHE.write_text(json.dumps(per_wallet))
        log(f"  cached -> {FILLS_CACHE}  total_fills={sum(len(v) for v in per_wallet.values())}")

    # === STEP 3 : métriques par wallet ===
    log("\n[3] calcul métriques...")
    metrics = []
    for addr, fills in per_wallet.items():
        n = len(fills)
        if n < MIN_FILLS:
            continue
        pnls = [float(f.get("closedPnl", 0)) for f in fills]
        train_pnls = [float(f.get("closedPnl", 0)) for f in fills
                      if int(f.get("time", 0)) < HOLDOUT_CUTOFF_MS]
        hold_pnls = [float(f.get("closedPnl", 0)) for f in fills
                     if int(f.get("time", 0)) >= HOLDOUT_CUTOFF_MS]
        holds_min = compute_hold_times_minutes(fills)
        hold_med = statistics.median(holds_min) if holds_min else 0.0
        profile = classify_profile(hold_med)
        # t-stat holdout vs 0
        t_stat = None
        if len(hold_pnls) >= 5:
            m = statistics.mean(hold_pnls)
            s = statistics.stdev(hold_pnls) if len(hold_pnls) > 1 else 0
            if s > 0:
                t_stat = m / (s / math.sqrt(len(hold_pnls)))
        metrics.append(dict(
            addr=addr, n=n,
            total_pnl=sum(pnls),
            train_n=len(train_pnls), train_pnl=sum(train_pnls),
            hold_n=len(hold_pnls), hold_pnl=sum(hold_pnls),
            n_closes=len(holds_min),
            hold_med_min=hold_med, profile=profile,
            t_stat=t_stat,
        ))
    log(f"  metrics : {len(metrics)} wallets (n>={MIN_FILLS})")

    # === STEP 4 : filtre copiable + Bonferroni ===
    copyables = [
        m for m in metrics
        if m["profile"] in ("intraday", "swing")
        and m["train_pnl"] > 0
        and m["hold_pnl"] > 0
        and m["hold_n"] >= 20
    ]
    log(f"\n[4] candidats copiables (profile intra/swing, train+, holdout+, hold_n>=20) : {len(copyables)}")

    N = len(metrics)
    alpha = 0.05 / max(1, N)
    z_crit = statistics.NormalDist().inv_cdf(1 - alpha / 2)
    bonf_survivors = [m for m in copyables
                      if m["t_stat"] is not None and m["t_stat"] > z_crit]
    log(f"  Bonferroni alpha={alpha:.6f}  z_crit={z_crit:.2f}  survivants={len(bonf_survivors)}")

    # === STEP 5 : test agrégé top vs bottom quartile ===
    eligible = [m for m in metrics if m["train_n"] >= 20 and m["hold_n"] >= 5]
    eligible.sort(key=lambda x: -x["train_pnl"])
    q = max(1, len(eligible) // 4)
    top_q = eligible[:q]
    bot_q = eligible[-q:]
    top_q_hold = sum(m["hold_pnl"] for m in top_q)
    bot_q_hold = sum(m["hold_pnl"] for m in bot_q)
    spread = top_q_hold - bot_q_hold
    log(f"\n[5] aggrégé : top quartile train ({q} w) holdout=${top_q_hold:+.0f}  "
        f"bot quartile holdout=${bot_q_hold:+.0f}  spread=${spread:+.0f}")

    # === STEP 6 : verdict ===
    write_verdict(metrics, copyables, bonf_survivors, top_q_hold, bot_q_hold, q,
                  N, z_crit, alpha)


def write_verdict(metrics, copyables, bonf_survivors, top_q_hold, bot_q_hold, q,
                  N, z_crit, alpha):
    bonf_survivors.sort(key=lambda x: -x["hold_pnl"])
    copyables.sort(key=lambda x: -x["hold_pnl"])

    lines = []
    lines.append("# HyperDex Phase 1 — Verdict GATE\n")
    lines.append(f"_Run : {datetime.now(timezone.utc).isoformat()}_\n")
    lines.append(f"## Méthodo")
    lines.append(f"- Univers : top {TOP_N} leaderboard, accountValue ${MIN_ACCOUNT_VALUE:,.0f}-${MAX_ACCOUNT_VALUE:,.0f}, monthPnL>0")
    lines.append(f"- Fenêtre : {WINDOW_DAYS}j  |  Holdout out-of-sample : {HOLDOUT_DAYS}j (train -90 à -30, holdout -30 à 0)")
    lines.append(f"- Filtre copiable : profil intraday/swing, n>={MIN_FILLS}, train_pnl>0, holdout_pnl>0, holdout_n>=20")
    lines.append(f"- Bonferroni correction : alpha=0.05/{N}={alpha:.6f}, z_crit={z_crit:.2f}\n")

    lines.append(f"## Résultats")
    lines.append(f"- Wallets analysés : **{N}**")
    lines.append(f"- Candidats copiables (train+/holdout+/profile) : **{len(copyables)}**")
    lines.append(f"- Survivants Bonferroni (t-stat holdout > z_crit) : **{len(bonf_survivors)}**\n")

    lines.append(f"## Test agrégé — le rang train prédit-il le holdout ?")
    lines.append(f"- Top quartile train ({q} wallets) → holdout PnL cumulé : **${top_q_hold:+,.0f}**")
    lines.append(f"- Bottom quartile train ({q} wallets) → holdout PnL cumulé : **${bot_q_hold:+,.0f}**")
    spread = top_q_hold - bot_q_hold
    lines.append(f"- Spread top-bottom = **${spread:+,.0f}**  ({'positif = prédiction présente' if spread > 0 else 'NUL ou NÉGATIF = pas de prédiction (overfit)'})\n")

    lines.append(f"## VERDICT")
    if len(bonf_survivors) >= 5 and spread > 0:
        lines.append(f"### **OUI — edge copiable détecté.**")
        lines.append(f"{len(bonf_survivors)} traders ont survécu la correction Bonferroni ET le test agrégé est positif. → **Phase 2.**\n")
    elif len(copyables) >= 10 and spread > 0:
        lines.append(f"### **TIÈDE — signal présent mais peu robuste.**")
        lines.append(f"{len(copyables)} candidats train+/holdout+, mais 0 ne passe le Bonferroni strict. Le test agrégé est positif (${spread:+,.0f}). À discuter — risque d'overfit résiduel.\n")
    else:
        lines.append(f"### **NON — pas d'edge copiable détecté.**")
        lines.append(f"Bonferroni : {len(bonf_survivors)} survivants (seuil cible >=5). Spread top-bot : ${spread:+,.0f}. **Projet à enterrer ou repenser** (doctrine : pas de live sans preuve).\n")

    if bonf_survivors:
        lines.append(f"## Top survivants Bonferroni (max 20)")
        lines.append("| wallet | n | profile | hold_med_min | train_pnl | holdout_pnl | t_stat |")
        lines.append("|---|---|---|---|---|---|---|")
        for m in bonf_survivors[:20]:
            lines.append(f"| {m['addr'][:14]} | {m['n']} | {m['profile']} | {m['hold_med_min']:.0f} | ${m['train_pnl']:+,.0f} | ${m['hold_pnl']:+,.0f} | {m['t_stat']:.2f} |")

    if copyables:
        lines.append(f"\n## Top candidats copiables (avant Bonferroni, max 30)")
        lines.append("| wallet | n | profile | hold_med_min | train_pnl | holdout_pnl | t_stat |")
        lines.append("|---|---|---|---|---|---|---|")
        for m in copyables[:30]:
            ts = f"{m['t_stat']:.2f}" if m["t_stat"] is not None else "—"
            lines.append(f"| {m['addr'][:14]} | {m['n']} | {m['profile']} | {m['hold_med_min']:.0f} | ${m['train_pnl']:+,.0f} | ${m['hold_pnl']:+,.0f} | {ts} |")

    # profil global de la population
    lines.append(f"\n## Distribution profile (population)")
    from collections import Counter
    pc = Counter(m["profile"] for m in metrics)
    for p, c in pc.most_common():
        lines.append(f"- {p} : {c}")

    txt = "\n".join(lines)
    VERDICT_MD.write_text(txt)
    log(f"\n=> verdict -> {VERDICT_MD}\n")
    log(txt)


if __name__ == "__main__":
    main()
