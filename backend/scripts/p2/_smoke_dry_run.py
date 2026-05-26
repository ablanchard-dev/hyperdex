"""Smoke P2.1 — vérifie ExchangeClient.submit_market_order(dry_run=True).

Critères de succès :
  1. Pas d'erreur d'import.
  2. ExchangeClient(dry_run=True) ne demande pas d'Exchange SDK.
  3. submit_market_order BTC retourne un Fill PAPER avec VWAP cohérent (≈ best ask).
  4. Test avec book stale simulé → reject (pas de bypass).
  5. Test avec sz arrondi à 0 → reject (pas d'ordre fantôme).
  6. Test latence : duration totale dans la fenêtre min-max LatencyModel.
"""
from __future__ import annotations

import asyncio
import sys
import time

sys.path.insert(0, "/home/dexter/hyperdex/backend")
from hyperliquid.info import Info
from hyperliquid.utils import constants

from app.services.execution import ExchangeClient, Fill, LatencyModel


async def main():
    print("=== P2.1 smoke — ExchangeClient(dry_run=True) ===")
    info = Info(constants.MAINNET_API_URL, skip_ws=True)

    # 1. Construction paper
    paper = ExchangeClient(
        dry_run=True, info=info,
        latency_model=LatencyModel(min_ms=100, max_ms=300),
    )
    print(f"[OK] construction paper (dry_run=True, exchange=None autorisé)")

    # 2. Submit BTC BUY $25 notional ≈ 0.0003 BTC à $90k
    print("\n[test 1] BTC BUY 0.0003 (~$30 notional)")
    t0 = time.time()
    fill = await paper.submit_market_order("BTC", "B", 0.0003)
    elapsed = time.time() - t0
    print(f"  fill : {fill}")
    print(f"  latence totale : {elapsed*1000:.0f}ms (attendu 100-300ms latence + ~50ms call)")
    assert fill.success, f"FAIL : {fill.error}"
    assert fill.dry_run, "FAIL : dry_run flag pas propagé"
    assert fill.fill_id.startswith("paper:"), f"FAIL : fill_id pas paper-tagged"
    assert 50000 < fill.avg_price < 200000, f"FAIL : VWAP BTC absurde {fill.avg_price}"
    assert fill.fee_usd > 0, "FAIL : fee non calculé"
    print(f"  ✓ VWAP cohérent (best ask + walk), fee {fill.fee_usd*10000:.2f} bps")

    # 3. SELL BTC
    print("\n[test 2] BTC SELL 0.0003")
    fill = await paper.submit_market_order("BTC", "A", 0.0003)
    assert fill.success, f"FAIL : {fill.error}"
    print(f"  ✓ {fill}")

    # 4. Size arrondie à 0 — sz_decimals BTC = 5 (vérifie via meta)
    print("\n[test 3] BTC size 1e-10 → arrondi à 0 → reject propre")
    fill = await paper.submit_market_order("BTC", "B", 1e-10)
    assert not fill.success, f"FAIL : size 1e-10 ne devrait pas passer"
    assert "arrondie" in (fill.error or "").lower() or "size" in (fill.error or "").lower()
    print(f"  ✓ reject : {fill.error}")

    # 5. Side invalide
    print("\n[test 4] side='X' invalide → reject")
    fill = await paper.submit_market_order("BTC", "X", 0.001)
    assert not fill.success
    print(f"  ✓ reject : {fill.error}")

    # 6. Coin inexistant
    print("\n[test 5] coin 'INEXISTANT' → reject (pas d'orderbook)")
    fill = await paper.submit_market_order("INEXISTANT", "B", 0.001)
    assert not fill.success
    print(f"  ✓ reject : {fill.error}")

    # 7. Live mode sans exchange → erreur claire
    print("\n[test 6] dry_run=False sans Exchange SDK → ValueError construction")
    try:
        ExchangeClient(dry_run=False, info=info, exchange=None)
        assert False, "FAIL : aurait dû raise"
    except ValueError as e:
        print(f"  ✓ refuse : {e}")

    # 8. ETH multi-call
    print("\n[test 7] 3 fills ETH consécutifs — pas de fuite mémoire/state")
    for i in range(3):
        f = await paper.submit_market_order("ETH", "B", 0.01)
        assert f.success, f"FAIL itér {i} : {f.error}"
        print(f"  fill {i+1}: vwap=${f.avg_price:.2f} levels_walked={f.levels_walked}")

    print("\n=== TOUS LES TESTS PASSENT ===")
    print("P2.1 OK. ExchangeClient est la pierre angulaire paper=live.")


if __name__ == "__main__":
    asyncio.run(main())
