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
from hyperliquid.utils.error import ClientError

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

    def _retry_429(self, fn, *args, attempts: int = 6) -> Any:
        """Appel pacé + backoff exponentiel sur 429 (rate limit HL)."""
        for i in range(attempts):
            self._pace()
            try:
                return fn(*args)
            except ClientError as e:
                if getattr(e, "status_code", None) == 429 and i < attempts - 1:
                    time.sleep(min(30, 2 ** i))  # 1,2,4,8,16,30s
                    continue
                raise

    def meta_and_asset_ctxs(self) -> Any:
        """[meta, ctxs] : universe perp + contexte par asset (dayNtlVlm, OI…)."""
        return self._retry_429(self._info.meta_and_asset_ctxs)

    def candles(self, name: str, interval: str,
                start_ms: int, end_ms: int) -> list[dict[str, Any]]:
        """Candles OHLCV (clés T/t/o/h/l/c/v en strings) pour un coin."""
        return self._retry_429(
            self._info.candles_snapshot, name, interval, start_ms, end_ms)

    def funding_history(self, name: str, start_ms: int,
                        end_ms: int | None = None) -> list[dict[str, Any]]:
        """Historique de funding (clés coin/fundingRate/premium/time) pour un coin."""
        return self._retry_429(self._info.funding_history, name, start_ms, end_ms)

    def funding_history_paged(self, name: str, start_ms: int, end_ms: int,
                              max_pages: int = 12) -> list[dict[str, Any]]:
        """funding_history paginé (HL cape ~500 pts/call, renvoie depuis start)."""
        out: list[dict[str, Any]] = []
        cur = start_ms
        for _ in range(max_pages):
            batch = self.funding_history(name, cur, end_ms)
            if not batch:
                break
            out.extend(batch)
            last = max(int(x["time"]) for x in batch)
            if len(batch) < 500 or last >= end_ms or last <= cur:
                break
            cur = last + 1
        return out
