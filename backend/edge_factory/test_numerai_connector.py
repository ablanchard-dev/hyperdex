"""Tests pour numerai_connector.py — mapping signal interne → submission Numerai.

Format Numerai Signals (doc) : CSV index=ticker, colonne 'prediction' ∈ ]0,1[
EXCLUSIF, ≥100 tickers de l'univers, chaque ticker UNE fois. La logique pure =
rank-normaliser des scores arbitraires vers ]0,1[, dédupliquer, filtrer l'univers.
Testée sans réseau (l'upload via numerapi = couche I/O avec clés Numerai séparées).

Run: cd backend/edge_factory && ../../.venv/bin/python test_numerai_connector.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numerai_connector as nc  # noqa: E402


def test_rank_normalize_to_open_unit_interval():
    scores = {"A": -5.0, "B": 0.0, "C": 3.0, "D": 100.0}
    pred = nc.to_predictions(scores)
    # toutes strictement dans ]0,1[
    assert all(0.0 < v < 1.0 for v in pred.values()), pred
    # ordre préservé (rank) : A plus bas score -> plus petite pred ; D le plus haut
    assert pred["A"] < pred["B"] < pred["C"] < pred["D"]


def test_monotonic_rank_ignores_scale():
    # seule la RANG compte, pas l'échelle (robuste aux outliers)
    a = nc.to_predictions({"X": 1.0, "Y": 2.0, "Z": 3.0})
    b = nc.to_predictions({"X": 1.0, "Y": 2.0, "Z": 1000.0})
    assert a == b  # mêmes rangs -> mêmes predictions


def test_dedup_keeps_one_per_ticker():
    # un dict ne peut pas dupliquer, mais on teste la garantie via la sortie CSV
    scores = {"A": 1.0, "B": 2.0, "C": 3.0}
    csv = nc.to_csv(scores)
    tickers = [line.split(",")[0] for line in csv.strip().splitlines()[1:]]
    assert len(tickers) == len(set(tickers))


def test_filter_universe():
    scores = {"AAPL": 1.0, "FAKE": 2.0, "MSFT": 3.0}
    universe = {"AAPL", "MSFT", "GOOG"}
    pred = nc.to_predictions(scores, universe=universe)
    assert set(pred) == {"AAPL", "MSFT"}  # FAKE retiré, GOOG absent des scores


def test_csv_format_header_and_values():
    csv = nc.to_csv({"AAPL": 1.0, "MSFT": 2.0})
    lines = csv.strip().splitlines()
    assert lines[0] == "ticker,prediction"
    # chaque ligne : ticker,float dans ]0,1[
    for line in lines[1:]:
        t, p = line.split(",")
        assert 0.0 < float(p) < 1.0


def test_validate_rejects_too_few_tickers():
    ok, msg = nc.validate_submission({f"T{i}": 0.5 for i in range(50)})
    assert ok is False and "100" in msg


def test_validate_accepts_valid_submission():
    pred = nc.to_predictions({f"T{i}": float(i) for i in range(150)})
    ok, msg = nc.validate_submission(pred)
    assert ok is True, msg


def test_validate_rejects_out_of_range():
    ok, msg = nc.validate_submission({f"T{i}": 0.5 for i in range(150)} | {"BAD": 1.0})
    assert ok is False  # 1.0 n'est pas dans ]0,1[ exclusif


def test_deterministic():
    s = {f"T{i}": (i * 7) % 13 for i in range(120)}
    assert nc.to_csv(s) == nc.to_csv(s)


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
