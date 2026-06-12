#!/usr/bin/env python3
"""Hunt: SHORT-TERM REVERSAL cross-sectional INTRADAY sur HL perps (1h candles).

Anomalie la + documentée (reversal court-terme), market-neutral PAR CONSTRUCTION
(long-short dollar-neutral → beta≈0 → échappe au piège beta qui a tué tout le reste).
Data GRATUITE (candles 1h). Le vrai test = SURVIT-IL AUX COÛTS (turnover horaire
énorme = cas d'école d'anomalie qui meurt après frais). Coûts réels dès le départ.

PRÉ-ENREGISTRÉ (lancé UNE fois, pas de p-hacking) : univers top-50 HL par volume
(figé), 60j × 1h, grille lookback {1,2,3,6}h × top_frac {0.2,0.3} = 8 specs,
taker 4.5 + slippage 5bps, exec_lag=1. Jugé par CRITIC (DSR déflaté ×8 + beta + PBO).
"""
import sys
import time

sys.path.insert(0, "/home/dexter/hyperdex/backend")
sys.path.insert(0, "/home/dexter/hyperdex/backend/edge_factory")
from adapter import Bar
from cross_sectional import cross_sectional_pbo, judge_cross_sectional, survivors
from app.services.hl_api.info_client import InfoClient

HOUR = 3600_000
N_UNIV = 50
DAYS = 60
MIN_BARS = 1000
TAKER_BPS = 4.5
SLIPPAGE_BPS = 5.0


def main():
    c = InfoClient(min_interval_s=1.0)
    meta, ctxs = c.meta_and_asset_ctxs()
    names = [a["name"] for a in meta["universe"]]
    vol = {names[i]: float(ctxs[i].get("dayNtlVlm", 0) or 0) for i in range(len(names))}
    coins = sorted(names, key=lambda s: -vol[s])[:N_UNIV]  # top volume = liquide/tradable
    end = int(time.time() * 1000)
    start = end - DAYS * 24 * HOUR
    print(f"[{time.strftime('%H:%M:%S')}] fetch {len(coins)} perps × {DAYS}j candles 1h...",
          flush=True)
    raw = {}
    for s in coins:
        try:
            k = c.candles(s, "1h", start, end)
            if len(k) >= MIN_BARS:
                raw[s] = {int(x["t"]) // HOUR: float(x["c"]) for x in k}
        except Exception:
            pass
    common = sorted(set.intersection(*[set(m) for m in raw.values()]))
    bars = {s: [Bar(ts=h * HOUR, close=raw[s][h]) for h in common] for s in raw}
    btc = bars.get("BTC") or next(iter(bars.values()))
    print(f"   {len(bars)} coins | {len(common)} heures (~{len(common)/24:.0f}j)", flush=True)

    # grille pré-enregistrée (n_trials=8 → DSR déflaté)
    specs = [{"signal": {"type": "xs_reversion",
                         "params": {"lookback": lb, "top_frac": tf}}}
             for lb in (1, 2, 3, 6) for tf in (0.2, 0.3)]

    res = judge_cross_sectional(bars, btc, specs, TAKER_BPS, train_frac=0.7,
                                slippage_bps=SLIPPAGE_BPS, borrow_bps_annual=0.0,
                                exec_lag=1)
    pbo = cross_sectional_pbo(bars, specs, TAKER_BPS, train_frac=0.7,
                              slippage_bps=SLIPPAGE_BPS, borrow_bps_annual=0.0,
                              exec_lag=1)

    print(f"\n=== XS REVERSAL INTRADAY HL — coûts taker {TAKER_BPS}+slip {SLIPPAGE_BPS}bps "
          f"({len(specs)} specs, PBO={pbo:.3f}) ===", flush=True)
    for r in res:
        p = r["hypothesis"]["signal"]["params"]
        g = r["gates"]
        print(f"  lb={p['lookback']}h tf={p['top_frac']} pass={r['pass']} "
              f"train_sh={r['train_sharpe']:+.3f} dsr={g['dsr']:.3f} "
              f"beta={g['beta_neutral']['beta']:+.2f} t_alpha={g['beta_neutral']['t_alpha']:+.2f} "
              f"{r['reasons']}", flush=True)
    print(f"SURVIVANTS : {len(survivors(res))}/{len(specs)} | PBO={pbo:.3f}", flush=True)

    # sensibilité coûts (HONNÊTETÉ, pas pour cherry-pick : verdict = la ligne pré-enreg ci-dessus)
    print("\n=== sensibilité coûts (gross→net, best train_sharpe) ===", flush=True)
    for slip in (0.0, 5.0, 10.0):
        rr = judge_cross_sectional(bars, btc, specs, TAKER_BPS, train_frac=0.7,
                                   slippage_bps=slip, borrow_bps_annual=0.0, exec_lag=1)
        best = max(rr, key=lambda x: x["train_sharpe"])
        print(f"  slip={slip:>4}bps | best train_sh={best['train_sharpe']:+.3f} "
              f"pass={best['pass']}", flush=True)


if __name__ == "__main__":
    main()
