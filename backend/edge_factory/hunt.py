#!/usr/bin/env python3
"""Moteur de chasse à l'edge UNIFIÉ — le cœur de l'appli Alpha-Forge.

Le but de l'appli : CHASSER des edges/stratégies, les juger sans pitié, garder la
trace. Ce module unifie toutes les familles éparpillées (momentum, cross-sectional,
funding, hawkes, transfer-entropy, liq-spike…) derrière UN registre + UN harnais :

  Registry.register(nom, hunter)   # hunter() → dict d'inputs CRITIC
  Registry.judge(nom)              # → verdict 4-gates + log research_memory
  Registry.hunt_all()             # juge tous les chasseurs
  Registry.leaderboard()          # classe : survivants d'abord, par Sharpe

Un "hunter" est une callable sans argument qui retourne un dict :
  {strat, bench, n_trials, sr_variance, [pbo_matrix], [permutation]}
(les familles existantes produisent déjà ces returns ; un hunter = l'adaptateur
fetch+backtest → ces séries). Le CRITIC (verdict.evaluate_edge) reste l'arbitre.
"""
from typing import Callable, Dict, List, Optional

from research_memory import ResearchMemory
from verdict import evaluate_edge

Hunter = Callable[[], Dict]


class Registry:
    def __init__(self, memory_path: Optional[str] = None):
        self._hunters: Dict[str, Hunter] = {}
        self._memory = ResearchMemory(memory_path) if memory_path else None
        self._results: Dict[str, Dict] = {}

    def register(self, name: str, hunter: Hunter) -> None:
        self._hunters[name] = hunter

    def names(self) -> List[str]:
        return list(self._hunters)

    def judge(self, name: str) -> Dict:
        """Exécute le chasseur, le juge via le CRITIC 4-gates, logge le verdict.

        V1 — n_trials réel : le DSR doit déflater par le VRAI nombre d'essais du
        multiple-testing = au moins le nombre de hunters enregistrés (chaque hunter
        est un essai), et au plus le max avec la grille interne déclarée par la famille.
        Sinon le DSR sous-déflate → gate cosmétique.
        """
        if name not in self._hunters:
            raise KeyError(f"chasseur inconnu : {name}")
        inp = self._hunters[name]()
        effective_n_trials = max(inp.get("n_trials", 1), len(self._hunters))
        v = evaluate_edge(
            inp_get(inp, "strat"), inp_get(inp, "bench"),
            n_trials=effective_n_trials,
            sr_variance=inp.get("sr_variance", 0.05),
            pbo_matrix=inp.get("pbo_matrix"),
            permutation=inp.get("permutation"),
        )
        v["gates"]["effective_n_trials"] = effective_n_trials
        res = {"name": name, "pass": v["pass"], "reasons": v["reasons"],
               "gates": v["gates"]}
        self._results[name] = res
        if self._memory is not None:
            self._memory.record({"hypothesis": {"signal": {"type": name}},
                                 "venue": "hunt", "pass": v["pass"],
                                 "reasons": v["reasons"], "gates": v["gates"]})
            self._memory.save()
        return res

    def hunt_all(self) -> List[Dict]:
        """Juge TOUS les chasseurs enregistrés (la chasse complète)."""
        return [self.judge(n) for n in self._hunters]

    def leaderboard(self) -> List[Dict]:
        """Classe les résultats : survivants d'abord, puis par Sharpe décroissant."""
        rows = list(self._results.values())
        return sorted(rows, key=lambda r: (r["pass"], r["gates"].get("sharpe", 0.0)),
                      reverse=True)


def inp_get(inp: Dict, key: str):
    if key not in inp:
        raise KeyError(f"le hunter doit fournir '{key}'")
    return inp[key]
