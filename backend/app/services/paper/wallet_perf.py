"""Per-wallet performance tracking + auto-mute des wallets négatifs (A4).

Tracker per-trader stats observed sur paper closes :
- n_wins, n_losses
- pnl_total
- rolling_30_closes (deque) avec PnL signed
- last_update_ts

Auto-mute trigger : si n_losses > MIN_LOSSES_FOR_AUTO_MUTE
                    AND pnl_total < PNL_THRESHOLD_AUTO_MUTE
                    → add to muted dict avec reason="wallet_perf_negative"

Stat : élimination alpha decay individuel (un wallet validé Bonferroni
peut perdre son edge avec le temps). Filtre observabilité-driven.

Persistence : JSON dans data/paper/wallet_perf.json (similaire muted_wallets.json).
"""
from __future__ import annotations

import json
import time
from collections import deque
from pathlib import Path
from typing import Any


class WalletPerformanceTracker:
    """Track per-wallet stats + auto-mute si perf négatif soutenu."""

    # Thresholds auto-mute (Phase A4 — copy-trading quant standard)
    MIN_LOSSES_FOR_AUTO_MUTE = 5
    PNL_THRESHOLD_AUTO_MUTE = -1.0  # USD : si pnl < -$1 avec >=5 losses → mute
    ROLLING_WINDOW = 30  # last 30 closes

    def __init__(self, persist_path: Path | None = None, verbose: bool = True):
        self.persist_path = persist_path
        self.verbose = verbose
        # Par wallet : dict avec n_wins, n_losses, pnl_total, rolling, last_update_ts
        self._stats: dict[str, dict] = {}
        if persist_path and persist_path.exists():
            try:
                data = json.loads(persist_path.read_text())
                # Reconstruct deques (JSON ne sérialize pas deque)
                for w, s in data.items():
                    rolling = s.pop("rolling", [])
                    s["rolling"] = deque(rolling, maxlen=self.ROLLING_WINDOW)
                    self._stats[w] = s
                if verbose:
                    print(f"[WALLET_PERF] loaded {len(self._stats)} wallets "
                          f"from {persist_path}", flush=True)
            except Exception as e:
                print(f"[WALLET_PERF] load fail: {type(e).__name__}: {e}",
                      flush=True)

    def update(self, wallet: str, net_pnl: float) -> dict:
        """Update stats après un close. Retourne le dict stats du wallet."""
        key = wallet.lower()
        if key not in self._stats:
            self._stats[key] = {
                "n_wins": 0, "n_losses": 0, "pnl_total": 0.0,
                "rolling": deque(maxlen=self.ROLLING_WINDOW),
                "last_update_ts": 0.0,
            }
        s = self._stats[key]
        s["pnl_total"] += net_pnl
        s["rolling"].append(net_pnl)
        s["last_update_ts"] = time.time()
        if net_pnl > 0:
            s["n_wins"] += 1
        elif net_pnl < 0:
            s["n_losses"] += 1
        return s

    def should_auto_mute(self, wallet: str) -> tuple[bool, str]:
        """Returns (should_mute, reason)."""
        key = wallet.lower()
        s = self._stats.get(key)
        if s is None:
            return (False, "")
        if (s["n_losses"] >= self.MIN_LOSSES_FOR_AUTO_MUTE
            and s["pnl_total"] < self.PNL_THRESHOLD_AUTO_MUTE):
            return (True, f"wallet_perf_negative "
                          f"(n_losses={s['n_losses']}, "
                          f"pnl_total=${s['pnl_total']:.2f})")
        return (False, "")

    def persist(self):
        if self.persist_path is None:
            return
        try:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            # Sérialize : convert deque → list
            data = {}
            for w, s in self._stats.items():
                d = dict(s)
                d["rolling"] = list(s["rolling"])
                data[w] = d
            tmp = self.persist_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2))
            tmp.replace(self.persist_path)
        except Exception as e:
            print(f"[WALLET_PERF] persist fail: {type(e).__name__}: {e}",
                  flush=True)

    def get_stats(self, wallet: str) -> dict | None:
        return self._stats.get(wallet.lower())

    def summary(self) -> dict:
        """Retourne stats globaux pour observabilité."""
        n_wallets = len(self._stats)
        if n_wallets == 0:
            return dict(n_wallets=0)
        total_pnl = sum(s["pnl_total"] for s in self._stats.values())
        positive = sum(1 for s in self._stats.values() if s["pnl_total"] > 0)
        negative = sum(1 for s in self._stats.values() if s["pnl_total"] < 0)
        return dict(
            n_wallets=n_wallets,
            n_positive=positive,
            n_negative=negative,
            total_pnl_sum=total_pnl,
        )
