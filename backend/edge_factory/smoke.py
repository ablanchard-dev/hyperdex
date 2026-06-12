#!/usr/bin/env python3
"""Smoke end-to-end Phase 1 : VenueAdapter -> returns -> CRITIC.evaluate_edge.

Prouve que le tuyau complet de l'edge-factory tourne bout-en-bout, sans dépendre
d'une venue live. La « stratégie » triviale = buy-and-hold (exposition marché),
qui DOIT être rejetée comme beta par le CRITIC — c'est la validation du juge.
Le vrai backtest de signaux + les adapters niche live = phases suivantes.
"""
from adapter import returns_from_bars
from verdict import evaluate_edge

_BIG = 10 ** 12


def smoke_evaluate(adapter, symbol, n_trials=1, sr_variance=0.1):
    """buy-and-hold du symbole, jugé contre le benchmark de la venue."""
    sym_bars = adapter.history(symbol, 0, _BIG)
    bench_bars = adapter.benchmark(0, _BIG)
    strat = returns_from_bars(sym_bars)
    bench = returns_from_bars(bench_bars)
    m = min(len(strat), len(bench))
    return evaluate_edge(strat[:m], bench[:m],
                         n_trials=n_trials, sr_variance=sr_variance)


if __name__ == "__main__":
    print("smoke.py : module — voir test_smoke.py pour l'exécution end-to-end")
