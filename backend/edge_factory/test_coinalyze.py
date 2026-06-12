"""Tests pour coinalyze.py — connecteur liquidations REST (parsing pur, sans réseau).

L'API Coinalyze renvoie par marché un 'history' array de bougies de liquidation :
typiquement {symbol, history:[{t:epoch_s, l:long_liq_usd, s:short_liq_usd}, ...]}.
On teste le PARSING (réponse JSON → events normalisés) sans clé ni réseau. Le fetch
HTTP réel (couche I/O) attend la clé API de l'opérateur.

Run: cd backend/edge_factory && ../../.venv/bin/python test_coinalyze.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import coinalyze as cz  # noqa: E402


def _resp(symbol="BTCUSDT_PERP.A"):
    return [{"symbol": symbol, "history": [
        {"t": 1700000000, "l": 1000000.0, "s": 50000.0},
        {"t": 1700000060, "l": 0.0, "s": 200000.0},
        {"t": 1700000120, "l": 30000.0, "s": 0.0},
    ]}]


def test_parse_returns_one_event_per_nonzero_side():
    # bougie 1 : long+short non nuls -> 2 events ; b2 : short seul -> 1 ; b3 : long -> 1
    events = cz.parse_liquidation_history(_resp())
    assert len(events) == 4, len(events)


def test_parse_long_short_sides_and_notional():
    events = cz.parse_liquidation_history(_resp())
    longs = [e for e in events if e["liquidated_side"] == "long"]
    shorts = [e for e in events if e["liquidated_side"] == "short"]
    assert len(longs) == 2 and len(shorts) == 2
    assert any(abs(e["notional"] - 1000000.0) < 1e-6 for e in longs)


def test_parse_timestamps_to_ms():
    events = cz.parse_liquidation_history(_resp())
    # t donné en SECONDES epoch -> stocké en ms (cohérent avec liq_recorder)
    assert all(e["ts"] % 1000 == 0 for e in events)
    assert min(e["ts"] for e in events) == 1700000000 * 1000


def test_parse_skips_zero_buckets():
    resp = [{"symbol": "X", "history": [{"t": 1, "l": 0.0, "s": 0.0}]}]
    assert cz.parse_liquidation_history(resp) == []


def test_hl_symbol_mapping():
    # liquidations HL natives sur Coinalyze = "{coin}.H" (exchange H = Hyperliquid)
    assert cz.hl_symbol("BTC") == "BTC.H"
    assert cz.hl_symbol("HYPE") == "HYPE.H"


def test_parse_oi_history_aligns_to_bars():
    # OI history Coinalyze = {symbol, history:[{t(epoch_s), o,h,l,c}]} → close aligné aux barres
    payload = [{"symbol": "BTC.H", "history": [
        {"t": 1700000000, "o": 1.0, "h": 1.1, "l": 0.9, "c": 1000.0},
        {"t": 1700003600, "o": 1.0, "h": 1.1, "l": 0.9, "c": 1100.0},
    ]}]
    bar_ts = [1700000000 * 1000, 1700003600 * 1000, 1700007200 * 1000]
    series = cz.parse_oi_history(payload, bar_ts)
    assert len(series) == 3            # une valeur par barre
    assert series[0] == 1000.0 and series[1] == 1100.0
    assert series[2] == 1100.0         # dernière OI connue propagée (ffill), pas de look-ahead


def test_parse_oi_history_empty():
    assert cz.parse_oi_history([], [1, 2]) == [0.0, 0.0]


def test_binance_liq_proxy_symbol():
    # Coinalyze n'a PAS les liq HL natives (vide) → proxy Binance ('A') : le signal
    # de liquidation est market-wide (même sous-jacent), exécution reste sur HL.
    assert cz.binance_liq_symbol("BTC") == "BTCUSDT_PERP.A"
    assert cz.binance_liq_symbol("HYPE") == "HYPEUSDT_PERP.A"


def test_parse_handles_empty_and_malformed():
    assert cz.parse_liquidation_history([]) == []
    assert cz.parse_liquidation_history([{"symbol": "X"}]) == []  # pas de history
    assert cz.parse_liquidation_history([{"history": [{"t": 1}]}]) == []  # pas l/s


def test_events_sorted_by_time_for_hawkes():
    # l'entrée du moteur Hawkes doit être triée temporellement
    events = cz.parse_liquidation_history(_resp())
    ts = [e["ts"] for e in events]
    assert ts == sorted(ts)


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
