"""Tests pour universe.py — l'univers LIVE tradeable HL (règle « univers = live »).

Fige le tradeable réel (perps liquides + contraintes réelles : tick/lot/min-notional/
frais/funding/spread) pour que TOUTE la recherche s'y contraigne. Sondé sur l'API HL :
meta = {name, szDecimals, maxLeverage}, ctx = {funding, dayNtlVlm, markPx, impactPxs}.
Frais HL : maker 1.5bps / taker 4.5bps, min $10 notionnel, funding horaire.

Run: cd backend/edge_factory && ../../.venv/bin/python test_universe.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import universe as U  # noqa: E402


def _meta_ctx():
    meta = {"universe": [
        {"name": "BTC", "szDecimals": 5, "maxLeverage": 40},
        {"name": "DOGE", "szDecimals": 0, "maxLeverage": 10},
        {"name": "ILLIQ", "szDecimals": 2, "maxLeverage": 5},
    ]}
    ctxs = [
        {"funding": "0.0000125", "dayNtlVlm": "2000000000", "markPx": "108952.0",
         "impactPxs": ["108948.0", "108960.0"]},
        {"funding": "-0.00003", "dayNtlVlm": "500000000", "markPx": "0.42",
         "impactPxs": ["0.4199", "0.4201"]},
        {"funding": "0.0001", "dayNtlVlm": "100000", "markPx": "5.0",  # illiquide
         "impactPxs": ["4.9", "5.1"]},
    ]
    return meta, ctxs


def test_build_filters_illiquid():
    meta, ctxs = _meta_ctx()
    univ = U.build_universe(meta, ctxs, min_dvol_usd=10_000_000)
    names = [p.name for p in univ]
    assert "BTC" in names and "DOGE" in names
    assert "ILLIQ" not in names  # 100k < 10M -> filtré


def test_sorted_by_liquidity_desc():
    meta, ctxs = _meta_ctx()
    univ = U.build_universe(meta, ctxs, min_dvol_usd=1_000_000)
    vols = [p.day_ntl_vlm for p in univ]
    assert vols == sorted(vols, reverse=True)  # BTC (2B) avant DOGE (500M)


def test_perp_carries_real_constraints():
    meta, ctxs = _meta_ctx()
    btc = next(p for p in U.build_universe(meta, ctxs, 1_000_000) if p.name == "BTC")
    assert btc.sz_decimals == 5
    assert btc.max_leverage == 40
    assert abs(btc.funding - 0.0000125) < 1e-12
    assert btc.mark_px == 108952.0


def test_min_order_size_respects_10usd():
    # min notional $10 -> taille mini = 10 / prix, arrondie au lot (szDecimals)
    assert abs(U.min_order_size(px=100.0, sz_decimals=2) - 0.1) < 1e-9  # 10/100=0.1
    # DOGE px 0.42 szDecimals 0 -> 10/0.42=23.8 -> arrondi lot 0 -> 24
    assert U.min_order_size(px=0.42, sz_decimals=0) == 24.0


def test_spread_bps_from_impact():
    # spread = (ask_impact - bid_impact) / mid * 1e4
    # BTC: (108960-108948)/108954 * 1e4 ≈ 1.1 bps
    sp = U.spread_bps(impact_pxs=["108948.0", "108960.0"])
    assert 0.5 < sp < 2.0, sp


def test_spread_bps_handles_missing():
    assert U.spread_bps(None) is None
    assert U.spread_bps(["100.0"]) is None  # une seule valeur


def test_fees_constants():
    # frais HL officiels (vérifiés) — source unique pour les hunters
    assert U.MAKER_BPS == 1.5
    assert U.TAKER_BPS == 4.5
    assert U.MIN_NOTIONAL_USD == 10.0
    assert U.FUNDING_INTERVAL_H == 1


def test_tradeable_names_and_constraints_map():
    meta, ctxs = _meta_ctx()
    univ = U.build_universe(meta, ctxs, 1_000_000)
    names = U.tradeable_names(univ)
    assert isinstance(names, list) and "BTC" in names
    cm = U.constraints_map(univ)
    assert cm["BTC"]["sz_decimals"] == 5
    assert "spread_bps" in cm["BTC"]


def test_default_granularity_is_executable():
    # granularité exécutable réaliste : 1h (latence HL 200-500ms, sub-min KO retail)
    assert U.EXECUTABLE_INTERVAL == "1h"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    fails = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            fails += 1
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:
            fails += 1
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - fails}/{len(fns)} passed")
    sys.exit(1 if fails else 0)
