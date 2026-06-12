#!/usr/bin/env python3
"""Famille Token Unlock — supply shock daté (offre forcée VC/équipe) → drift résiduel.

Mécanisme (le perdant structurel) : les unlocks cliff = offre FORCÉE, datée, insensible
au prix (vesting VC/équipe à bas coût de base). Shorter est CONTRAINT (borrow indispo,
funding qui punit les shorts crowded) → l'anticipation reste INCOMPLÈTE → il reste du
drift exploitable. Si l'edge disparaît après neutralisation beta → c'était de la beta alt
(le CRITIC le tue, gate 1).

Spec pré-enregistrée (cahier des charges #1) :
  - magnitude = tokens débloqués / offre circulante (as-of J-1) ; SEUIL 2 %.
  - fenêtre primaire [J-5, J-1] (entrée connue à l'avance → exec_lag respecté).
  - SHORT le token ; le CRITIC neutralise vs le marché (bench) = alpha résiduel only.
  - coûts : round-trip taker+slippage amortis sur la fenêtre + funding/jour.
  - UNE spec primaire, pas de grille (pas de multiple-testing).

Data-agnostique : events + bars passés en argument (testable offline, comme les autres
familles). Le fetch live (source d'unlocks + prix + funding) est séparé (run_*.py).
"""
from typing import Callable, Dict, List

from adapter import Bar

Hunter = Callable[[], Dict]
DAY_SECONDS = 86400


def _returns_by_ts(bars: List[Bar]) -> Dict[int, float]:
    """ts du jour -> return close-to-close (jour précédent -> ce jour)."""
    out: Dict[int, float] = {}
    for i in range(1, len(bars)):
        p0, p1 = bars[i - 1].close, bars[i].close
        out[bars[i].ts] = (p1 - p0) / p0 if p0 else 0.0
    return out


def _estimate_beta(tok_ret: Dict[int, float], bench_ret: Dict[int, float],
                   end_ts: int, lookback_days: int, day_seconds: int,
                   default: float = 1.0, min_pts: int = 20) -> float:
    """Beta du token vs marché sur les jours AVANT end_ts (aucun look-ahead)."""
    xs: List[float] = []
    ys: List[float] = []
    for k in range(1, lookback_days + 1):
        ts = end_ts - k * day_seconds
        rt, rm = tok_ret.get(ts), bench_ret.get(ts)
        if rt is None or rm is None:
            continue
        ys.append(rt)
        xs.append(rm)
    if len(xs) < min_pts:
        return default
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    cov = sum((xs[i] - mx) * (ys[i] - my) for i in range(len(xs)))
    var = sum((x - mx) ** 2 for x in xs)
    if var <= 0:
        return default
    return max(0.0, min(3.0, cov / var))


def unlock_short_series(events: List[Dict], symbol_bars: Dict[str, List[Bar]],
                        bench_bars: List[Bar], mag_threshold: float = 0.02,
                        window=(-5, -1), taker_bps: float = 4.5,
                        slippage_bps: float = 5.0, funding_bps_per_day: float = 0.0,
                        beta_lookback_days: int = 60, day_seconds: int = DAY_SECONDS):
    """(strat_returns, bench_returns) alignés sur les jours d'événements ACTIFS.

    Pour chaque unlock 'gros' (magnitude >= seuil), position BETA-NEUTRE sur la fenêtre :
    short token + long β×marché (β estimé AVANT la fenêtre). Return du jour =
    -ret_token + β·ret_marché - coûts_amortis - funding → isole le résiduel (pas la beta
    alt). On agrège par jour (moyenne si plusieurs tokens actifs) et on aligne le bench du
    même jour. Le gate beta-neutral du CRITIC confirme alors que la beta résiduelle ≈ 0.
    """
    bench_ret = _returns_by_ts(bench_bars)
    tok_ret = {s: _returns_by_ts(b) for s, b in symbol_bars.items()}
    w0, w1 = window
    ndays = max(1, w1 - w0 + 1)
    rt_cost = (2 * taker_bps + 2 * slippage_bps) / 1e4 / ndays   # round-trip amorti/jour
    fund = funding_bps_per_day / 1e4

    by_day: Dict[int, List[float]] = {}
    for ev in events:
        if ev.get("magnitude", 0.0) < mag_threshold:
            continue
        sym = ev.get("symbol")
        if sym not in tok_ret:
            continue
        u_ts = ev["unlock_ts"]
        beta = _estimate_beta(tok_ret[sym], bench_ret, u_ts + w0 * day_seconds,
                              beta_lookback_days, day_seconds)
        for d in range(w0, w1 + 1):
            day_ts = u_ts + d * day_seconds
            r = tok_ret[sym].get(day_ts)
            rm = bench_ret.get(day_ts)
            if r is None or rm is None:
                continue
            # short token (-r) + long β×marché (+β·rm) = position beta-neutre
            by_day.setdefault(day_ts, []).append(-r + beta * rm - rt_cost - fund)

    strat: List[float] = []
    bench: List[float] = []
    for day_ts in sorted(by_day):
        if day_ts not in bench_ret:
            continue
        contribs = by_day[day_ts]
        strat.append(sum(contribs) / len(contribs))
        bench.append(bench_ret[day_ts])
    return strat, bench


def make_token_unlock_hunter(events: List[Dict], symbol_bars: Dict[str, List[Bar]],
                             bench_bars: List[Bar], mag_threshold: float = 0.02,
                             window=(-5, -1), taker_bps: float = 4.5,
                             slippage_bps: float = 5.0, funding_bps_per_day: float = 0.0,
                             beta_lookback_days: int = 60, day_seconds: int = DAY_SECONDS,
                             train_frac: float = 0.7, n_trials: int = 1,
                             sr_variance: float = 0.05) -> Hunter:
    """Hunter conforme à hunt.Registry. Split OOS par TEMPS D'ÉVÉNEMENT (test = unlocks
    après le cutoff chronologique → le CRITIC juge hors-échantillon). Retourne le dict
    {strat, bench, n_trials, sr_variance} attendu par verdict.evaluate_edge."""
    def hunter() -> Dict:
        evs = sorted(events, key=lambda e: e["unlock_ts"])
        if len(evs) > 1:
            ts = [e["unlock_ts"] for e in evs]
            cutoff = ts[int(len(ts) * train_frac)]
            test_events = [e for e in evs if e["unlock_ts"] >= cutoff]
        else:
            test_events = evs
        strat, bench = unlock_short_series(
            test_events, symbol_bars, bench_bars, mag_threshold=mag_threshold,
            window=window, taker_bps=taker_bps, slippage_bps=slippage_bps,
            funding_bps_per_day=funding_bps_per_day,
            beta_lookback_days=beta_lookback_days, day_seconds=day_seconds)
        return {"strat": strat, "bench": bench,
                "n_trials": n_trials, "sr_variance": sr_variance}
    return hunter
