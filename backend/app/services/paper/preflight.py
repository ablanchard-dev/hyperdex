"""Preflight health check au boot du bot HyperDex.

Valide les pré-requis critiques avant de lancer le WS listener :
1. Hyperliquid API reachable (info.meta() responds)
2. Cohort CSV existe + readable
3. Muted wallets JSON loadable (si présent)
4. Funding rate API marche (meta_and_asset_ctxs)
5. l2_snapshot fonctionne sur 1 coin sample
6. user_state (REST) marche sur 1 wallet de la cohorte

Si fail critique → raise RuntimeError, le launcher abort avant WS start.
Si fail non-critique → log warning, continue.

Évite les surprises au runtime (ex: VPN coupé, API down, fichiers manquants).
"""
from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import Any


class PreflightError(RuntimeError):
    """Erreur critique au preflight — bot ne doit pas démarrer."""


def _check(label: str, ok: bool, detail: str = "", critical: bool = True) -> bool:
    marker = "✓" if ok else ("✗" if critical else "⚠")
    print(f"[PREFLIGHT] {marker} {label}: {detail}", flush=True)
    return ok


def run_preflight(
    info: Any,
    cohort_csv: Path,
    muted_path: Path | None = None,
    wallet_perf_path: Path | None = None,
    sample_coin: str = "BTC",
    timeout_per_check_s: float = 10.0,
) -> bool:
    """Retourne True si tout OK, sinon raise PreflightError sur critique.

    Returns True/False pour status global.
    """
    print("[PREFLIGHT] === HyperDex boot health check ===", flush=True)
    all_ok = True
    failures = []

    # 1. HL API meta() reachable
    try:
        meta = info.meta()
        n_universe = len(meta.get("universe", []) or [])
        ok = n_universe > 0
        _check("HL API meta()", ok, f"{n_universe} assets in universe")
        if not ok:
            all_ok = False
            failures.append("HL meta returned empty universe")
    except Exception as e:
        _check("HL API meta()", False, f"{type(e).__name__}: {str(e)[:80]}")
        all_ok = False
        failures.append(f"HL meta unreachable: {type(e).__name__}")

    # 2. Cohort CSV
    if not cohort_csv.exists():
        _check("Cohort CSV", False, f"NOT FOUND at {cohort_csv}")
        all_ok = False
        failures.append(f"cohort_csv missing: {cohort_csv}")
        sample_wallet = None
    else:
        try:
            with open(cohort_csv) as f:
                rows = list(csv.DictReader(f))
            ok = len(rows) > 10
            _check("Cohort CSV", ok, f"{len(rows)} wallets loadable")
            if not ok:
                all_ok = False
                failures.append(f"cohort too small: {len(rows)}")
            sample_wallet = rows[0].get("addr") if rows else None
        except Exception as e:
            _check("Cohort CSV", False, f"{type(e).__name__}: {str(e)[:80]}")
            all_ok = False
            failures.append(f"cohort_csv read fail: {type(e).__name__}")
            sample_wallet = None

    # 3. Muted wallets JSON (non-critique si absent)
    if muted_path is not None:
        if muted_path.exists():
            try:
                data = json.loads(muted_path.read_text())
                _check("Muted wallets JSON", True, f"{len(data)} muted",
                       critical=False)
            except Exception as e:
                _check("Muted wallets JSON", False,
                       f"corrupt: {type(e).__name__}: {str(e)[:60]}",
                       critical=False)
        else:
            _check("Muted wallets JSON", True,
                   f"not yet present at {muted_path.name} (1st boot OK)",
                   critical=False)

    # 4. wallet_perf.json (non-critique si absent)
    if wallet_perf_path is not None and wallet_perf_path.exists():
        try:
            data = json.loads(wallet_perf_path.read_text())
            _check("Wallet perf JSON", True, f"{len(data)} tracked",
                   critical=False)
        except Exception as e:
            _check("Wallet perf JSON", False,
                   f"corrupt: {type(e).__name__}: {str(e)[:60]}",
                   critical=False)

    # 5. Funding rate API (meta_and_asset_ctxs)
    try:
        meta, ctxs = info.meta_and_asset_ctxs()
        n_ctxs = len(ctxs or [])
        has_funding = any(
            isinstance(c, dict) and "funding" in c for c in (ctxs or [])
        )
        ok = n_ctxs > 0 and has_funding
        _check("Funding API (meta_and_asset_ctxs)", ok,
               f"{n_ctxs} contexts, has_funding={has_funding}")
        if not ok:
            all_ok = False
            failures.append("funding API missing data")
    except Exception as e:
        _check("Funding API", False, f"{type(e).__name__}: {str(e)[:80]}")
        all_ok = False
        failures.append(f"funding API fail: {type(e).__name__}")

    # 6. l2_snapshot test
    try:
        book = info.l2_snapshot(sample_coin)
        levels = book.get("levels") or [[], []]
        ok = len(levels) >= 2 and len(levels[0]) > 0 and len(levels[1]) > 0
        depth = f"bids={len(levels[0])} asks={len(levels[1])}"
        _check(f"l2_snapshot({sample_coin})", ok, depth)
        if not ok:
            all_ok = False
            failures.append(f"l2_snapshot empty for {sample_coin}")
    except Exception as e:
        _check(f"l2_snapshot({sample_coin})", False,
               f"{type(e).__name__}: {str(e)[:80]}")
        all_ok = False
        failures.append(f"l2_snapshot fail: {type(e).__name__}")

    # 7. user_state pour un wallet de la cohorte
    if sample_wallet:
        try:
            state = info.user_state(sample_wallet)
            ok = isinstance(state, dict) and "assetPositions" in state
            _check(f"user_state(sample wallet)", ok,
                   f"{len(state.get('assetPositions', []))} positions")
            if not ok:
                all_ok = False
                failures.append("user_state schema unexpected")
        except Exception as e:
            _check(f"user_state(sample wallet)", False,
                   f"{type(e).__name__}: {str(e)[:80]}")
            all_ok = False
            failures.append(f"user_state fail: {type(e).__name__}")

    # 8. all_mids
    try:
        mids = info.all_mids()
        n = len(mids) if isinstance(mids, dict) else 0
        ok = n > 50
        _check("all_mids()", ok, f"{n} mid prices")
        if not ok:
            failures.append(f"all_mids returned {n} only")
    except Exception as e:
        _check("all_mids()", False, f"{type(e).__name__}: {str(e)[:80]}",
               critical=False)

    # Verdict
    print("[PREFLIGHT] " + "=" * 50, flush=True)
    if all_ok:
        print("[PREFLIGHT] ✅ ALL CRITICAL CHECKS PASSED — boot proceeds",
              flush=True)
        return True
    else:
        print(f"[PREFLIGHT] ❌ CRITICAL FAILURES ({len(failures)}):",
              flush=True)
        for f in failures:
            print(f"[PREFLIGHT]   - {f}", flush=True)
        raise PreflightError(f"Preflight failed: {failures}")
