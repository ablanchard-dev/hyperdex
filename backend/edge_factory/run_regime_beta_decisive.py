#!/usr/bin/env python3
"""TEST DÉCISIF rotation de beta régime-conditionnel — grille pré-enreg, durci, 1 fois.

Risk-on BTC → long haut-beta / short bas-beta ; risk-off → l'inverse. Tout trailing
(passé only). Grille pré-enregistrée : beta_window {48,72} × regime_window {12,24,48}
= 6 specs, n_trials=6 honnête. Univers live HL, coûts réels, exec_lag=1. Le CRITIC
(beta-neutral t≥3) dira si c'est de l'alpha ou du beta timé (probable beta_deguise).
"""
import statistics
import sys
import time

sys.path.insert(0, "/home/dexter/hyperdex/backend")
sys.path.insert(0, "/home/dexter/hyperdex/backend/edge_factory")
import regime_beta as rb
import universe as U
from adapter import Bar, returns_from_bars
from verdict import evaluate_edge
from app.services.hl_api.info_client import InfoClient

HOUR = 3600_000
DAYS = 60
BETA_WINS = [48, 72]
REGIME_WINS = [12, 24, 48]


def main():
    c = InfoClient(min_interval_s=1.0)
    meta, ctxs = c.meta_and_asset_ctxs()
    coins = U.tradeable_names(U.build_universe(meta, ctxs))
    end = int(time.time() * 1000)
    start = end - DAYS * 24 * HOUR
    print(f"[{time.strftime('%H:%M:%S')}] {len(coins)} perps, fetch candles 1h...", flush=True)
    raw = {}
    for s in coins:
        try:
            k = c.candles(s, "1h", start, end)
            if len(k) >= 1000:
                raw[s] = {int(x["t"]) // HOUR: float(x["c"]) for x in k}
        except Exception:
            pass
    common = sorted(set.intersection(*[set(m) for m in raw.values()]))
    bars = {s: [Bar(ts=h * HOUR, close=raw[s][h]) for h in common] for s in raw}
    btc = bars.get("BTC") or next(iter(bars.values()))
    alts = {s: b for s, b in bars.items() if s != "BTC"}
    print(f"   {len(alts)} alts + BTC | {len(common)} heures (~{len(common)/24:.0f}j)", flush=True)

    specs = [(bw, rw) for bw in BETA_WINS for rw in REGIME_WINS]
    n_trials = len(specs)
    n = len(common)
    cut = int(n * 0.7)
    bench_te = returns_from_bars(btc[cut:n])

    def run(bw, rw, lo, hi):
        sb = {s: b[lo:hi] for s, b in alts.items()}
        return rb.regime_beta_returns(sb, btc[lo:hi], beta_window=bw, regime_window=rw,
                                      top_frac=0.3, taker_bps=U.TAKER_BPS,
                                      slippage_bps=4.0, exec_lag=1)

    train_sh = []
    for bw, rw in specs:
        r = run(bw, rw, 0, cut)
        train_sh.append(statistics.mean(r) / statistics.pstdev(r)
                        if len(r) > 1 and statistics.pstdev(r) > 0 else 0.0)
    sr_var = max(statistics.pvariance(train_sh) if len(train_sh) > 1 else 0.05, 1e-4)

    print(f"\n=== ROTATION BETA RÉGIME-CONDITIONNEL — {n_trials} specs (durci) ===", flush=True)
    print(f"   {'b_win':>5} {'r_win':>5} {'sharpe':>8} {'dsr':>6} {'beta':>6} {'t_alpha':>8} {'reason':>16} pass",
          flush=True)
    surv, best = 0, None
    for bw, rw in specs:
        te = run(bw, rw, cut, n)
        m = min(len(te), len(bench_te))
        v = evaluate_edge(te[:m], bench_te[:m], n_trials=n_trials, sr_variance=sr_var)
        g, bn = v["gates"], v["gates"]["beta_neutral"]
        surv += int(v["pass"])
        reason = ",".join(v["reasons"]) or "OK"
        if best is None or g["sharpe"] > best[0]:
            best = (g["sharpe"], bn["t_alpha"], bn["beta"], bw, rw)
        print(f"   {bw:>5} {rw:>5} {g['sharpe']:>+8.3f} {g['dsr']:>6.2f} {bn['beta']:>+6.2f} "
              f"{bn['t_alpha']:>+8.2f} {reason:>16} {v['pass']}", flush=True)
    print(f"\nSURVIVANTS : {surv}/{n_trials} | best Sharpe {best[0]:+.3f} "
          f"(t_alpha {best[1]:+.2f}, beta {best[2]:+.2f}, b_win={best[3]} r_win={best[4]})",
          flush=True)
    print("verdict : " + ("🟢 LEAD" if surv else
                          "réfuté proprement (grille pré-enreg, durci, n_trials honnête)"),
          flush=True)


if __name__ == "__main__":
    main()
