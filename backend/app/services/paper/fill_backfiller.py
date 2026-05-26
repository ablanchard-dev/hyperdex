"""FillBackfiller — récupère les fills manqués via WS subscriptions.

Le WS HL drop silencieusement ~90% des subs (audit 2026-05-26 : 7/10 dormants
en fait actifs, 362 fills manqués sur 10 wallets en 31h).

Ce service tourne en parallèle du WS listener et :
  1. Toutes les 60s, pick N wallets (oldest last-polled first)
  2. Fetch user_fills_by_time(addr, start_time=last_seen_ts[addr])
  3. Filter dedup via tid (avoid replay si WS déjà reçu)
  4. Dispatch en time-ASC vers orchestrator.on_trader_fill (qui dedup encore via
     PnLTracker.has_open / tracker.get pour idempotence open/close)

Rate-limit budget HL : 1200 weight/min, info=20/req → max 60 req/min.
Avec 232 wallets et N=58/loop, full cycle ≈ 232/58 × 60s = 4min, soit
58 × 20 = 1160 weight/min, bien sous 1200 budget.

Persistance state JSON à PAPER_DIR/fill_backfiller_state.json pour resume.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict, deque
from pathlib import Path
from typing import Any

from hyperliquid.info import Info


class FillBackfiller:
    """Periodic backfiller catching fills missed by WS subscriptions."""

    # Rate-limit prudent : 58 wallets × 20 weight = 1160/min, sous budget 1200
    DEFAULT_BATCH_PER_LOOP = 58
    DEFAULT_LOOP_INTERVAL_S = 60.0
    DEFAULT_INITIAL_LOOKBACK_S = 300  # 5min back at boot
    DEFAULT_SEEN_TIDS_LRU = 200       # avoid memory growth per wallet

    def __init__(
        self,
        addresses: list[str],
        orchestrator: Any,           # CopyOrchestrator with on_trader_fill
        info: Info,
        state_path: Path,
        batch_per_loop: int = DEFAULT_BATCH_PER_LOOP,
        loop_interval_s: float = DEFAULT_LOOP_INTERVAL_S,
        initial_lookback_s: int = DEFAULT_INITIAL_LOOKBACK_S,
        verbose: bool = True,
    ):
        self.addresses = [a.lower() for a in addresses]
        self.orchestrator = orchestrator
        self.info = info
        self.state_path = state_path
        self.batch_per_loop = batch_per_loop
        self.loop_interval_s = loop_interval_s
        self.initial_lookback_s = initial_lookback_s
        self.verbose = verbose

        # State : last successful fetch ts per addr (ms), and last_polled ts per addr
        # last_seen_ts[addr] = max(fill.time) we've already dispatched
        # last_polled_ts[addr] = when we last hit the API for this wallet
        self.last_seen_ts: dict[str, int] = {}
        self.last_polled_ts: dict[str, float] = {}
        self.seen_tids: dict[str, deque[int]] = {}

        # Stats
        self.stats: dict[str, int] = dict(
            loops=0, wallets_polled=0,
            fills_fetched=0, fills_dispatched=0, fills_skipped_tid_seen=0,
            fills_skipped_old=0, api_errors=0,
        )
        self._stop = asyncio.Event()
        self._load_state()

    def _log(self, msg: str):
        if self.verbose:
            print(f"[BACKFILL] {msg}", flush=True)

    def _load_state(self):
        if not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text())
            self.last_seen_ts = {k.lower(): int(v) for k, v in
                                 data.get("last_seen_ts", {}).items()}
            self._log(f"loaded state : {len(self.last_seen_ts)} wallets")
        except Exception as e:
            self._log(f"state load fail : {type(e).__name__} — fresh start")

    def _save_state(self):
        try:
            self.state_path.write_text(json.dumps({
                "last_seen_ts": self.last_seen_ts,
                "stats": self.stats,
            }))
        except Exception as e:
            self._log(f"state save fail : {type(e).__name__}")

    def _pick_next_batch(self) -> list[str]:
        """Pick N wallets least recently polled (round-robin staggered)."""
        now = time.time()
        # Tri ASC par last_polled_ts (jamais polled = 0 = priority)
        sorted_addrs = sorted(
            self.addresses,
            key=lambda a: self.last_polled_ts.get(a, 0.0),
        )
        return sorted_addrs[:self.batch_per_loop]

    async def _process_wallet(self, addr: str):
        """Fetch new fills for wallet, dispatch ASC to orchestrator."""
        now_ms = int(time.time() * 1000)
        # start_time : last_seen ou (now - initial_lookback) au boot
        start_time = self.last_seen_ts.get(
            addr, now_ms - self.initial_lookback_s * 1000
        )
        try:
            # Note : user_fills_by_time est sync mais SDK gère localement.
            # On l'execute dans un thread pour pas bloquer l'event loop.
            fills = await asyncio.to_thread(
                self.info.user_fills_by_time, addr, start_time=start_time
            )
        except Exception as e:
            self.stats["api_errors"] += 1
            self._log(f"API err {addr[:14]}: {type(e).__name__}: {str(e)[:60]}")
            self.last_polled_ts[addr] = time.time()
            return

        self.last_polled_ts[addr] = time.time()
        if not fills:
            return

        self.stats["fills_fetched"] += len(fills)

        # Sort ASC by time (HL renvoie reverse-chrono)
        fills_sorted = sorted(fills, key=lambda f: int(f.get("time", 0)))

        # Setup tid LRU pour ce wallet
        tids = self.seen_tids.setdefault(addr, deque(maxlen=self.DEFAULT_SEEN_TIDS_LRU))
        tid_set = set(tids)

        max_time = self.last_seen_ts.get(addr, 0)
        for fill in fills_sorted:
            ts = int(fill.get("time", 0))
            if ts <= 0:
                continue
            # Skip si déjà vu (par tid OU par ts <= last_seen)
            tid = int(fill.get("tid", 0))
            if tid > 0 and tid in tid_set:
                self.stats["fills_skipped_tid_seen"] += 1
                continue
            if ts < self.last_seen_ts.get(addr, 0):
                # Cas border : fill plus vieux que notre last_seen → on garde
                # le tid pour tracking mais on ne dispatche pas (déjà géré).
                self.stats["fills_skipped_old"] += 1
                continue
            # Dispatch via orchestrator (qui dedup encore via tracker.has_open)
            try:
                await self.orchestrator.on_trader_fill(addr, fill)
                self.stats["fills_dispatched"] += 1
                if tid > 0:
                    tids.append(tid)
                    tid_set.add(tid)
                max_time = max(max_time, ts)
            except Exception as e:
                self._log(f"dispatch err {addr[:14]}: {type(e).__name__}: {str(e)[:60]}")

        if max_time > self.last_seen_ts.get(addr, 0):
            self.last_seen_ts[addr] = max_time

    async def run(self):
        """Main loop : every loop_interval_s, process batch."""
        self._log(f"start : {len(self.addresses)} wallets, batch={self.batch_per_loop}, "
                  f"interval={self.loop_interval_s}s, full_cycle≈"
                  f"{len(self.addresses)/self.batch_per_loop * self.loop_interval_s/60:.1f}min")
        loop_idx = 0
        while not self._stop.is_set():
            loop_start = time.time()
            self.stats["loops"] += 1
            batch = self._pick_next_batch()
            self.stats["wallets_polled"] += len(batch)

            # Process en parallèle (concurrency limitée car async sur thread)
            sem = asyncio.Semaphore(8)

            async def _bounded(addr):
                async with sem:
                    await self._process_wallet(addr)

            await asyncio.gather(*[_bounded(a) for a in batch],
                                 return_exceptions=True)

            # Save state toutes les 5 loops
            loop_idx += 1
            if loop_idx % 5 == 0:
                self._save_state()
                self._log(f"loop #{self.stats['loops']} stats={self.stats}")

            elapsed = time.time() - loop_start
            sleep_for = max(0, self.loop_interval_s - elapsed)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=sleep_for)
            except asyncio.TimeoutError:
                pass

        self._save_state()
        self._log(f"stopped — final stats={self.stats}")

    def stop(self):
        self._stop.set()
