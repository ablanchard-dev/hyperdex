#!/usr/bin/env python3
"""Agent LLM Hypothesis — un LLM propose des hypothèses en JSON-DSL.

L'agent EXTRAIT le JSON de la réponse LLM, VALIDE chaque spec contre le DSL sûr
(hypothesis_dsl.validate_spec), et ne retourne que les exécutables. L'appel LLM
est injecté (mock en test, claude CLI en --live). Le LLM ne produit JAMAIS de
code — seulement des specs depuis le vocabulaire fermé.

⚠️ Sur le DSL actuel (TA prix), l'agent génère surtout du beta (TA prix = beta,
prouvé). Sa vraie valeur viendra des primitives cross-sectional market-neutral
(enrichissement vocabulaire à venir) ; ici on pose la BOUCLE d'autonomie.
"""
import json
import re
import subprocess
from typing import Callable, List

from hypothesis_dsl import validate_spec

_VOCAB = ("momentum{lookback>=1}, breakout{lookback>=1}, "
          "ma_cross{fast>=1, slow>fast}, zscore_reversion{lookback>=2, entry_z>0}")


def build_prompt(n: int) -> str:
    return (
        f"Tu es un agent de recherche quant. Propose {n} hypothèses de trading "
        "DISTINCTES, en variant les familles et les paramètres. "
        "Réponds UNIQUEMENT par un tableau JSON. Chaque élément : "
        '{"name":str,"rationale":str,"signal":{"type":..,"params":{..}}}. '
        f"Types et params autorisés (STRICT) : {_VOCAB}. "
        "N'invente aucun type hors de cette liste. Donne un rationale économique court."
    )


def extract_specs(text: str) -> List[dict]:
    """Extrait un tableau JSON de la réponse LLM (fence ```json``` ou brut)."""
    if not text:
        return []
    candidates = []
    fence = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    if fence:
        candidates.append(fence.group(1))
    # fallback : premier '[' jusqu'au dernier ']'
    i, j = text.find("["), text.rfind("]")
    if i != -1 and j > i:
        candidates.append(text[i:j + 1])
    for c in candidates:
        try:
            data = json.loads(c)
            if isinstance(data, list):
                return [d for d in data if isinstance(d, dict)]
        except json.JSONDecodeError:
            continue
    return []


def valid_specs(specs: List[dict]) -> List[dict]:
    return [s for s in specs if validate_spec(s)]


def generate_hypotheses(call_llm: Callable[[str], str], n: int = 10) -> List[dict]:
    """Prompte le LLM, extrait + valide les specs DSL exécutables."""
    return valid_specs(extract_specs(call_llm(build_prompt(n))))


def call_llm_claude(prompt: str, timeout: int = 120) -> str:
    """Appel réel via la CLI claude (non-interactif). Best-effort."""
    try:
        r = subprocess.run(["claude", "--print", prompt],
                           capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return f"(LLM indisponible: {type(e).__name__})"


if __name__ == "__main__":
    import sys
    if "--live" in sys.argv:
        specs = generate_hypotheses(call_llm_claude, n=8)
        print(f"hypothèses valides générées par le LLM : {len(specs)}")
        for s in specs:
            print(" ", s["signal"]["type"], s["signal"]["params"],
                  "—", s.get("rationale", "")[:60])
    else:
        print("usage: python llm_hypothesis.py --live")
