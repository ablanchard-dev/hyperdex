#!/usr/bin/env python3
"""TEST DÉCISIF FEES→prix — proxy GRATUIT du PEAD (revenu réel, ≠ TVL capital parqué).

DeFiLlama dailyFees par protocole (gratuit) + prix HL daily. Hypothèse : les protocoles
dont les REVENUS (fees) accélèrent voient leur token surperformer — l'équivalent crypto
du post-earnings drift, mais GRATUIT (vs PEAD actions payant). Économiquement DISTINCT de
la TVL (revenu généré vs capital inerte). Réutilise tvl_signal.tvl_xs_backtest (générique,
testé). Grille pré-enreg lookback {7,14,30} × top_frac {0.3,0.4} = 6 specs, n_trials=6.
Un seul run, CRITIC durci. Survit → 1er lead fondamental ; sinon réfutation propre.
"""
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
import tvl_signal as tv
import universe as U
from verdict import evaluate_edge
from app.services.hl_api.info_client import InfoClient

import httpx

DAY = 86400
LOOKBACKS = [7, 14, 30]
TOP_FRACS = [0.3, 0.4]


def daily_fees(symbol_to_slug):
    out = {}
    for sym, slug in symbol_to_slug.items():
        try:
            r = httpx.get(f"https://api.llama.fi/summary/fees/{slug}?dataType=dailyFees",
                          timeout=20)
            if r.status_code != 200:
                continue
            ch = r.json().get("totalDataChart", [])
            out[sym] = {int(d) // DAY: float(v) for d, v in ch if v}
        except Exception:
            pass
    return out


def main():
    # mapping via /protocols (le seul qui matche l'univers HL ; overview/fees a d'autres symbols)
    protos = httpx.get("https://api.llama.fi/protocols", timeout=20).json()
    sym_slug = {}
    for p in sorted(protos, key=lambda x: -(x.get("tvl") or 0)):
        s = (p.get("symbol") or "").upper()
        if s and s != "-" and s not in sym_slug:
            sym_slug[s] = p["slug"]

    c = InfoClient(min_interval_s=1.0)
    meta, ctxs = c.meta_and_asset_ctxs()
    hl = U.tradeable_names(U.build_universe(meta, ctxs))
    tokens = [s for s in hl if s in sym_slug]
    print(f"[{time.strftime('%H:%M:%S')}] {len(tokens)} tokens HL avec fees : {tokens}", flush=True)

    fees = daily_fees({s: sym_slug[s] for s in tokens})
    end = int(time.time() * 1000)
    start = end - 365 * DAY * 1000
    px = {}
    for s in tokens:
        try:
            k = c.candles(s, "1d", start, end)
            if len(k) >= 60:
                px[s] = {int(x["t"]) // (DAY * 1000): float(x["c"]) for x in k}
        except Exception:
            pass
    tokens = [s for s in tokens if s in fees and s in px and len(fees[s]) >= 60]
    print(f"   {len(tokens)} tokens avec fees+prix daily ≥60j : {tokens}", flush=True)
    if len(tokens) < 5:
        print("   univers trop mince.", flush=True)
        return
    fees = {s: fees[s] for s in tokens}
    px = {s: px[s] for s in tokens}

    common = sorted(set.intersection(*[set(px[s]) for s in tokens]))
    bench = [statistics.mean((px[s][common[i]] - px[s][common[i - 1]]) / px[s][common[i - 1]]
                             for s in tokens if px[s][common[i - 1]])
             for i in range(1, len(common))]

    specs = [(lb, tf) for lb in LOOKBACKS for tf in TOP_FRACS]
    n_trials = len(specs)
    train_sh = []
    for lb, tf in specs:
        r = tv.tvl_xs_backtest(fees, px, lookback=lb, top_frac=tf,
                               taker_bps=U.TAKER_BPS, slippage_bps=5.0, exec_lag=1)
        train_sh.append(statistics.mean(r) / statistics.pstdev(r)
                        if len(r) > 1 and statistics.pstdev(r) > 0 else 0.0)
    sr_var = max(statistics.pvariance(train_sh) if len(train_sh) > 1 else 0.05, 1e-4)

    print(f"\n=== FEES→PRIX cross-sectional (proxy PEAD gratuit) — {n_trials} specs durci ===",
          flush=True)
    print(f"   {'lb':>4} {'tf':>4} {'sharpe':>8} {'dsr':>6} {'beta':>6} {'t_alpha':>8} {'reason':>14} pass",
          flush=True)
    surv, best = 0, None
    for lb, tf in specs:
        te = tv.tvl_xs_backtest(fees, px, lookback=lb, top_frac=tf,
                                taker_bps=U.TAKER_BPS, slippage_bps=5.0, exec_lag=1)
        m = min(len(te), len(bench))
        if m < 10:
            print(f"   {lb:>4} {tf:>4} (trop court)", flush=True)
            continue
        v = evaluate_edge(te[:m], bench[:m], n_trials=n_trials, sr_variance=sr_var)
        g, bn = v["gates"], v["gates"]["beta_neutral"]
        surv += int(v["pass"])
        reason = ",".join(v["reasons"]) or "OK"
        if best is None or g["sharpe"] > best[0]:
            best = (g["sharpe"], bn["t_alpha"], lb, tf)
        print(f"   {lb:>4} {tf:>4} {g['sharpe']:>+8.3f} {g['dsr']:>6.2f} {bn['beta']:>+6.2f} "
              f"{bn['t_alpha']:>+8.2f} {reason:>14} {v['pass']}", flush=True)
    if best:
        print(f"\nSURVIVANTS : {surv}/{n_trials} | best Sharpe {best[0]:+.3f} "
              f"(t_alpha {best[1]:+.2f}, lb={best[2]} tf={best[3]})", flush=True)
    print("verdict : " + ("🟢 LEAD FONDAMENTAL (fees)" if surv else
                          "réfuté proprement (grille pré-enreg, durci, n_trials honnête)"),
          flush=True)


if __name__ == "__main__":
    main()
