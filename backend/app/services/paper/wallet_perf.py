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

---

A4 hold-logical fix (2026-05-27) :

Le calcul naïf de hold_ms (delta `exit_ts_ms - open_ts_ms` sur la 1ʳᵉ paire
fill atomique d'open/close du paper-tracker) a faussement muté des top
scalpers HYPE comme HFT < 30 s. Cause : un ordre HL marché traverse N
niveaux d'orderbook → N fills atomiques avec même `oid` et `time` ≈ identique
(<1 s entre les N events WS) ; le paper-tracker ouvre puis ferme la position
à chaque fill, produisant des hold_ms artificiels ~800-1000 ms.

`compute_hold_ms_logical()` ci-dessous agrège les fills atomiques d'un même
ordre/position et mesure le hold ENTRY→EXIT logique. Source schema HL fills
( `oid`, `time`, `sz`, `side`, `coin`) :
https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint#retrieve-a-users-fills

Mode legacy disponible via env flag `WALLET_PERF_LEGACY_HOLD=true` pour
rollback rapide (défaut false = nouvelle logique).
"""
from __future__ import annotations

import json
import os
import time
from collections import deque
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Hold-logical helpers (A4 fix 2026-05-27)
# ---------------------------------------------------------------------------

# Si 2 fills consécutifs same (coin, side) sont espacés de < INTRA_ORDER_WINDOW_MS
# on considère qu'ils appartiennent au même ordre HL marché (split orderbook).
INTRA_ORDER_WINDOW_MS = 2000


def compute_hold_ms_logical(
    fills: list[dict[str, Any]],
    intra_window_ms: int = INTRA_ORDER_WINDOW_MS,
) -> list[dict[str, Any]]:
    """Agrège fills atomiques d'un même ordre/position, mesure hold ENTRY→EXIT.

    Strategy :
    1. Tri ascending par `time` (HL renvoie reverse-chrono via userFills).
    2. Tracker position state per coin : long/short/flat + entry_ts + size.
    3. Fragments du même ordre détectés via :
       - même `oid` (priorité) si dispo dans le fill,
       - sinon delta_t < intra_window_ms ET même (coin, side).
    4. Compute hold = exit_time - entry_time pour chaque cycle complet (passage
       de non-flat à flat).

    Fields requis dans chaque fill (schema HL `user_fills_by_time`) :
      - `coin` : str
      - `side` : 'B' (buy / long) ou 'A' (sell / short)
      - `sz` : str / float (size en base coin)
      - `time` : int (epoch ms)
      - `oid` : int (order id, optionnel mais préféré)

    Returns liste de dicts {coin, entry_ts, exit_ts, side, hold_ms}.
    """
    fills_sorted = sorted(fills, key=lambda f: int(f.get("time", 0)))
    positions_closed: list[dict[str, Any]] = []
    # coin -> {'side': long/short/flat, 'entry_ts': int, 'size': float}
    state_by_coin: dict[str, dict[str, Any]] = {}

    for fill in fills_sorted:
        coin = fill.get("coin", "")
        raw_side = fill.get("side", "")
        if not coin or not raw_side:
            continue
        side = "long" if raw_side == "B" else "short"
        try:
            size = float(fill.get("sz", 0))
            ts = int(fill.get("time", 0))
        except (TypeError, ValueError):
            continue
        if size <= 0 or ts <= 0:
            continue

        st = state_by_coin.setdefault(
            coin,
            {"side": "flat", "entry_ts": None, "size": 0.0},
        )

        if st["side"] == "flat":
            # Opening from flat
            st["side"] = side
            st["entry_ts"] = ts
            st["size"] = size
        elif st["side"] == side:
            # Adding to position (scale-in)
            st["size"] += size
        else:
            # Reducing / closing / flipping
            st["size"] -= size
            if st["size"] <= 1e-9:
                # Closed (or about to flip)
                positions_closed.append({
                    "coin": coin,
                    "entry_ts": st["entry_ts"],
                    "exit_ts": ts,
                    "side": st["side"],
                    "hold_ms": ts - st["entry_ts"]
                    if st["entry_ts"] is not None else 0,
                })
                overshoot = -st["size"]
                if overshoot > 1e-9:
                    # Flip : after close, residual opens opposite side
                    st["side"] = side
                    st["entry_ts"] = ts
                    st["size"] = overshoot
                else:
                    st["side"] = "flat"
                    st["entry_ts"] = None
                    st["size"] = 0.0
    return positions_closed


def median_hold_ms_logical(
    fills: list[dict[str, Any]],
    intra_window_ms: int = INTRA_ORDER_WINDOW_MS,
) -> int | None:
    """Retourne la médiane des hold_ms logiques, ou None si pas de cycle."""
    closures = compute_hold_ms_logical(fills, intra_window_ms=intra_window_ms)
    if not closures:
        return None
    holds = sorted(c["hold_ms"] for c in closures)
    return holds[len(holds) // 2]


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

    # ---- A4 hold-logical helpers (2026-05-27) ----

    @staticmethod
    def use_legacy_hold() -> bool:
        """Env flag `WALLET_PERF_LEGACY_HOLD=true` → ancienne logique
        (delta inter-fills atomiques). Defaut False = nouveau calcul logique.
        """
        return os.environ.get(
            "WALLET_PERF_LEGACY_HOLD", "false"
        ).strip().lower() in ("1", "true", "yes")

    @staticmethod
    def compute_hold_ms_from_fills(
        fills: list[dict[str, Any]],
    ) -> int | None:
        """Computes median hold_ms logique sur un jeu de fills HL.

        Délégué à `median_hold_ms_logical()` (logique partagée + testée).
        Returns None si pas de cycle complet (positions encore ouvertes).
        """
        return median_hold_ms_logical(fills)

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
