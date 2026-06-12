#!/usr/bin/env python3
"""TEST DÉCISIF TVL→prix — fondamental on-chain cross-sectional, grille pré-enreg, durci.

DeFiLlama TVL daily (gratuit, par protocole) + prix HL daily (candles 1d). Hypothèse :
long forte-croissance-TVL / short décroissance (capital afflue → prix suit). Grille
pré-enregistrée lookback {7,14,30} × top_frac {0.3,0.4} = 6 specs, n_trials=6 honnête.
Un seul run. Survit au CRITIC durci → 1er lead fondamental ; sinon réfutation propre.
"""
import statistics
import sys
import time

sys.path.insert(0, "/home/dexter/hyperdex/backend")
sys.path.insert(0, "/home/dexter/hyperdex/backend/edge_factory")
import tvl_signal as tv
import universe as U
from verdict import evaluate_edge
from app.services.hl_api.info_client import InfoClient

import httpx

DAY = 86400
LOOKBACKS = [7, 14, 30]
TOP_FRACS = [0.3, 0.4]


def daily_tvl(symbol_to_slug):
    """TVL daily par token via DeFiLlama /protocol/{slug} (gratuit)."""
    out = {}
    for sym, slug in symbol_to_slug.items():
        try:
            r = httpx.get(f"https://api.llama.fi/protocol/{slug}", timeout=20)
            if r.status_code != 200:
                continue
            tvl = r.json().get("tvl", [])
            out[sym] = {int(p["date"]) // DAY: float(p["totalLiquidityUSD"])
                        for p in tvl if p.get("totalLiquidityUSD")}
        except Exception:
            pass
    return out


def main():
    # 1) protocoles DeFiLlama (symbol → slug du plus gros protocole de ce symbol)
    protos = httpx.get("https://api.llama.fi/protocols", timeout=20).json()
    sym_slug = {}
    for p in sorted(protos, key=lambda x: -(x.get("tvl") or 0)):
        s = (p.get("symbol") or "").upper()
        if s and s != "-" and s not in sym_slug:
            sym_slug[s] = p["slug"]

    # 2) univers HL ∩ DeFiLlama
    c = InfoClient(min_interval_s=1.0)
    meta, ctxs = c.meta_and_asset_ctxs()
    hl = U.tradeable_names(U.build_universe(meta, ctxs))
    tokens = [s for s in hl if s in sym_slug]
    print(f"[{time.strftime('%H:%M:%S')}] {len(tokens)} tokens HL∩DeFiLlama : {tokens}", flush=True)

    # 3) TVL daily + prix daily HL
    tvl = daily_tvl({s: sym_slug[s] for s in tokens})
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
    tokens = [s for s in tokens if s in tvl and s in px and len(tvl[s]) >= 60]
    print(f"   {len(tokens)} tokens avec TVL+prix daily ≥60j", flush=True)
    if len(tokens) < 5:
        print("   univers trop mince pour cross-sectional.", flush=True)
        return
    tvl = {s: tvl[s] for s in tokens}
    px = {s: px[s] for s in tokens}

    # bench = moyenne equal-weight des tokens (le "marché" de cet univers)
    common = sorted(set.intersection(*[set(px[s]) for s in tokens]))
    bench_series = [statistics.mean((px[s][common[i]] - px[s][common[i - 1]]) / px[s][common[i - 1]]
                                    for s in tokens if px[s][common[i - 1]])
                    for i in range(1, len(common))]

    specs = [(lb, tf) for lb in LOOKBACKS for tf in TOP_FRACS]
    n_trials = len(specs)
    train_sh = []
    for lb, tf in specs:
        r = tv.tvl_xs_backtest(tvl, px, lookback=lb, top_frac=tf,
                               taker_bps=U.TAKER_BPS, slippage_bps=5.0, exec_lag=1)
        train_sh.append(statistics.mean(r) / statistics.pstdev(r)
                        if len(r) > 1 and statistics.pstdev(r) > 0 else 0.0)
    sr_var = max(statistics.pvariance(train_sh) if len(train_sh) > 1 else 0.05, 1e-4)

    print(f"\n=== TVL→PRIX cross-sectional — {n_trials} specs (durci, n_trials={n_trials}) ===",
          flush=True)
    print(f"   {'lb':>4} {'tf':>4} {'sharpe':>8} {'dsr':>6} {'beta':>6} {'t_alpha':>8} {'reason':>14} pass",
          flush=True)
    surv, best = 0, None
    for lb, tf in specs:
        te = tv.tvl_xs_backtest(tvl, px, lookback=lb, top_frac=tf,
                                taker_bps=U.TAKER_BPS, slippage_bps=5.0, exec_lag=1)
        m = min(len(te), len(bench_series))
        if m < 10:
            print(f"   {lb:>4} {tf:>4} (trop court)", flush=True)
            continue
        v = evaluate_edge(te[:m], bench_series[:m], n_trials=n_trials, sr_variance=sr_var)
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
    print("verdict : " + ("🟢 LEAD FONDAMENTAL" if surv else
                          "réfuté proprement (grille pré-enreg, durci, n_trials honnête)"),
          flush=True)


if __name__ == "__main__":
    main()
