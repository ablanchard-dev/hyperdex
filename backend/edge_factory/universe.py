#!/usr/bin/env python3
"""Univers LIVE tradeable HL — la règle « univers = live » (cahier des charges §7).

On ne cherche QUE dans le tradeable réel. Ce module est la SOURCE UNIQUE de l'univers
(perps liquides + contraintes réelles) pour tous les hunters. Sondé sur l'API HL :
  meta.universe[i] = {name, szDecimals (lot), maxLeverage, marginTableId}
  ctxs[i]          = {funding (HORAIRE), openInterest, dayNtlVlm ($24h), markPx,
                      premium, midPx, impactPxs [bid_impact, ask_impact], ...}

Frais HL officiels (vérifiés, base tier) : maker 1.5 bps / taker 4.5 bps, min $10
notionnel, funding horaire, prix = 5 sig figs / max (6 − szDecimals) décimales.
Granularité exécutable = 1h (latence HL 200-500 ms → sub-minute non-fiable pour du retail).
"""
from typing import Dict, List, NamedTuple, Optional

MAKER_BPS = 1.5
TAKER_BPS = 4.5
MIN_NOTIONAL_USD = 10.0
FUNDING_INTERVAL_H = 1
EXECUTABLE_INTERVAL = "1h"
DEFAULT_MIN_DVOL_USD = 10_000_000  # 10M$/jour = seuil de liquidité tradeable


class Perp(NamedTuple):
    name: str
    sz_decimals: int
    max_leverage: int
    day_ntl_vlm: float      # volume notionnel 24h ($)
    funding: float          # funding horaire (fraction)
    mark_px: float
    spread_bps: Optional[float]
    min_order_size: float


def _f(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def min_order_size(px: float, sz_decimals: int) -> float:
    """Taille mini pour atteindre $10 notionnel, arrondie au lot (szDecimals).
    On arrondit vers le HAUT au pas du lot pour garantir ≥ $10."""
    if px <= 0:
        return 0.0
    raw = MIN_NOTIONAL_USD / px
    step = 10 ** (-sz_decimals)
    # ceil au pas du lot
    import math
    lots = math.ceil(raw / step - 1e-9)
    return round(lots * step, sz_decimals)


def spread_bps(impact_pxs) -> Optional[float]:
    """Spread relatif en bps depuis impactPxs [bid_impact, ask_impact]."""
    if not impact_pxs or len(impact_pxs) < 2:
        return None
    bid, ask = _f(impact_pxs[0]), _f(impact_pxs[1])
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / 2.0
    return (ask - bid) / mid * 1e4 if mid > 0 else None


def build_universe(meta: dict, ctxs: list,
                   min_dvol_usd: float = DEFAULT_MIN_DVOL_USD) -> List[Perp]:
    """Construit l'univers tradeable : perps avec volume ≥ seuil, triés liquidité↓."""
    out: List[Perp] = []
    universe = meta.get("universe", [])
    for i, m in enumerate(universe):
        if i >= len(ctxs):
            break
        ctx = ctxs[i]
        dvol = _f(ctx.get("dayNtlVlm")) or 0.0
        if dvol < min_dvol_usd:
            continue
        px = _f(ctx.get("markPx")) or 0.0
        szd = int(m.get("szDecimals", 0))
        out.append(Perp(
            name=m["name"],
            sz_decimals=szd,
            max_leverage=int(m.get("maxLeverage", 1)),
            day_ntl_vlm=dvol,
            funding=_f(ctx.get("funding")) or 0.0,
            mark_px=px,
            spread_bps=spread_bps(ctx.get("impactPxs")),
            min_order_size=min_order_size(px, szd) if px > 0 else 0.0,
        ))
    out.sort(key=lambda p: -p.day_ntl_vlm)
    return out


def tradeable_names(univ: List[Perp]) -> List[str]:
    return [p.name for p in univ]


def constraints_map(univ: List[Perp]) -> Dict[str, dict]:
    """Map name → contraintes (pour que les hunters lisent les coûts réels)."""
    return {p.name: {"sz_decimals": p.sz_decimals, "max_leverage": p.max_leverage,
                     "day_ntl_vlm": p.day_ntl_vlm, "funding": p.funding,
                     "mark_px": p.mark_px, "spread_bps": p.spread_bps,
                     "min_order_size": p.min_order_size}
            for p in univ}


def round_trip_cost_bps(spread_bps_val: Optional[float],
                        maker: bool = False) -> float:
    """Coût aller-retour réaliste en bps : 2× frais + 1× spread traversé (taker).
    En maker on paie le rebate (pas le spread) ; en taker on paie frais + demi-spread."""
    fee = MAKER_BPS if maker else TAKER_BPS
    sp = spread_bps_val or 0.0
    return 2 * fee + (sp if not maker else 0.0)
