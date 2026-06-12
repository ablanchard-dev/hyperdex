#!/usr/bin/env python3
"""TEST DÉCISIF non-linéarité Numerai — GBM vs ridge baseline (corr OOS 0.0003).

Le ridge linéaire plafonne (corr 0.0003) car il ne capte pas les INTERACTIONS. GBM
(arbres peu profonds boostés) les capture. Question : la non-linéarité dépasse-t-elle
le ridge ? Si corr GBM >> 0.0003 → le levier est confirmé → on scale + neutralise.

PRÉ-ENREGISTRÉ : 40k train échantillonné, 20 arbres depth 4 lr 0.1 min_leaf 50,
22 features numériques, target. Scoring corr Spearman OOS par era (numpy). Un run.
"""
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd

import gbm
import ridge as rg

TRAIN = "numerai_data/train.parquet"
VALID = "numerai_data/validation.parquet"
TARGET = "target"
N_TRAIN = 40000
N_TREES = 20
MAX_DEPTH = 4
LR = 0.1
MIN_LEAF = 50
SEED = 42


def main():
    print("[1/4] train...", flush=True)
    tr = pd.read_parquet(TRAIN)
    feat = [c for c in tr.columns
            if c.startswith("feature") and pd.api.types.is_numeric_dtype(tr[c])]
    tr = tr.dropna(subset=feat + [TARGET]).sample(n=N_TRAIN, random_state=SEED)
    X = tr[feat].values
    y = tr[TARGET].values
    print(f"   {len(X)} lignes × {len(feat)} features", flush=True)

    print(f"[2/4] fit GBM ({N_TREES} arbres depth {MAX_DEPTH})...", flush=True)
    import time
    t0 = time.perf_counter()
    model = gbm.fit(X, y, n_trees=N_TREES, max_depth=MAX_DEPTH, lr=LR, min_leaf=MIN_LEAF)
    print(f"   fit en {time.perf_counter()-t0:.0f}s", flush=True)

    print("[3/4] predict validation...", flush=True)
    va = pd.read_parquet(VALID, columns=feat + [TARGET, "date"]).dropna(subset=feat + [TARGET])
    preds = np.asarray(gbm.predict(model, va[feat].values))
    tgt = va[TARGET].values
    era_idx = [idx for idx in va.groupby("date").indices.values() if len(idx) >= 20]

    print("[4/4] corr OOS par era...", flush=True)
    corrs = [rg.spearman_fast(preds[idx], tgt[idx]) for idx in era_idx]
    mc = statistics.mean(corrs)
    sc = statistics.pstdev(corrs)
    sharpe = mc / sc if sc > 0 else 0.0
    pos = sum(1 for c in corrs if c > 0) / len(corrs)

    print(f"\n=== VERDICT NUMERAI GBM ({len(corrs)} eras OOS) ===", flush=True)
    print(f"   corr moyenne : {mc:+.5f}   (ridge baseline = +0.00030)", flush=True)
    print(f"   corr Sharpe  : {sharpe:+.3f}   (viser >0.5)", flush=True)
    print(f"   % eras > 0   : {pos*100:.1f}%", flush=True)
    gain = mc / 0.00030 if 0.00030 else 0
    print(f"   → GBM/ridge = {gain:.1f}× | "
          f"{'NON-LINÉARITÉ CONFIRMÉE, scaler+neutraliser' if mc > 0.003 else 'gain insuffisant — itérer params/depth'}",
          flush=True)


if __name__ == "__main__":
    main()
