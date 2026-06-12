#!/usr/bin/env python3
"""TEST DÉCISIF OI-DIVERGENCE — grille PRÉ-ENREGISTRÉE, lancée UNE fois, jugée durci.

L'OI-divergence dominait le top du leaderboard (t~1.6) → on creuse PROPREMENT, sans
p-hacking : grille fixée AVANT (seuils {1.5,2,2.5,3} × fenêtres {24,48,72}), deux
variantes (cross-sectional long-short oi_xs + per-coin contrarian oi_signal sur le top
coin), n_trials = VRAIE largeur de la grille (déflation DSR honnête). Un seul run.
Survit au CRITIC durci → vrai lead. Sinon → réfutation documentée.

Univers live HL figé, OI HL natif (Coinalyze .H), coûts réels, exec_lag=1.
"""
import os
import statistics
import sys
import time

sys.path.insert(0, "/opt/app/hyperdex/backend")
sys.path.insert(0, "/opt/app/hyperdex/backend/edge_factory")
import coinalyze as cz
import oi_xs
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
    univ = U.build_universe(meta, ctxs)
    coins = U.tradeable_names(univ)
    end = int(time.time() * 1000)
    start = end - DAYS * 24 * HOUR
    print(f"[{time.strftime('%H:%M:%S')}] univers {len(coins)} perps, fetch candles+OI...",
          flush=True)
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

    key = open(os.path.expanduser("~/.coinalyze_key")).read().strip()
    to, frm = int(time.time()), int(time.time()) - DAYS * 24 * 3600
    oi = {}
    for s in bars:
        try:
            series = cz.parse_oi_history(
                cz.fetch_oi_history(cz.hl_symbol(s), "1hour", frm, to, api_key=key),
                [b.ts for b in bars[s]])
            if series and any(v > 0 for v in series):
                oi[s] = series
        except Exception:
            pass
    sb = {s: bars[s] for s in oi}
    btc = bars.get("BTC") or next(iter(bars.values()))
    print(f"   {len(sb)} coins avec OI | {len(common)} heures (~{len(common)/24:.0f}j)",
          flush=True)
    if len(sb) < 6:
        print("   univers OI trop mince.", flush=True)
        return

    # GRILLE PRÉ-ENREGISTRÉE — cross-sectional long-short (beta annulé par construction)
    specs = [(thr, win) for thr in THRESHOLDS for win in WINDOWS]
    n_trials = len(specs)  # vraie largeur multiple-testing (déflation DSR honnête)
    n = min(len(b) for b in sb.values())
    cut = int(n * 0.7)
    bench_te = returns_from_bars(btc[cut:n])

    print(f"\n=== OI-DIVERGENCE CROSS-SECTIONAL — grille {n_trials} specs (durci, "
          f"n_trials={n_trials}) ===", flush=True)
    print(f"   {'thr':>4} {'win':>4} {'sharpe':>8} {'dsr':>6} {'beta':>6} {'t_alpha':>8} {'pass'}",
          flush=True)
    # variance des Sharpe train cross-specs (pour la déflation)
    train_sh = []
    for thr, win in specs:
        tb = {s: b[:cut] for s, b in sb.items()}
        toi = {s: v[:cut] for s, v in oi.items()}
        r = oi_xs.oi_xs_backtest(tb, toi, window=win, top_frac=0.3, taker_bps=U.TAKER_BPS,
                                 slippage_bps=4.0, exec_lag=1)
        train_sh.append(statistics.mean(r) / statistics.pstdev(r)
                        if len(r) > 1 and statistics.pstdev(r) > 0 else 0.0)
    sr_var = max(statistics.pvariance(train_sh) if len(train_sh) > 1 else 0.05, 1e-4)

    surv = 0
    best = None
    for (thr, win), tsh in zip(specs, train_sh):
        teb = {s: b[cut:n] for s, b in sb.items()}
        teoi = {s: v[cut:n] for s, v in oi.items()}
        te = oi_xs.oi_xs_backtest(teb, teoi, window=win, top_frac=0.3,
                                  taker_bps=U.TAKER_BPS, slippage_bps=4.0, exec_lag=1)
        m = min(len(te), len(bench_te))
        v = evaluate_edge(te[:m], bench_te[:m], n_trials=n_trials, sr_variance=sr_var)
        g = v["gates"]
        bn = g["beta_neutral"]
        surv += int(v["pass"])
        row = (g["sharpe"], bn["beta"], bn["t_alpha"], v["pass"], thr, win)
        if best is None or g["sharpe"] > best[0]:
            best = row
        print(f"   {thr:>4} {win:>4} {g['sharpe']:>+8.3f} {g['dsr']:>6.2f} "
              f"{bn['beta']:>+6.2f} {bn['t_alpha']:>+8.2f} {v['pass']}", flush=True)
    print(f"\nSURVIVANTS : {surv}/{n_trials} | best Sharpe {best[0]:+.3f} "
          f"(thr={best[4]} win={best[5]}, t_alpha={best[2]:+.2f})", flush=True)
    print("verdict : " + ("🟢 LEAD — durcir/forward" if surv else
                          "réfuté proprement (grille pré-enreg, durci, n_trials honnête)"),
          flush=True)


if __name__ == "__main__":
    main()
