#!/usr/bin/env python3
"""TEST DÉCISIF saisonnalité horaire — profil train-only, jugé CRITIC durci, UNE fois.

Angle distinct (recherche : BTC 21-23h UTC +, 3-4h − ; Monday Asia Open). Profil horaire
appris sur les 70% train, appliqué aux 30% test (anti-look-ahead). Grille pré-enregistrée
min_abs {0, 0.0005, 0.001} = 3 specs × per-coin agrégé equal-weight. n_trials honnête.
Univers live HL, coûts réels, exec_lag=1. Survit → lead ; sinon réfuté propre.
"""
import statistics
import sys
import time

sys.path.insert(0, "/opt/app/hyperdex/backend")
sys.path.insert(0, "/opt/app/hyperdex/backend/edge_factory")
import seasonality as sz
import universe as U
from adapter import Bar, returns_from_bars
from verdict import evaluate_edge
from app.services.hl_api.info_client import InfoClient

HOUR = 3600_000
DAYS = 60
MIN_ABS = [0.0, 0.0005, 0.001]


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
    print(f"   {len(bars)} coins | {len(common)} heures (~{len(common)/24:.0f}j)", flush=True)

    n_trials = len(MIN_ABS)
    n = len(common)
    cut = int(n * 0.7)
    bench_te = returns_from_bars(btc[cut:n])

    def portfolio(min_abs):
        series = [sz.seasonality_returns(bars[s], train_frac=0.7, taker_bps=U.TAKER_BPS,
                                         slippage_bps=4.0, min_abs=min_abs) for s in bars]
        series = [r for r in series if r]
        if not series:
            return []
        m = min(len(x) for x in series)
        return [statistics.mean(series[j][t] for j in range(len(series))) for t in range(m)]

    train_sh = []
    for ma in MIN_ABS:
        # train profil = 70% ; pour sr_var on mesure le Sharpe sur le test (proxy)
        r = portfolio(ma)
        train_sh.append(statistics.mean(r) / statistics.pstdev(r)
                        if len(r) > 1 and statistics.pstdev(r) > 0 else 0.0)
    sr_var = max(statistics.pvariance(train_sh) if len(train_sh) > 1 else 0.05, 1e-4)

    print(f"\n=== SAISONNALITÉ HORAIRE — {n_trials} specs (profil train-only, durci) ===",
          flush=True)
    print(f"   {'min_abs':>8} {'sharpe':>8} {'dsr':>6} {'beta':>6} {'t_alpha':>8} {'reason':>14} pass",
          flush=True)
    surv, best = 0, None
    for ma in MIN_ABS:
        te = portfolio(ma)
        if not te:
            print(f"   {ma:>8} (aucun trade)", flush=True)
            continue
        m = min(len(te), len(bench_te))
        v = evaluate_edge(te[:m], bench_te[:m], n_trials=n_trials, sr_variance=sr_var)
        g, bn = v["gates"], v["gates"]["beta_neutral"]
        surv += int(v["pass"])
        reason = ",".join(v["reasons"]) or "OK"
        if best is None or g["sharpe"] > best[0]:
            best = (g["sharpe"], bn["t_alpha"], ma)
        print(f"   {ma:>8} {g['sharpe']:>+8.3f} {g['dsr']:>6.2f} {bn['beta']:>+6.2f} "
              f"{bn['t_alpha']:>+8.2f} {reason:>14} {v['pass']}", flush=True)
    if best:
        print(f"\nSURVIVANTS : {surv}/{n_trials} | best Sharpe {best[0]:+.3f} "
              f"(t_alpha {best[1]:+.2f}, min_abs={best[2]})", flush=True)
    print("verdict : " + ("🟢 LEAD" if surv else
                          "réfuté proprement (profil train-only, durci, n_trials honnête)"),
          flush=True)


if __name__ == "__main__":
    main()
