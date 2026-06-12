#!/usr/bin/env python3
"""DSR (Deflated Sharpe Ratio) + PBO (Probability of Backtest Overfitting / CSCV).

Lopez de Prado, "The Deflated Sharpe Ratio" (2014) + "The Probability of
Backtest Overfitting" (2015). Calculable offline sur le substrat — c'est ce qui
tranche overfit-de-multiple-testing vs edge réel, là où un simple train/holdout
ne suffit pas (cf cohort 232 ≈ 90% lottery).

Cœur statistique pur-python (statistics.NormalDist, pas de numpy/scipy requis),
testé known-answer dans test_dsr_pbo.py. main() = wiring IO (lit le CSV
per-wallet du run véracité + matrice [jour × wallet] optionnelle).

Entrées :
  - per-wallet CSV : veracity_v2_perwallet_28d_*.csv (colonnes tr_sharpe, tr_n…)
  - matrice CSV optionnelle : jour × wallet de PnL net quotidien (2ᵉ passe Tokyo)
    → donne skew/kurtosis réels (correction non-normale du DSR) + PBO/CSCV.
"""
import argparse
import csv as _csv
import math
from itertools import combinations
from statistics import NormalDist

_N = NormalDist()
EULER = 0.5772156649015329  # constante d'Euler-Mascheroni (γ)


def _norm_cdf(x):
    return _N.cdf(x)


def _norm_ppf(p):
    return _N.inv_cdf(p)


def _moments(xs):
    """(n, mean, m2, m3, m4) avec m_k = moments centrés populationnels."""
    n = len(xs)
    if n == 0:
        return 0, 0.0, 0.0, 0.0, 0.0
    mu = sum(xs) / n
    m2 = m3 = m4 = 0.0
    for x in xs:
        dvm = x - mu
        d2 = dvm * dvm
        m2 += d2
        m3 += d2 * dvm
        m4 += d2 * d2
    return n, mu, m2 / n, m3 / n, m4 / n


def _sharpe(xs):
    """Sharpe = mean / stdev (échantillon, ddof=1). 0 si n<2 ou variance nulle."""
    n = len(xs)
    if n < 2:
        return 0.0
    mu = sum(xs) / n
    var = sum((x - mu) ** 2 for x in xs) / (n - 1)
    return mu / math.sqrt(var) if var > 0 else 0.0


def _skew(xs):
    """Skewness populationnelle m3 / m2**1.5. 0 si n<3 ou variance nulle."""
    n, _, m2, m3, _ = _moments(xs)
    if n < 3 or m2 <= 0:
        return 0.0
    return m3 / (m2 ** 1.5)


def _kurtosis(xs):
    """Kurtosis NON-excess m4 / m2**2 (normale ≈ 3). 0 si n<2 ou variance nulle."""
    n, _, m2, _, m4 = _moments(xs)
    if n < 2 or m2 <= 0:
        return 0.0
    return m4 / (m2 * m2)


def psr(sr, sr_benchmark, T, skew, kurt):
    """Probabilistic Sharpe Ratio : P(SR_vrai > sr_benchmark) sous estimation.

    PSR = Φ( (sr - sr_benchmark)·√(T-1) / √(1 - skew·sr + (kurt-1)/4·sr²) ).
    """
    denom = math.sqrt(max(1e-12, 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr))
    return _norm_cdf((sr - sr_benchmark) * math.sqrt(max(1, T - 1)) / denom)


def expected_max_sharpe(sr_variance, n_trials):
    """SR0 = Sharpe max attendu sous H0 (aucun edge) après n_trials essais.

    SR0 = √Var(SR)·[ (1-γ)·Z⁻¹(1-1/N) + γ·Z⁻¹(1-1/(N·e)) ].
    Croît avec n_trials → c'est la pénalité multiple-testing.
    """
    if n_trials < 2 or sr_variance <= 0:
        return 0.0
    s = math.sqrt(sr_variance)
    return s * ((1 - EULER) * _norm_ppf(1 - 1.0 / n_trials)
                + EULER * _norm_ppf(1 - 1.0 / (n_trials * math.e)))


def deflated_sharpe(sr, T, skew, kurt, sr_variance, n_trials):
    """DSR = PSR avec benchmark = SR0(n_trials). >0.95 = robuste à l'overfit."""
    sr0 = expected_max_sharpe(sr_variance, n_trials)
    return psr(sr, sr0, T, skew, kurt)


