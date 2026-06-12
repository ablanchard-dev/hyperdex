#!/usr/bin/env python3
"""DSL d'hypothèses + interpréteur SÛR pour l'agent LLM Hypothesis.

Le LLM émet une hypothèse comme spec JSON depuis CE vocabulaire fermé — jamais
du code arbitraire (pas d'exec arbitraire, pas de bug look-ahead injecté).
L'interpréteur valide la spec puis produit un signal(closes)->{-1,0,1} qui
n'utilise que le passé (les closes passées lui sont fournies tronquées par
l'appelant ; aucune fonction ne lit le futur).

Vocabulaire : momentum, ma_cross, zscore_reversion, breakout.
"""
import statistics
from typing import Callable, List

# schéma : type -> (params requis, validateur)
_SCHEMA = {
    "momentum": (["lookback"],
                 lambda p: p["lookback"] >= 1),
    "breakout": (["lookback"],
                 lambda p: p["lookback"] >= 1),
    "ma_cross": (["fast", "slow"],
                 lambda p: p["fast"] >= 1 and p["slow"] > p["fast"]),
    "zscore_reversion": (["lookback", "entry_z"],
                         lambda p: p["lookback"] >= 2 and p["entry_z"] > 0),
}


def validate_spec(spec: dict) -> bool:
    """True ssi la spec est exécutable en sécurité (type connu, params valides)."""
    try:
        sig = spec["signal"]
        stype, params = sig["type"], sig["params"]
    except (KeyError, TypeError):
        return False
    if stype not in _SCHEMA:
        return False
    required, validator = _SCHEMA[stype]
    if any(k not in params for k in required):
        return False
    try:
        return bool(validator(params))
    except (TypeError, ValueError, ZeroDivisionError):
        return False


def _momentum(p):
    lb = p["lookback"]
    def f(closes):
        if len(closes) <= lb:
            return 0
        return 1 if closes[-1] > closes[-1 - lb] else -1
    return f


def _breakout(p):
    lb = p["lookback"]
    def f(closes):
        if len(closes) <= lb:
            return 0
        w = closes[-1 - lb:-1]
        if closes[-1] > max(w):
            return 1
        if closes[-1] < min(w):
            return -1
        return 0
    return f


def _ma_cross(p):
    fast, slow = p["fast"], p["slow"]
    def f(closes):
        if len(closes) < slow:
            return 0
        ma_f = sum(closes[-fast:]) / fast
        ma_s = sum(closes[-slow:]) / slow
        return 1 if ma_f > ma_s else (-1 if ma_f < ma_s else 0)
    return f


def _zscore_reversion(p):
    lb, ez = p["lookback"], p["entry_z"]
    def f(closes):
        if len(closes) < lb:
            return 0
        w = closes[-lb:]
        mu = sum(w) / lb
        sd = statistics.pstdev(w)
        if sd <= 0:
            return 0
        z = (closes[-1] - mu) / sd
        if z > ez:
            return -1   # étendu vers le haut -> fade
        if z < -ez:
            return 1
        return 0
    return f


_BUILDERS = {"momentum": _momentum, "breakout": _breakout,
             "ma_cross": _ma_cross, "zscore_reversion": _zscore_reversion}


def build_signal(spec: dict) -> Callable[[List[float]], int]:
    """spec validée -> fn(closes)->{-1,0,1}. Lève ValueError si invalide."""
    if not validate_spec(spec):
        raise ValueError(f"spec invalide : {spec.get('signal')}")
    sig = spec["signal"]
    return _BUILDERS[sig["type"]](sig["params"])
