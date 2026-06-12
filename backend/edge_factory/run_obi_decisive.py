#!/usr/bin/env python3
"""TEST DÉCISIF OBI→move — 1er angle SOUS-HORAIRE, sur notre dataset propriétaire.

Lit obi_data.jsonl (snapshots carnet HL accumulés), teste si OBI[t] prédit le move de
mid. Grille pré-enreg seuils {0.1,0.2,0.3} × exec_lag {0,1} ; pour chaque, GROSS (cost=0)
ET NET (round-trip réel par coin = 2×taker + spread médian du coin). Portefeuille
equal-weight des coins, jugé CRITIC durci. lag=0 = idéal irréaliste (référence haute) ;
lag=1 = réaliste (latence retail). Si même le GROSS lag=0 est nul → pas de signal du tout.
"""
import json
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, "/home/dexter/hyperdex/backend")
sys.path.insert(0, "/home/dexter/hyperdex/backend/edge_factory")
import obi_backtest as ob
import universe as U
from verdict import evaluate_edge

PATH = "obi_data.jsonl"
THRESHOLDS = [0.1, 0.2, 0.3]
LAGS = [0, 1]


def main():
    rows = [json.loads(l) for l in open(PATH) if l.strip()]
    by = defaultdict(list)
    spreads = defaultdict(list)
    for r in rows:
        by[r["coin"]].append((r["time"], r["obi"], r["mid"]))
        spreads[r["coin"]].append(r.get("spread_bps", 0.0))
    for c in by:
        by[c].sort()
    coins = [c for c in by if len(by[c]) > 50]
    print(f"OBI dataset : {len(rows)} records, {len(coins)} coins, "
          f"~{len(rows)//max(1,len(coins))}/coin", flush=True)
    cost = {c: 2 * U.TAKER_BPS + statistics.median(spreads[c]) for c in coins}
    print(f"round-trip net médian : {statistics.median(list(cost.values())):.1f} bps", flush=True)

    def portfolio(thr, lag, use_cost):
        series = []
        for c in coins:
            cb = cost[c] if use_cost else 0.0
            r = ob.obi_backtest(by[c], threshold=thr, cost_bps=cb, exec_lag=lag)
            if r:
                series.append(r)
        if not series:
            return []
        m = min(len(x) for x in series)
        return [statistics.mean(series[j][t] for j in range(len(series))) for t in range(m)]

    # benchmark = move moyen du panier (buy-and-hold mid)
    bench = []
    bm = []
    for c in coins:
        mids = [x[2] for x in by[c]]
        bm.append([(mids[i] - mids[i - 1]) / mids[i - 1] for i in range(1, len(mids))])
    mlen = min(len(x) for x in bm)
    bench = [statistics.mean(bm[j][t] for j in range(len(bm))) for t in range(mlen)]

    specs = [(thr, lag) for thr in THRESHOLDS for lag in LAGS]
    n_trials = len(specs)
    print(f"\n=== OBI→MOVE — {n_trials} specs × (gross/net) ===", flush=True)
    print(f"   {'thr':>4} {'lag':>3} {'gross_sh':>9} {'net_sh':>8} {'net_t_a':>8} {'pass':>5}",
          flush=True)
    surv = 0
    for thr, lag in specs:
        g = portfolio(thr, lag, use_cost=False)
        net = portfolio(thr, lag, use_cost=True)
        if not net or len(net) < 20:
            continue
        gsh = statistics.mean(g) / statistics.pstdev(g) if len(g) > 1 and statistics.pstdev(g) > 0 else 0.0
        m = min(len(net), len(bench))
        v = evaluate_edge(net[:m], bench[:m], n_trials=n_trials, sr_variance=0.05)
        surv += int(v["pass"])
        print(f"   {thr:>4} {lag:>3} {gsh:>+9.3f} {v['gates']['sharpe']:>+8.3f} "
              f"{v['gates']['beta_neutral']['t_alpha']:>+8.2f} {str(v['pass']):>5}", flush=True)
    print(f"\nSURVIVANTS : {surv}/{n_trials}", flush=True)
    print("lecture : gross_sh lag=0 = signal idéal (sans coût ni latence) ; "
          "net_sh lag=1 = réalité. Si gross_sh lag=0 ~0 → OBI ne prédit RIEN ; "
          "si gross>0 mais net<0 → mur des coûts sous-horaire.", flush=True)


if __name__ == "__main__":
    main()