def pbo_cscv(rows, S=16):
    """Probability of Backtest Overfitting via CSCV (combinatorially symmetric CV).

    rows : matrice [T périodes × N stratégies] de returns.
    Découpe T en S blocs ; pour chaque combinaison de S/2 blocs en IS :
      sélectionne la meilleure strat IS, regarde son rang OOS → logit λ.
    PBO = fraction des combos où λ < 0 (la meilleure IS sous la médiane OOS).
    Retourne (pbo, logits).
    """
    T = len(rows)
    N = len(rows[0]) if T else 0
    if T < S or N < 2:
        return float("nan"), []
    # blocs d'indices contigus (aussi égaux que possible)
    bounds = [round(i * T / S) for i in range(S + 1)]
    chunks = [list(range(bounds[s], bounds[s + 1])) for s in range(S)]

    def col_sharpe(idx, n):
        return _sharpe([rows[t][n] for t in idx])

    logits = []
    eps = 1e-6
    for is_blocks in combinations(range(S), S // 2):
        is_set = set(is_blocks)
        is_idx, oos_idx = [], []
        for s in range(S):
            (is_idx if s in is_set else oos_idx).extend(chunks[s])
        is_sr = [col_sharpe(is_idx, n) for n in range(N)]
        oos_sr = [col_sharpe(oos_idx, n) for n in range(N)]
        n_star = max(range(N), key=lambda n: is_sr[n])
        # rang OOS de la strat sélectionnée : 1 (pire) .. N (meilleure)
        rank = sum(1 for n in range(N) if oos_sr[n] <= oos_sr[n_star])
        omega = min(1 - eps, max(eps, rank / (N + 1)))
        logits.append(math.log(omega / (1 - omega)))
    pbo = sum(1 for x in logits if x < 0) / len(logits)
    return pbo, logits


# --------------------------------------------------------------------------
# main() = wiring IO (non couvert par les tests unitaires ; glue lecture CSV).
# --------------------------------------------------------------------------
def _read_perwallet(path):
    with open(path, newline="") as f:
        return list(_csv.DictReader(f))


def _f(row, k):
    v = row.get(k, "")
    try:
        return float(v) if v not in ("", "None", None) else None
    except ValueError:
        return None


def analyze_perwallet(rows, qual_n_min=20):
    """DSR au niveau cohorte depuis le CSV per-wallet.

    n_trials = nb de wallets évalués (largeur du multiple-testing).
    sr_variance = variance des Sharpe train sur tout l'univers scanné.
    Approx normale (skew=0,kurt=3) — la matrice donne la correction réelle.
    """
    sharpes_all = [_f(r, "tr_sharpe") for r in rows]
    sharpes_all = [s for s in sharpes_all if s is not None]
    n_trials = len(sharpes_all)
    if n_trials < 2:
        return {"error": "pas assez de wallets"}
    mu = sum(sharpes_all) / n_trials
    sr_var = sum((s - mu) ** 2 for s in sharpes_all) / (n_trials - 1)
    sr0 = expected_max_sharpe(sr_var, n_trials)
    # cohorte qualifiée = WRclose 0.55-0.90, tr_n>=min, net>0 (même filtre que le run)
    quals = []
    for r in rows:
        wr = _f(r, "tr_wr_close")
        n = _f(r, "tr_n")
        net = _f(r, "tr_net")
        sh = _f(r, "tr_sharpe")
        if (wr is not None and n and net is not None and sh is not None
                and n >= qual_n_min and 0.55 <= wr < 0.90 and net > 0):
            quals.append((r["addr"], sh, int(n)))
    quals.sort(key=lambda x: -x[1])
    out = {"n_trials": n_trials, "sr_variance": round(sr_var, 5),
           "sr0_expected_max": round(sr0, 4), "n_qualifiers": len(quals),
           "top": []}
    for addr, sh, n in quals[:10]:
        dsr = deflated_sharpe(sh, T=n, skew=0.0, kurt=3.0,
                              sr_variance=sr_var, n_trials=n_trials)
        out["top"].append({"addr": addr, "tr_sharpe": round(sh, 4),
                           "tr_n": n, "dsr_normal_approx": round(dsr, 4)})
    out["n_qual_dsr_gt_0_95"] = sum(
        1 for t in out["top"] if t["dsr_normal_approx"] > 0.95)
    return out


def _read_matrix(path):
    """Matrice CSV jour×wallet → (rows[T][N], wallet_ids). 1ère col = jour."""
    with open(path, newline="") as f:
        rdr = _csv.reader(f)
        header = next(rdr)
        wallet_ids = header[1:]
        rows = [[float(x) for x in line[1:]] for line in rdr if line]
    return rows, wallet_ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--perwallet", help="veracity_v2_perwallet_*.csv")
    ap.add_argument("--matrix", help="matrice jour×wallet de PnL net (optionnel)")
    ap.add_argument("--S", type=int, default=16, help="blocs CSCV (pair)")
    args = ap.parse_args()

    if args.perwallet:
        rep = analyze_perwallet(_read_perwallet(args.perwallet))
        print("=== DSR (per-wallet, approx normale) ===")
        for k, v in rep.items():
            if k != "top":
                print(f"  {k} = {v}")
        for t in rep.get("top", []):
            print(f"   {t['addr'][:10]}… SR={t['tr_sharpe']:+.3f} "
                  f"n={t['tr_n']} DSR={t['dsr_normal_approx']:.3f}")

    if args.matrix:
        rows, wids = _read_matrix(args.matrix)
        pbo, logits = pbo_cscv(rows, S=args.S)
        print(f"\n=== PBO / CSCV ({len(rows)}j × {len(wids)} wallets, "
              f"S={args.S}, {len(logits)} combos) ===")
        print(f"  PBO = {pbo:.3f}  ({'OVERFIT' if pbo > 0.5 else 'ROBUSTE'} "
              f"— seuil 0.5)")
        # DSR corrigé non-normal par wallet (skew/kurt réels du return série)
        per = [[r[n] for r in rows] for n in range(len(wids))]
        mus = [(_sharpe(s)) for s in per]
        mu = sum(mus) / len(mus) if mus else 0.0
        sr_var = (sum((s - mu) ** 2 for s in mus) / (len(mus) - 1)
                  if len(mus) > 1 else 0.0)
        print(f"  sr_variance(daily) = {sr_var:.5f}, n_trials = {len(wids)}")


if __name__ == "__main__":
    main()
