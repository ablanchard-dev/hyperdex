"""HyperDex — wrapper Info API Hyperliquid.

Combine le SDK officiel pour les endpoints user_* avec un fetch HTTP direct
sur stats-data pour le leaderboard global (non exposé par le SDK). Pacing
intégré pour rester sous le budget 1200 poids/min (info=20 poids/req).
"""
from __future__ import annotations

import time
from typing import Any

import httpx
from hyperliquid.info import Info
from hyperliquid.utils import constants

LEADERBOARD_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
DEFAULT_MIN_INTERVAL_S = 1.5  # ~40 req/min en info = 800 poids/min, sous 1200/min


class InfoClient:
    def __init__(self, mainnet: bool = True,
                 min_interval_s: float = DEFAULT_MIN_INTERVAL_S) -> None:
        url = constants.MAINNET_API_URL if mainnet else constants.TESTNET_API_URL
        self._info = Info(url, skip_ws=True)
        self._last_call = 0.0
        self._min_interval = min_interval_s

    def _pace(self) -> None:
        dt = time.monotonic() - self._last_call
        if dt < self._min_interval:
            time.sleep(self._min_interval - dt)
        self._last_call = time.monotonic()

    def fetch_leaderboard(self) -> list[dict[str, Any]]:
        """Fetch le leaderboard global (jusqu'à ~37k traders)."""
        with httpx.Client(timeout=60) as c:
            r = c.get(LEADERBOARD_URL)
            r.raise_for_status()
            return r.json().get("leaderboardRows", [])

    def user_fills_by_time(
        self, address: str, start_ms: int, end_ms: int | None = None,
        max_fills: int = 10000,
    ) -> list[dict[str, Any]]:
        """Fetch fills with time-based pagination, up to max_fills."""
        all_fills: list[dict[str, Any]] = []
        cursor = start_ms
        for _ in range(40):  # cap pages
            self._pace()
            try:
                batch = self._info.user_fills_by_time(address, cursor, end_ms)
            except Exception:
                return all_fills
            if not batch:
                break
            all_fills.extend(batch)
            if len(batch) < 500 or len(all_fills) >= max_fills:
                break
            last_ts = max(int(f.get("time", 0)) for f in batch)
            if last_ts <= cursor:
                break
            cursor = last_ts + 1
        return all_fills

    def user_state(self, address: str) -> dict[str, Any]:
        self._pace()
        return self._info.user_state(address)
