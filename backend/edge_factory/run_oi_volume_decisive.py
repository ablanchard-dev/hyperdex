#!/usr/bin/env python3
"""TEST DÉCISIF OI/VOLUME ratio — grille PRÉ-ENREGISTRÉE, une fois, CRITIC durci.

Angle : OI gonfle sans volume proportionnel = accumulation passive → suivre l'OI
(momentum de positionnement, distinct de la divergence OI-prix contrarian déjà réfutée).
Data : candles HL (close+volume 'v') + OI HL natif (Coinalyze .H). Grille fixée AVANT :
seuils {1.5,2,2.5,3} × fenêtres {24,48,72} = 12 specs, n_trials=12 honnête. Per-coin
agrégé (moyenne equal-weight des returns par coin) jugé vs BTC. Un seul run.
"""
import os
import statistics
import sys
import time

sys.path.insert(0, "/home/dexter/hyperdex/backend")
sys.path.insert(0, "/home/dexter/hyperdex/backend/edge_factory")
import coinalyze as cz
import oi_volume_signal as ov
import universe as U
from adapter import Bar, returns_from_bars
from verdict import evaluate_edge
from app.services.hl_api.info_client import InfoClient

HOUR = 3600_000
DAYS = 60
THRESHOLDS = [1.5, 2.0, 2.5, 3.0]
WINDOWS = [24, 48, 72]


def main():
    c = InfoClient(min_interval_s=1.0)
    meta, ctxs = c.meta_and_asset_ctxs()
    coins = U.tradeable_names(U.build_universe(meta, ctxs))
    end = int(time.time() * 1000)
    start = end - DAYS * 24 * HOUR
    print(f"[{time.strftime('%H:%M:%S')}] {len(coins)} perps, fetch candles(+vol)+OI...",
          flush=True)
    bars, vol = {}, {}
    for s in coins:
        try:
            k = c.candles(s, "1h", start, end)
            if len(k) >= 1000:
                hrs = [int(x["t"]) // HOUR for x in k]
                cmap = {h: float(x["c"]) for h, x in zip(hrs, k)}
                vmap = {h: float(x["v"]) for h, x in zip(hrs, k)}
                bars[s] = (hrs, cmap, vmap)
        except Exception:
            pass
    common = sorted(set.intersection(*[set(v[0]) for v in bars.values()]))
    closes = {s: [bars[s][1][h] for h in common] for s in bars}
    vols = {s: [bars[s][2][h] for h in common] for s in bars}
    barobj = {s: [Bar(ts=h * HOUR, close=closes[s][i]) for i, h in enumerate(common)]
              for s in bars}

    key = open(os.path.expanduser("~/.coinalyze_key")).read().strip()
    to, frm = int(time.time()), int(time.time()) - DAYS * 24 * 3600
    oi = {}
    for s in barobj:
        try:
            series = cz.parse_oi_history(
                cz.fetch_oi_history(cz.hl_symbol(s), "1hour", frm, to, api_key=key),
                [b.ts for b in barobj[s]])
            if series and any(v > 0 for v in series):
                oi[s] = series
        except Exception:
            pass
    syms = [s for s in oi if s in barobj]
    btc = barobj.get("BTC") or barobj[syms[0]]
    print(f"   {len(syms)} coins (candles+vol+OI) | {len(common)} heures", flush=True)
    if len(syms) < 6:
        print("   univers trop mince.", flush=True)
        return

    specs = [(thr, win) for thr in THRESHOLDS for win in WINDOWS]
    n_trials = len(specs)
    n = len(common)
    cut = int(n * 0.7)
    bench_te = returns_from_bars(btc[cut:n])

    def portfolio(thr, win, lo, hi):
        series = []
        for s in syms:
            r = ov.oi_volume_returns(barobj[s][lo:hi], oi[s][lo:hi], vols[s][lo:hi],
                                     window=win, threshold=thr, taker_bps=U.TAKER_BPS,
                                     slippage_bps=4.0, exec_lag=1)
            series.append(r)
        m = min(len(x) for x in series)
        return [statistics.mean(series[j][t] for j in range(len(series)))
                for t in range(m)]

    train_sh = []
    for thr, win in specs:
        r = portfolio(thr, win, 0, cut)
        train_sh.append(statistics.mean(r) / statistics.pstdev(r)
                        if len(r) > 1 and statistics.pstdev(r) > 0 else 0.0)
    sr_var = max(statistics.pvariance(train_sh) if len(train_sh) > 1 else 0.05, 1e-4)

    print(f"\n=== OI/VOLUME ratio (momentum positionnement) — {n_trials} specs, durci ===",
          flush=True)
    print(f"   {'thr':>4} {'win':>4} {'sharpe':>8} {'dsr':>6} {'beta':>6} {'t_alpha':>8} pass",
          flush=True)
    surv, best = 0, None
    for thr, win in specs:
        te = portfolio(thr, win, cut, n)
        m = min(len(te), len(bench_te))
        v = evaluate_edge(te[:m], bench_te[:m], n_trials=n_trials, sr_variance=sr_var)
        g, bn = v["gates"], v["gates"]["beta_neutral"]
        surv += int(v["pass"])
        if best is None or g["sharpe"] > best[0]:
            best = (g["sharpe"], bn["t_alpha"], thr, win)
        print(f"   {thr:>4} {win:>4} {g['sharpe']:>+8.3f} {g['dsr']:>6.2f} "
              f"{bn['beta']:>+6.2f} {bn['t_alpha']:>+8.2f} {v['pass']}", flush=True)
    print(f"\nSURVIVANTS : {surv}/{n_trials} | best Sharpe {best[0]:+.3f} "
          f"(t_alpha {best[1]:+.2f}, thr={best[2]} win={best[3]})", flush=True)
    print("verdict : " + ("🟢 LEAD" if surv else
                          "réfuté proprement (grille pré-enreg, durci, n_trials honnête)"),
          flush=True)


if __name__ == "__main__":
    main()
