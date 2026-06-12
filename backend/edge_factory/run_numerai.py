#!/usr/bin/env python3
"""TEST DÉCISIF NUMERAI — ridge sur 23 features → corrélation OOS (métrique de paiement).

Pipeline : train.parquet → fit ridge (features→target) → predict validation.parquet
→ corrélation de Spearman par date (la "corr" Numerai = ce qui est payé) → moyenne +
Sharpe des corr (consistance). Numerai paie le résiduel décorrélé : une corr moyenne
faible mais POSITIVE et STABLE suffit (≠ CRITIC live-trading, bar bien plus bas).

Le ridge pur-python est O(n·m²) → on échantillonne le TRAIN (les features sont déjà
rank-normalisées, un sous-échantillon suffit pour 23 features) mais on SCORE sur
toute la validation. Pré-enregistré : lambda=10, échantillon train 80k lignes, seed 42.
"""
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd

import neutralize as nz
import ridge as rg

TRAIN = "numerai_data/train.parquet"
VALID = "numerai_data/validation.parquet"
TARGET = "target"
LAMBDA = 10.0
TRAIN_SAMPLE = 80000
SEED = 42


def main():
    feat_cols = None
    print("[1/4] lecture train + features...", flush=True)
    tr = pd.read_parquet(TRAIN)
    feat_cols = [c for c in tr.columns
                 if c.startswith("feature") and pd.api.types.is_numeric_dtype(tr[c])]
    tr = tr.dropna(subset=feat_cols + [TARGET])
    tr_s = tr.sample(n=min(TRAIN_SAMPLE, len(tr)), random_state=SEED)
    X = tr_s[feat_cols].values.tolist()
    y = tr_s[TARGET].values.tolist()
    print(f"   {len(X)} lignes train échantillonnées, {len(feat_cols)} features", flush=True)

    print("[2/4] fit ridge...", flush=True)
    model = rg.fit(X, y, lam=LAMBDA)

    print("[3/4] lecture validation + prédiction (numpy)...", flush=True)
    import numpy as np
    va = pd.read_parquet(VALID, columns=feat_cols + [TARGET, "date"])
    va = va.dropna(subset=feat_cols + [TARGET])
    feat_arr = va[feat_cols].values
    tgt_arr = va[TARGET].values
    preds = np.asarray(rg.predict_fast(model, feat_arr))  # numpy : 63s→<1s

    # indices par era calculés UNE fois (pas de copie de 4.3M lignes par proportion)
    era_idx = [idx for idx in va.groupby("date").indices.values() if len(idx) >= 20]

    print("[4/4] corrélation OOS par date — baseline + neutralisation...", flush=True)

    def score(pred_arr):
        corrs = [rg.spearman_fast(pred_arr[idx], tgt_arr[idx]) for idx in era_idx]
        mc = statistics.mean(corrs)
        sc = statistics.pstdev(corrs)
        return mc, (mc / sc if sc > 0 else 0.0), sum(1 for c in corrs if c > 0) / len(corrs), len(corrs)

    # projection calculée UNE fois par era (réutilisée pour toutes les proportions)
    _, proj = nz.neutralize_by_era_fast(preds, feat_arr, va["date"].values, proportion=1.0)
    proj = np.asarray(proj)
    print(f"\n=== VERDICT NUMERAI — ridge + neutralisation (OOS validation) ===", flush=True)
    print(f"   {'proportion':>10} {'corr_moy':>10} {'corr_Sharpe':>12} {'%dates>0':>9}", flush=True)
    best = None
    for prop in (0.0, 0.3, 0.5, 1.0):
        pc = preds - prop * proj  # réutilise proj → balayage quasi-gratuit
        mc, sh, pos, nd = score(pc)
        tag = ""
        if best is None or sh > best[1]:
            best = (prop, sh, mc)
            tag = " ←"
        print(f"   {prop:>10.1f} {mc:>+10.5f} {sh:>+12.3f} {pos*100:>8.1f}%{tag}", flush=True)
    print(f"\n   MEILLEUR : prop={best[0]} corr_Sharpe={best[1]:+.3f} corr_moy={best[2]:+.5f}",
          flush=True)
    print(f"   → {'VIABLE' if best[1] > 0.5 else 'progrès mais sous le bar — itérer (non-linéarité)'}",
          flush=True)


if __name__ == "__main__":
    main()
