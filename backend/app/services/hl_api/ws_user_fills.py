"""WS listener pour userFills HL avec watchdog data-silence et SHARDING.

Subscribe à userFills pour N addresses via PLUSIEURS Info instances (shards).
Empiriquement, HL limite à ~15 subscriptions par window 60s par IP/connection.
Pour couvrir des cohortes >15 wallets, on shard sur N Info instances avec
60s de cooldown entre shards (= bootstrap progressif).

Watchdog 90s : si aucun event reçu globalement → reconnect tous les shards.
Filtre per-wallet via _last_fill_time : ni perte ni dup au reconnect.

Architecture :
  WS shard1 (≤15 wallets) ─┐
  WS shard2 (≤15 wallets) ─┼─→ sync queue partagée → asyncio drainer → on_fill
  WS shardN (≤15 wallets) ─┘
"""
from __future__ import annotations

import asyncio
import math
import queue
import threading
import time
from typing import Awaitable, Callable

from hyperliquid.info import Info
from hyperliquid.utils import constants


OnFillAsync = Callable[[str, dict], Awaitable[None]]


class WsUserFillsListener:
    """Subscribe à userFills pour N wallets via sharding, dispatch async."""

    # HL limit officielle 2026-05-26 (audit) :
    #   - 10 connexions WS max / IP
    #   - 10 unique users / connexion
    # Donc plafond effectif = 10 × 10 = 100 wallets/IP en WS.
    # Avec cohort 232 → split HOT (≤100 WS) + WARM (REST via backfiller).
    # Source : https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits
    SHARD_SIZE = 10                       # 10 users / connexion (limite HL)
    MAX_SHARDS_PER_IP = 10                # 10 conn / IP (limite HL)
    COOLDOWN_BETWEEN_SHARDS_S = 60.0

    # Watchdog GLOBAL (sécurité) : si TOUS shards silent → full reboot.
    WATCHDOG_SILENCE_S = 1800.0  # 30 min : juste safety net, per-shard fait le job
    WATCHDOG_TICK_S = 30.0

    # Watchdog PER-SHARD : détecte shard mort sans casser les autres.
    # SDK hyperliquid ne re-subscribe pas au TCP reconnect → un shard "Expired"
    # = silencieux pour toujours. Polyoracle gère lui-même les re-subscribes,
    # nous on doit faire pareil par-shard.
    SHARD_SILENCE_S = 180.0  # 3 min sans event sur un shard ayant déjà reçu = mort
    SHARD_MIN_EVENTS_FOR_DEAD_DETECTION = 1  # le shard doit avoir reçu au moins 1 event

    def __init__(self, addresses: list[str], on_fill: OnFillAsync,
                 testnet: bool = False, ignore_initial_snapshot: bool = True):
        # Safety : truncate à MAX_SHARDS_PER_IP × SHARD_SIZE pour respecter
        # limite HL (sinon les wallets > 100 sont silencieusement droppés).
        max_wallets = self.MAX_SHARDS_PER_IP * self.SHARD_SIZE
        if len(addresses) > max_wallets:
            print(f"[WS] WARNING : {len(addresses)} wallets demandés > "
                  f"max_wallets={max_wallets} (HL limit 10 conn × 10 users/IP). "
                  f"Truncate aux {max_wallets} premiers (= HOT tier responsabilité "
                  f"appelant).", flush=True)
            addresses = addresses[:max_wallets]
        self.addresses = [a.lower() for a in addresses]
        self.on_fill = on_fill
        self.testnet = testnet
        self.ignore_initial = ignore_initial_snapshot
        self._sync_queue: queue.Queue = queue.Queue(maxsize=10000)
        self._shards: list[dict] = []  # [{info, addrs, last_event_ts}, ...]
        self._stop = threading.Event()
        self._last_event_ts = time.time()
        self._start_ms = int(time.time() * 1000)
        # last fill time vu PAR WALLET (ms). Floor = _start_ms.
        self._last_fill_time: dict[str, int] = {}
        self._reconnect_count = 0
        self._dispatched_count = 0
        self._skipped_old = 0
        self._bootstrap_done = False

    # ----- WS subscription (sync, called from WS thread) -----

    def _make_cb(self, addr: str, shard_idx: int):
        def cb(msg):
            now = time.time()
            self._last_event_ts = now
            # Update per-shard timestamp + flag "ever received" pour
            # déterminer si un shard est vraiment mort (vs jamais vu d'event).
            if 0 <= shard_idx < len(self._shards):
                self._shards[shard_idx]["last_event_ts"] = now
                self._shards[shard_idx]["events_count"] = (
                    self._shards[shard_idx].get("events_count", 0) + 1)
            try:
                self._sync_queue.put_nowait((addr, msg))
            except queue.Full:
                pass  # drop old, don't block WS thread
        return cb

    def _connect_one_shard(self, shard_idx: int, shard_addrs: list[str]) -> dict:
        """Crée 1 Info instance + subscribe ses addresses. Retourne dict shard.
        Pré-alloue le slot dans self._shards pour que make_cb puisse y écrire."""
        # Ensure slot exists in self._shards before subscribes fire callbacks
        while len(self._shards) <= shard_idx:
            self._shards.append({
                "info": None, "addrs": [], "ok": 0,
                "last_event_ts": time.time(), "events_count": 0,
            })
        api = (constants.TESTNET_API_URL if self.testnet
               else constants.MAINNET_API_URL)
        info = Info(api, skip_ws=False)
        ok = 0
        for addr in shard_addrs:
            try:
                info.subscribe(
                    {"type": "userFills", "user": addr},
                    self._make_cb(addr, shard_idx),
                )
                ok += 1
            except Exception as e:
                print(f"[WS sub FAIL] shard{shard_idx} {addr[:14]}: {e}",
                      flush=True)
        print(f"[WS] shard {shard_idx}: {ok}/{len(shard_addrs)} subscribed",
              flush=True)
        # Update slot avec instance créée
        self._shards[shard_idx] = {
            "info": info, "addrs": shard_addrs, "ok": ok,
            "last_event_ts": time.time(), "events_count": 0,
        }
        return self._shards[shard_idx]

    def _disconnect_all(self):
        for shard in self._shards:
            try:
                shard["info"].disconnect_websocket()
            except Exception:
                pass
        self._shards = []

    # ----- bootstrap progressif (async, non-bloquant) -----

    async def _async_bootstrap(self):
        """Bootstrap N shards avec cooldown 60s entre chaque.
        Lance progressivement, ne bloque pas le drainer/watchdog.
        Note : _connect_one_shard pré-alloue le slot dans self._shards."""
        self._shards = []  # reset au début (boot ou full reconnect)
        n_shards = math.ceil(len(self.addresses) / self.SHARD_SIZE)
        loop = asyncio.get_event_loop()
        for shard_idx in range(n_shards):
            start = shard_idx * self.SHARD_SIZE
            end = start + self.SHARD_SIZE
            shard_addrs = self.addresses[start:end]
            await loop.run_in_executor(
                None, self._connect_one_shard, shard_idx, shard_addrs)
            if shard_idx < n_shards - 1:
                print(f"[WS] cooldown {self.COOLDOWN_BETWEEN_SHARDS_S:.0f}s "
                      f"avant shard {shard_idx+1}/{n_shards}...", flush=True)
                await asyncio.sleep(self.COOLDOWN_BETWEEN_SHARDS_S)
        self._bootstrap_done = True
        ok_total = sum(s["ok"] for s in self._shards)
        print(f"[WS] bootstrap COMPLET : {len(self._shards)} shards, "
              f"{ok_total}/{len(self.addresses)} wallets subscribés",
              flush=True)

    async def _reconnect_one_shard(self, shard_idx: int):
        """Reconnect 1 shard isolé sans toucher aux autres. Pas de full reboot.
        ~3s downtime (disconnect + 15 subscribes consécutifs, pas de cooldown
        car les autres shards continuent à occuper le quota IP)."""
        if shard_idx < 0 or shard_idx >= len(self._shards):
            return
        shard = self._shards[shard_idx]
        addrs = list(shard["addrs"])
        events_before = shard.get("events_count", 0)
        print(f"[WS_SHARD_RECONNECT] shard {shard_idx} silent → reconnect "
              f"(prev events={events_before})", flush=True)
        # Disconnect old Info
        try:
            if shard["info"] is not None:
                shard["info"].disconnect_websocket()
        except Exception:
            pass
        # Rebuild
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, self._connect_one_shard, shard_idx, addrs)
        # Reset events_count car nouvelle WS
        self._shards[shard_idx]["events_count"] = 0
        self._shards[shard_idx]["last_event_ts"] = time.time()
        print(f"[WS_SHARD_RECONNECT] shard {shard_idx} reconnected",
              flush=True)

    async def _async_reconnect(self):
        """Reconnect : disconnect tous + re-bootstrap progressif."""
        self._reconnect_count += 1
        print(f"[WS_WATCHDOG] reconnect #{self._reconnect_count} "
              f"({len(self._shards)} shards à rebuild)", flush=True)
        self._disconnect_all()
        self._bootstrap_done = False
        self._last_event_ts = time.time()  # reset pendant le rebuild
        await asyncio.sleep(1.5)
        await self._async_bootstrap()

    # ----- async drainer + watchdog -----

    async def _drainer(self):
        loop = asyncio.get_event_loop()
        while not self._stop.is_set():
            try:
                item = await loop.run_in_executor(
                    None, self._sync_queue.get, True, 0.5)
            except queue.Empty:
                continue
            except Exception:
                continue
            addr, msg = item
            try:
                data = msg.get("data") if isinstance(msg, dict) else None
                if not data:
                    continue
                fills = data.get("fills", [])
                # Filtre per-wallet uniforme : dispatch si fts strictement >
                # dernier vu pour ce wallet (floor = _start_ms initial).
                # CRITIQUE : HL envoie userFills reverse-chronological (newest
                # first). On TRIE ascending avant dispatch sinon CLOSE
                # arriverait avant OPEN → tracker.get()=None → close raté.
                floor = self._last_fill_time.get(addr, self._start_ms)
                fills_sorted = []
                for f in fills:
                    if not isinstance(f, dict):
                        continue
                    try:
                        fts = int(f.get("time", 0))
                    except Exception:
                        continue
                    fills_sorted.append((fts, f))
                fills_sorted.sort(key=lambda x: x[0])
                max_fts = floor
                for fts, f in fills_sorted:
                    if fts <= floor:
                        self._skipped_old += 1
                        continue
                    await self._safe_call(addr, f)
                    self._dispatched_count += 1
                    if fts > max_fts:
                        max_fts = fts
                if max_fts > floor:
                    self._last_fill_time[addr] = max_fts
            except Exception as e:
                print(f"[drainer err] {type(e).__name__}: {e}", flush=True)

    async def _safe_call(self, addr: str, fill: dict):
        try:
            await self.on_fill(addr, fill)
        except Exception as e:
            print(f"[on_fill err] {addr[:14]} {type(e).__name__}: {e}",
                  flush=True)

    async def _watchdog(self):
        """Watchdog 2-niveaux :
        - Per-shard : si shard ayant déjà reçu events stagne >SHARD_SILENCE_S
          → reconnect just ce shard (~3s downtime).
        - Global : si TOUS shards silent >WATCHDOG_SILENCE_S → full reboot
          (sécurité, normalement jamais atteint avec per-shard).
        """
        while not self._stop.is_set():
            await asyncio.sleep(self.WATCHDOG_TICK_S)
            if not self._bootstrap_done:
                continue
            now = time.time()
            # PER-SHARD : reconnect ciblé pour les shards morts
            for shard_idx in range(len(self._shards)):
                shard = self._shards[shard_idx]
                if shard.get("events_count", 0) < self.SHARD_MIN_EVENTS_FOR_DEAD_DETECTION:
                    # Shard jamais reçu d'event = wallets dormants, on touche pas
                    continue
                silence = now - shard["last_event_ts"]
                if silence > self.SHARD_SILENCE_S:
                    try:
                        await self._reconnect_one_shard(shard_idx)
                    except Exception as e:
                        print(f"[WS_SHARD_RECONNECT] shard {shard_idx} "
                              f"failed: {type(e).__name__}: {str(e)[:80]}",
                              flush=True)
            # GLOBAL (sécurité ultime) : si on ne reçoit RIEN depuis 30 min
            global_silence = now - self._last_event_ts
            if global_silence > self.WATCHDOG_SILENCE_S:
                print(f"[WS_WATCHDOG] global silence {global_silence:.0f}s > "
                      f"{self.WATCHDOG_SILENCE_S}s → full reboot", flush=True)
                try:
                    await self._async_reconnect()
                except Exception as e:
                    print(f"[WS_WATCHDOG] full reboot failed: "
                          f"{type(e).__name__}: {str(e)[:100]} — back off 90s",
                          flush=True)
                    self._last_event_ts = time.time()
                    await asyncio.sleep(90)

    async def _stats_logger(self):
        while not self._stop.is_set():
            await asyncio.sleep(60)
            n_shards = len(self._shards)
            ok_total = sum(s["ok"] for s in self._shards) if self._shards else 0
            # Per-shard activity : nombre de shards actifs (events_count>0)
            now = time.time()
            active_shards = sum(
                1 for s in self._shards
                if s.get("events_count", 0) > 0
                and (now - s.get("last_event_ts", 0)) < 300)
            print(f"[WS_STATS] shards={n_shards}/{ok_total}/"
                  f"{len(self.addresses)} "
                  f"active_shards={active_shards} "
                  f"dispatched={self._dispatched_count} "
                  f"skipped_old={self._skipped_old} "
                  f"reconnects={self._reconnect_count} "
                  f"queue_depth={self._sync_queue.qsize()} "
                  f"last_event_age={time.time()-self._last_event_ts:.0f}s "
                  f"bootstrap={'DONE' if self._bootstrap_done else 'IN_PROGRESS'}",
                  flush=True)

    # ----- API publique -----

    async def run(self):
        print(f"[WS] starting sharded listener : {len(self.addresses)} wallets "
              f"in ~{math.ceil(len(self.addresses)/self.SHARD_SIZE)} shards "
              f"of {self.SHARD_SIZE} wallets, "
              f"cooldown {self.COOLDOWN_BETWEEN_SHARDS_S:.0f}s between "
              f"({'testnet' if self.testnet else 'mainnet'})", flush=True)
        # Lance drainer + watchdog + stats EN PARALLÈLE du bootstrap
        # → shards qui sont up commencent à dispatcher tout de suite.
        drainer_task = asyncio.create_task(self._drainer())
        watchdog_task = asyncio.create_task(self._watchdog())
        stats_task = asyncio.create_task(self._stats_logger())
        bootstrap_task = asyncio.create_task(self._async_bootstrap())
        try:
            await asyncio.gather(
                drainer_task, watchdog_task, stats_task, bootstrap_task,
            )
        finally:
            self._disconnect_all()

    def stop(self):
        self._stop.set()
        self._disconnect_all()
