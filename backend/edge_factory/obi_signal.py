#!/usr/bin/env python3
"""Order-book imbalance (OBI) — frontière microstructure sous-horaire (seul angle gratuit
non-testé). Calcul PUR depuis l2_snapshot HL ; le recorder (poll dans le temps) est l'I/O.

OBI = (Σ sz_bid − Σ sz_ask)/(Σ sz_bid + Σ sz_ask) sur les `depth` meilleurs niveaux.
OBI>0 = pression acheteuse (déséquilibre vers les bids). Prédicteur microstructure classique.
⚠️ prior FAIBLE : alpha contesté par les HFT, latence retail (HL 200-500ms) → on instrumente
et on testera quand assez de snapshots seront accumulés, sans illusion sur l'issue.
"""
from typing import Dict, List, Tuple


def _side_volume(side: list, depth: int) -> float:
    vol = 0.0
    for lvl in side[:depth]:
        try:
            vol += float(lvl["sz"])
        except (KeyError, TypeError, ValueError):
            continue
    return vol


def compute_obi(book: dict, depth: int = 5) -> float:
    """OBI sur les `depth` meilleurs niveaux. 0.0 si carnet vide/malformé."""
    levels = book.get("levels") if isinstance(book, dict) else None
    if not levels or len(levels) < 2:
        return 0.0
    vb = _side_volume(levels[0], depth)
    va = _side_volume(levels[1], depth)
    tot = vb + va
    return (vb - va) / tot if tot > 0 else 0.0


def mid_price(book: dict) -> float:
    """Mid = (meilleur bid + meilleur ask)/2. 0.0 si indisponible."""
    levels = book.get("levels") if isinstance(book, dict) else None
    if not levels or len(levels) < 2 or not levels[0] or not levels[1]:
        return 0.0
    try:
        return (float(levels[0][0]["px"]) + float(levels[1][0]["px"])) / 2.0
    except (KeyError, TypeError, ValueError, IndexError):
        return 0.0


def obi_series(snapshots: List[dict], depth: int = 5) -> List[Tuple[int, float, float]]:
    """Liste de snapshots l2 → série triée [(ts, obi, mid)]. Pour le backtest futur :
    OBI[t] prédit-il le move de mid de t à t+1 ? (signal microstructure, exec_lag à appliquer)."""
    out = []
    for b in snapshots:
        t = b.get("time")
        if t is None:
            continue
        out.append((int(t), compute_obi(b, depth), mid_price(b)))
    out.sort(key=lambda x: x[0])
    return out
