"""Tests pour obi_recorder.py — record compact OBI (parsing pur, sans réseau)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import obi_recorder as rec  # noqa: E402


def _book(bid_sz, ask_sz, t=1000):
    return {"coin": "BTC", "time": t,
            "levels": [[{"px": "100.0", "sz": str(bid_sz), "n": 1}],
                       [{"px": "101.0", "sz": str(ask_sz), "n": 1}]]}


def test_record_compact_fields():
    r = rec.record_from_snapshot(_book(3.0, 1.0))
    assert r["coin"] == "BTC" and r["time"] == 1000
    assert r["obi"] > 0  # bid>ask
    assert abs(r["mid"] - 100.5) < 1e-9
    assert r["spread_bps"] > 0


def test_record_empty_is_none():
    assert rec.record_from_snapshot({"levels": [[], []]}) is None
    assert rec.record_from_snapshot({}) is None


def test_roundtrip_jsonl():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "obi.jsonl")
    recs = [rec.record_from_snapshot(_book(3.0, 1.0, t=1000)),
            rec.record_from_snapshot(_book(1.0, 3.0, t=2000))]
    with open(p, "w") as f:
        for r in recs:
            f.write(rec.to_jsonl(r) + "\n")
    loaded = rec.load_records(p)
    assert len(loaded) == 2
    assert loaded[0]["time"] == 1000


def test_load_missing_empty():
    assert rec.load_records("/nonexistent/xyz.jsonl") == []


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
