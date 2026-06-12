"""Tests pour liq_recorder.py — parsing du feed liquidations Binance forceOrder.

La logique PURE (message brut → LiqEvent, relecture JSONL, conversion temps) est
testée sans réseau. La connexion WS (record_stream) = couche I/O fine non testée ici.

Run: cd backend/edge_factory && ../../.venv/bin/python test_liq_recorder.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import liq_recorder as lr  # noqa: E402


def _msg(symbol="BTCUSDT", side="SELL", price="60000", qty="0.5", ts=1700000000000):
    return {"e": "forceOrder", "o": {"s": symbol, "S": side, "o": "LIMIT",
                                     "q": qty, "p": price, "ap": price,
                                     "X": "FILLED", "T": ts}}


def test_parse_sell_is_long_liquidation():
    ev = lr.parse_force_order(_msg(side="SELL"))
    assert ev["liquidated_side"] == "long"  # vente forcée = long liquidé
    assert ev["symbol"] == "BTCUSDT"
    assert abs(ev["notional"] - 30000.0) < 1e-6  # 60000 * 0.5


def test_parse_buy_is_short_liquidation():
    ev = lr.parse_force_order(_msg(side="BUY"))
    assert ev["liquidated_side"] == "short"


def test_parse_uses_avg_price_when_present():
    m = _msg(price="100")
    m["o"]["ap"] = "105"
    ev = lr.parse_force_order(m)
    assert ev["price"] == 105.0


def test_parse_rejects_malformed():
    assert lr.parse_force_order({}) is None
    assert lr.parse_force_order({"o": {}}) is None
    assert lr.parse_force_order({"o": {"s": "X", "q": "bad", "T": "x"}}) is None


def test_roundtrip_jsonl_and_load(tmp_path=None):
    d = tmp_path or tempfile.mkdtemp()
    path = os.path.join(str(d), "liq.jsonl")
    events = [lr.parse_force_order(_msg(symbol="BTCUSDT", ts=1700000000000)),
              lr.parse_force_order(_msg(symbol="ETHUSDT", ts=1700000001000)),
              lr.parse_force_order(_msg(symbol="BTCUSDT", ts=1700000002000))]
    with open(path, "w") as f:
        for e in events:
            f.write(lr.to_jsonl(e) + "\n")
    allev = lr.load_events(path)
    assert len(allev) == 3
    btc = lr.load_events(path, symbol="BTCUSDT")
    assert len(btc) == 2 and all(e["symbol"] == "BTCUSDT" for e in btc)


def test_event_times_seconds_relative_and_sorted():
    events = [{"ts": 1700000005000}, {"ts": 1700000000000}, {"ts": 1700000002000}]
    times = lr.event_times_seconds(events)
    assert times == [0.0, 2.0, 5.0]


def test_load_missing_file_returns_empty():
    assert lr.load_events("/nonexistent/path/xyz.jsonl") == []


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
