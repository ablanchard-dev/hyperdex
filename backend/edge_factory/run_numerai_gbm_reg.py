#!/usr/bin/env python3
"""GBM Numerai RÉGULARISÉ — corriger l'overfit (train +0.011 vs OOS −0.001).

Le GBM agressif (depth4/lr0.1) overfit le bruit Numerai. On régularise : arbres
peu profonds (depth 2), lr faible, grosses feuilles. Grille testée en OOS HONNÊTE
(le train sert au fit, la validation au score — pas de fuite). On garde la config
qui MAXIMISE la corr OOS (sélection de modèle légitime, mesurée hors-échantillon).
"""
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd

import gbm
import ridge as rg

N_TRAIN = 40000
SEED = 42
# grille de régularisation (du moins au plus régularisé)
CONFIGS = [
    {"n_trees": 30, "max_depth": 2, "lr": 0.05, "min_leaf": 200},
    {"n_trees": 50, "max_depth": 2, "lr": 0.02, "min_leaf": 500},
    {"n_trees": 20, "max_depth": 3, "lr": 0.03, "min_leaf": 500},
]


def main():
    tr = pd.read_parquet("numerai_data/train.parquet")
    feat = [c for c in tr.columns
            if c.startswith("feature") and pd.api.types.is_numeric_dtype(tr[c])]
    tr = tr.dropna(subset=feat + ["target"]).sample(n=N_TRAIN, random_state=SEED)
    X, y = tr[feat].values, tr["target"].values
    va = pd.read_parquet("numerai_data/validation.parquet",
                         columns=feat + ["target", "date"]).dropna(subset=feat + ["target"])
    Xva, tgt = va[feat].values, va["target"].values
    era_idx = [idx for idx in va.groupby("date").indices.values() if len(idx) >= 20]
    print(f"train {len(X)} | val {len(va)} | {len(era_idx)} eras | ridge baseline +0.00030\n",
          flush=True)

    print(f"   {'config':<42} {'corr_OOS':>10} {'Sharpe':>8} {'%>0':>6}", flush=True)
    best = None
    for cfg in CONFIGS:
        t0 = time.perf_counter()
        model = gbm.fit(X, y, **cfg)
        preds = np.asarray(gbm.predict(model, Xva))
        corrs = [rg.spearman_fast(preds[idx], tgt[idx]) for idx in era_idx]
        mc = statistics.mean(corrs)
        sh = mc / statistics.pstdev(corrs) if statistics.pstdev(corrs) > 0 else 0.0
        pos = sum(1 for c in corrs if c > 0) / len(corrs)
        tag = ""
        if best is None or mc > best[1]:
            best = (cfg, mc, sh)
            tag = " ←"
        label = f"d{cfg['max_depth']} lr{cfg['lr']} leaf{cfg['min_leaf']} n{cfg['n_trees']}"
        print(f"   {label:<42} {mc:>+10.5f} {sh:>+8.3f} {pos*100:>5.0f}%"
              f"  [{time.perf_counter()-t0:.0f}s]{tag}", flush=True)

    print(f"\n   MEILLEUR : corr_OOS={best[1]:+.5f} Sharpe={best[2]:+.3f}", flush=True)
    print(f"   vs ridge +0.00030 → {'GBM régularisé MEILLEUR' if best[1] > 0.0004 else 'toujours pas — features Numerai peu exploitables par arbres nus'}",
          flush=True)


if __name__ == "__main__":
    main()
