#!/usr/bin/env python3
"""LA CHASSE COMPLÈTE — sur l'UNIVERS LIVE figé (règle « univers = live » §7).

C'est le point d'entrée de l'appli : `python run_hunt.py` → chasse industrialisée.
La recherche est CONTRAINTE à l'univers tradeable réel (universe.build_universe :
perps ≥ seuil de liquidité, contraintes/coûts réels), pas à un top-N arbitraire.
Ajouter une famille = un make_*_hunter de plus dans build_registry(). Data partagée
(fetch HL 1×), verdicts loggés en research_memory, classés survivants-d'abord.
"""
import statistics
import sys
import time

sys.path.insert(0, "/opt/app/hyperdex/backend")
sys.path.insert(0, "/opt/app/hyperdex/backend/edge_factory")
import coinalyze as cz
import hunters as H
import liq_spike as ls
import universe as U
from adapter import Bar
from hunt import Registry
from app.services.hl_api.info_client import InfoClient

HOUR = 3600_000
DAYS = 60
MEM = "hunt_memory.json"


def fetch_hl():
    """Fetch sur l'UNIVERS LIVE figé (universe.build_universe) — pas un top-N arbitraire.
    Retourne (bars, univ) : candles 1h des perps tradeables + l'objet univers (coûts)."""
    c = InfoClient(min_interval_s=1.0)
    meta, ctxs = c.meta_and_asset_ctxs()
    univ = U.build_universe(meta, ctxs)  # perps ≥ seuil liquidité, contraintes réelles
    coins = U.tradeable_names(univ)
    end = int(time.time() * 1000)
    start = end - DAYS * 24 * HOUR
    print(f"[{time.strftime('%H:%M:%S')}] univers live = {len(coins)} perps tradeables "
          f"(≥{U.DEFAULT_MIN_DVOL_USD/1e6:.0f}M$/j) × {DAYS}j candles {U.EXECUTABLE_INTERVAL}...",
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
    # coût réel : spread médian de l'univers (les hunters l'utilisent au lieu d'un slip en dur)
    sps = sorted(p.spread_bps for p in univ if p.spread_bps is not None)
    med_spread = sps[len(sps) // 2] if sps else 5.0
    print(f"   {len(bars)} coins avec ≥1000 barres | {len(common)} heures (~{len(common)/24:.0f}j) "
          f"| spread médian {med_spread:.2f}bps → slip réel utilisé", flush=True)
    return bars, med_spread, c


def fetch_funding(client, coins, days=DAYS):
    """Funding + premium HORAIRES HL natifs (funding_history_paged) alignés sur heures
    communes → (funding_by_coin, premium_by_coin) pour la famille carry delta-neutral."""
    end = int(time.time() * 1000)
    start = end - days * 24 * HOUR
    data = {}
    for s in coins:
        try:
            fh = client.funding_history_paged(s, start, end)
            if len(fh) >= 1000:
                hrs = [int(x["time"]) // HOUR for x in fh]
                data[s] = (hrs,
                           {h: float(x["fundingRate"]) for h, x in zip(hrs, fh)},
                           {h: float(x["premium"]) for h, x in zip(hrs, fh)})
        except Exception:
            pass
    if len(data) < 5:
        return {}, {}
    common = sorted(set.intersection(*[set(v[0]) for v in data.values()]))
    funding = {s: [v[1][h] for h in common] for s, v in data.items()}
    premium = {s: [v[2][h] for h in common] for s, v in data.items()}
    print(f"   funding HL natif : {len(funding)} coins × {len(common)} heures", flush=True)
    return funding, premium


def fetch_oi(coins, bars, days=DAYS):
    """Open-interest HORAIRE HL natif (Coinalyze open-interest-history, {coin}.H — marche
    contrairement aux liq) → {coin: oi_close[]} aligné aux barres pour la divergence OI-prix."""
    import os
    key = open(os.path.expanduser("~/.coinalyze_key")).read().strip()
    to = int(time.time())
    frm = to - days * 24 * 3600
    out = {}
    for s in coins:
        try:
            payload = cz.fetch_oi_history(cz.hl_symbol(s), "1hour", frm, to, api_key=key)
            series = cz.parse_oi_history(payload, [b.ts for b in bars[s]])
            if series and any(v > 0 for v in series):
                out[s] = series
        except Exception:
            pass
    print(f"   open-interest HL natif (Coinalyze .H) : {len(out)} coins", flush=True)
    return out


def fetch_liquidations(coins, bars, days=DAYS):
    """Liquidations HL natives via Coinalyze ('{coin}.H'), agrégées en net_liq signé
    par barre (aligné aux candles de chaque coin) → {coin: net_liq[]} pour liq_spike."""
    import os
    key = open(os.path.expanduser("~/.coinalyze_key")).read().strip()
    to = int(time.time())
    frm = to - days * 24 * 3600
    out = {}
    for s in coins:
        try:
            # HL natif d'abord ; si vide (Coinalyze ne l'a pas) → proxy Binance market-wide
            events = cz.parse_liquidation_history(
                cz.fetch_liquidation_history(cz.hl_symbol(s), "1hour", frm, to, api_key=key))
            if not events:
                events = cz.parse_liquidation_history(
                    cz.fetch_liquidation_history(cz.binance_liq_symbol(s), "1hour",
                                                 frm, to, api_key=key))
            if not events:
                continue
            out[s] = ls.net_liquidation_per_bar(events, [b.ts for b in bars[s]])
        except Exception:
            pass
    print(f"   liquidations (HL natif sinon proxy Binance market-wide) : {len(out)} coins",
          flush=True)
    return out


def build_registry(bars, slippage_bps=5.0, funding=None, premium=None, liq=None,
                   oi=None):
    """Enregistre TOUTES les familles comme chasseurs (grilles pré-enreg, coûts réels).
    slippage_bps = spread médian réel de l'univers live. funding/premium/liq = data
    optionnelle (si fournie → branche les familles funding-carry et liq-spike)."""
    btc = bars.get("BTC") or next(iter(bars.values()))
    reg = Registry(memory_path=MEM)

    # famille cross-sectional : momentum + reversion × lookbacks (grille = n_trials)
    xs_grid = [(f, lb) for f in ("xs_momentum", "xs_reversion")
               for lb in (6, 12, 24)]
    n_xs = len(xs_grid)
    for feat, lb in xs_grid:
        reg.register(f"{feat}_lb{lb}", H.make_cross_sectional_hunter(
            bars, btc, feat, {"lookback": lb}, top_frac=0.3,
            taker_bps=U.TAKER_BPS, slippage_bps=slippage_bps, n_trials=n_xs))

    # famille lead-lag BTC→alts (grille lookbacks)
    alts = {s: b for s, b in bars.items() if s != "BTC"}
    for lb in (1, 2, 3):
        reg.register(f"lead_lag_lb{lb}", H.make_lead_lag_hunter(
            alts, btc, lookback=lb, top_frac=0.3,
            taker_bps=U.TAKER_BPS, slippage_bps=slippage_bps, n_trials=3))

    # famille FUNDING CARRY delta-neutral (maker — le carry s'exécute en limit)
    if funding and premium:
        reg.register("funding_carry", H.make_funding_carry_hunter(
            funding, premium, btc, fee_bps=U.MAKER_BPS, n_trials=1))

    # famille LIQ-SPIKE contrarian par coin (z-threshold pré-enreg, grille = n_trials)
    if liq:
        liq_coins = [s for s in liq if s in bars]
        for s in liq_coins:
            reg.register(f"liq_spike_{s}", H.make_liq_spike_hunter(
                bars[s], liq[s], z_window=48, z_threshold=2.0,
                taker_bps=U.TAKER_BPS, slippage_bps=slippage_bps,
                n_trials=len(liq_coins)))

    # famille OI-DIVERGENCE contrarian par coin (positionnement crowded, P2)
    if oi:
        oi_coins = [s for s in oi if s in bars]
        for s in oi_coins:
            reg.register(f"oi_div_{s}", H.make_oi_divergence_hunter(
                bars[s], oi[s], window=48, threshold=2.0,
                taker_bps=U.TAKER_BPS, slippage_bps=slippage_bps,
                n_trials=len(oi_coins)))

    return reg


def main():
    bars, med_spread, client = fetch_hl()
    if len(bars) < 10:
        print("data insuffisante.", flush=True)
        return
    coins = list(bars)
    funding, premium = fetch_funding(client, coins)
    liq = fetch_liquidations(coins, bars)
    oi = fetch_oi(coins, bars)
    reg = build_registry(bars, slippage_bps=med_spread,
                         funding=funding, premium=premium, liq=liq, oi=oi)
    print(f"\n=== CHASSE : {len(reg.names())} familles enregistrées → CRITIC 5-gates "
          f"(beta+DSR+PBO+perm+convexité, durci t=3/PBO=0.2) ===", flush=True)
    t0 = time.perf_counter()
    reg.hunt_all()
    print(f"   chasse en {time.perf_counter()-t0:.0f}s\n", flush=True)

    lb = reg.leaderboard()
    header = f"{'famille':<22} | {'PASS':>5} | {'sharpe':>7} | {'dsr':>5} | {'beta':>6} | {'t_alpha':>7} | reasons"
    print(header, flush=True)
    print("-" * len(header), flush=True)
    for r in lb:
        g = r["gates"]
        bn = g.get("beta_neutral", {})
        print(f"{r['name']:<22} | {str(r['pass']):>5} | {g.get('sharpe', 0):>+7.3f} | "
              f"{g.get('dsr', 0):>5.2f} | {bn.get('beta', 0):>+6.2f} | "
              f"{bn.get('t_alpha', 0):>+7.2f} | {','.join(r['reasons'])}", flush=True)
    surv = [r for r in lb if r["pass"]]
    print(f"\nSURVIVANTS : {len(surv)}/{len(lb)}"
          f"{' → ' + ', '.join(s['name'] for s in surv) if surv else ' (aucun — honnête)'}",
          flush=True)


if __name__ == "__main__":
    main()
