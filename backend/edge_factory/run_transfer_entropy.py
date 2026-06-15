#!/usr/bin/env python3
"""Investigation LIVE : transfer entropy directionnelle BTC ↔ alts sur HL.

Mesure l'information directionnelle (effective TE, biais corrigé par surrogates)
entre les returns 1h de BTC et de chaque alt liquide. Question : qui LEAD qui ?
La recherche dit que les alts leadent parfois BTC (sens inverse de mon lead-lag
LINÉAIRE réfuté) — la TE (non-linéaire, asymétrique) est l'outil correct pour trancher.

PRÉ-ENREGISTRÉ : top-20 HL par volume (figé), 90j × 1h, n_bins=4, 200 surrogates.
On ne TRADE rien ici — on mesure si un flux exploitable existe AVANT de bâtir un signal.
"""
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
import transfer_entropy as te
from app.services.hl_api.info_client import InfoClient

HOUR = 3600_000
N_UNIV = 20
DAYS = 90
N_BINS = 4
N_SURRO = 200


def returns(closes):
    return [(closes[i] - closes[i - 1]) / closes[i - 1] if closes[i - 1] else 0.0
            for i in range(1, len(closes))]


def main():
    c = InfoClient(min_interval_s=1.0)
    meta, ctxs = c.meta_and_asset_ctxs()
    names = [a["name"] for a in meta["universe"]]
    vol = {names[i]: float(ctxs[i].get("dayNtlVlm", 0) or 0) for i in range(len(names))}
    coins = sorted(names, key=lambda s: -vol[s])[:N_UNIV]
    if "BTC" not in coins:
        coins = ["BTC"] + coins[:N_UNIV - 1]
    end = int(time.time() * 1000)
    start = end - DAYS * 24 * HOUR
    print(f"[{time.strftime('%H:%M:%S')}] fetch {len(coins)} perps × {DAYS}j 1h...", flush=True)
    raw = {}
    for s in coins:
        try:
            k = c.candles(s, "1h", start, end)
            if len(k) >= 1000:
                raw[s] = {int(x["t"]) // HOUR: float(x["c"]) for x in k}
        except Exception:
            pass
    common = sorted(set.intersection(*[set(m) for m in raw.values()]))
    rets = {s: returns([raw[s][h] for h in common]) for s in raw}
    print(f"   {len(rets)} coins | {len(common)} heures (~{len(common)/24:.0f}j)\n", flush=True)
    if "BTC" not in rets:
        print("BTC absent — abort.", flush=True)
        return

    btc = rets["BTC"]
    print(f"=== TRANSFER ENTROPY directionnelle BTC ↔ alt (effective, {N_SURRO} surro) ===",
          flush=True)
    print(f"{'alt':<8} {'TE(BTC→alt)':>12} {'TE(alt→BTC)':>12} {'sens':>10} {'sig':>5}", flush=True)
    leads_btc = []
    for s in sorted(rets):
        if s == "BTC":
            continue
        a = rets[s]
        m = min(len(btc), len(a))
        b2a = te.effective_transfer_entropy(btc[:m], a[:m], n_bins=N_BINS,
                                            n_surrogates=N_SURRO, seed=1)
        a2b = te.effective_transfer_entropy(a[:m], btc[:m], n_bins=N_BINS,
                                            n_surrogates=N_SURRO, seed=2)
        if b2a["ete"] > a2b["ete"]:
            sens, sig = "BTC→alt", b2a["significant"]
        else:
            sens, sig = "alt→BTC", a2b["significant"]
            if a2b["significant"]:
                leads_btc.append(s)
        print(f"{s:<8} {b2a['ete']:>12.5f} {a2b['ete']:>12.5f} {sens:>10} {str(sig):>5}",
              flush=True)
    print(f"\nAlts qui LEADENT BTC significativement : {leads_btc or 'AUCUN'}", flush=True)
    print("(si AUCUN sig → pas de flux exploitable simple ; honnête, documenté)", flush=True)


if __name__ == "__main__":
    main()
