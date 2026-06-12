#!/usr/bin/env python3
"""Connecteur Numerai Signals — mapping signal interne → submission (sortie monétisation).

Format Numerai (doc) : CSV index=ticker, colonne 'prediction' ∈ ]0,1[ EXCLUSIF,
≥100 tickers de l'univers, chaque ticker UNE fois. On RANK-normalise des scores
internes arbitraires (n'importe quelle échelle) vers ]0,1[ — seul le rang compte,
robuste aux outliers (cf neutralisation Numerai). Pur & testable sans réseau ;
l'upload via numerapi (clés Numerai publique+secrète) = couche I/O séparée.
"""
from typing import Dict, Optional, Set, Tuple

MIN_TICKERS = 100


def to_predictions(scores: Dict[str, float],
                   universe: Optional[Set[str]] = None) -> Dict[str, float]:
    """Scores arbitraires → predictions ∈ ]0,1[ par rang (Gaussian-rank simple).

    pred_i = (rang_i + 1) / (N + 1) → strictement dans ]0,1[ (jamais 0 ni 1).
    universe : si fourni, ne garde que les tickers présents dans l'univers."""
    items = scores
    if universe is not None:
        items = {t: s for t, s in scores.items() if t in universe}
    n = len(items)
    if n == 0:
        return {}
    ranked = sorted(items, key=lambda t: items[t])
    return {t: (i + 1) / (n + 1) for i, t in enumerate(ranked)}


def to_csv(scores: Dict[str, float],
           universe: Optional[Set[str]] = None) -> str:
    """Submission CSV : 'ticker,prediction' triée par ticker (déterministe)."""
    pred = to_predictions(scores, universe)
    lines = ["ticker,prediction"]
    for t in sorted(pred):
        lines.append(f"{t},{pred[t]:.6f}")
    return "\n".join(lines) + "\n"


def validate_submission(pred: Dict[str, float]) -> Tuple[bool, str]:
    """Vérifie les règles Numerai avant upload (≥100 tickers, ]0,1[, unicité)."""
    if len(pred) < MIN_TICKERS:
        return False, f"besoin de ≥{MIN_TICKERS} tickers, reçu {len(pred)}"
    for t, v in pred.items():
        if not (0.0 < v < 1.0):
            return False, f"prediction hors ]0,1[ pour {t}: {v}"
    return True, "ok"


def write_submission(scores: Dict[str, float], path: str,
                     universe: Optional[Set[str]] = None) -> Tuple[bool, str]:
    """Écrit le CSV de submission après validation. Retourne (ok, message)."""
    pred = to_predictions(scores, universe)
    ok, msg = validate_submission(pred)
    if not ok:
        return False, msg
    with open(path, "w") as f:
        f.write(to_csv(scores, universe))
    return True, path


def upload(path: str, model_id: str, public_id: str,
           secret_key: str) -> str:  # pragma: no cover (I/O réseau, clés Numerai)
    """Upload via numerapi SignalsAPI. Clés Numerai DISTINCTES de la clé liq."""
    from numerapi import SignalsAPI
    sapi = SignalsAPI(public_id, secret_key)
    return sapi.upload_predictions(path, model_id=model_id)
