#!/usr/bin/env python3
"""CRITIC — composants du juge incorruptible de l'edge-factory.

Phase 1, brique centrale pour la chasse aux niches : le test BETA-NEUTRAL.
Le tueur récurrent de cette recherche = un « edge » directionnel qui n'est que
du beta déguisé (copy-trading HYPE, liquidation-fade beta 1.16×, momentum =
crash-beta). Avant de croire à un alpha, on régresse les returns de la strat sur
le benchmark et on exige un alpha RÉSIDUEL significatif après retrait du beta.

OLS pur-python (pas de numpy/scipy requis), testé known-answer dans test_critic.py.
Le DSR/PBO (overfit / multiple-testing) vit dans scripts/p2/_dsr_pbo.py ;
le verdict agrégé (OOS + DSR + PBO + beta-neutral) sera câblé en itération suivante.
"""
import math


def beta_neutral_alpha(strat_returns, bench_returns):
    """Régression OLS strat ~ alpha + beta*bench. Retourne alpha, beta, t_alpha.

    t_alpha = statistique de Student de l'intercept (alpha). |t_alpha| élevé +
    alpha>0 = vrai alpha résiduel ; alpha~0 = la strat n'est que du beta.
    """
    n = len(strat_returns)
    if n < 3 or len(bench_returns) != n:
        return {"alpha": 0.0, "beta": 0.0, "t_alpha": 0.0, "n": n}
    mb = sum(bench_returns) / n
    ms = sum(strat_returns) / n
    sxx = sum((b - mb) ** 2 for b in bench_returns)
    if sxx <= 0:  # benchmark constant -> pas de beta estimable
        beta = 0.0
    else:
        cov = sum((b - mb) * (s - ms)
                  for b, s in zip(bench_returns, strat_returns))
        beta = cov / sxx
    alpha = ms - beta * mb
    sse = sum((s - (alpha + beta * b)) ** 2
              for s, b in zip(strat_returns, bench_returns))
    if sse <= 1e-15:  # ajustement parfait : alpha exact, t indéfini
        t_alpha = math.inf if abs(alpha) > 1e-12 else 0.0
    else:
        s2 = sse / (n - 2)
        denom = (1.0 / n) + (mb * mb / sxx if sxx > 0 else 0.0)
        se_alpha = math.sqrt(s2 * denom)
        t_alpha = alpha / se_alpha if se_alpha > 0 else 0.0
    return {"alpha": alpha, "beta": beta, "t_alpha": t_alpha, "n": n}


BETA_DISGUISE_MIN = 0.5  # |beta| ≥ ce seuil = exposition marché réelle = beta déguisé


def beta_neutral_verdict(strat_returns, bench_returns, t_min=2.0):
    """Verdict beta-neutral : PASS seulement si alpha résiduel > 0 ET significatif.

    Sinon la raison distingue (P5, label précis) :
      - 'beta_deguise'  : |beta| ≥ BETA_DISGUISE_MIN → vraie expo marché masquée en alpha
      - 'weak_alpha'    : |beta| < seuil mais alpha non-significatif → pas du beta, juste
                          pas de signal (le label 'beta_deguise' était trompeur ici).
    """
    r = beta_neutral_alpha(strat_returns, bench_returns)
    ok = r["alpha"] > 0 and r["t_alpha"] >= t_min
    if ok:
        reason = "ok"
    elif abs(r["beta"]) >= BETA_DISGUISE_MIN:
        reason = "beta_deguise"
    else:
        reason = "weak_alpha"
    return {
        "pass": bool(ok),
        "reason": reason,
        "alpha": round(r["alpha"], 6),
        "beta": round(r["beta"], 4),
        "t_alpha": round(r["t_alpha"], 3) if math.isfinite(r["t_alpha"]) else r["t_alpha"],
        "n": r["n"],
    }


def convexity_verdict(strat_returns, bench_returns, t_gamma_min=2.0):
    """Gate convexité / tail — démasque le short-vol déguisé (V3).

    L'OLS linéaire (beta_neutral) rate une stratégie qui gagne petit en temps normal
    mais explose au krach : son profil est CONVEXE négatif en bench (gamma<0 sur
    bench²). On régresse strat ~ α + β·bench + γ·bench² (moindres carrés 3 params) et
    on REJETTE si γ est significativement négatif (t_gamma ≤ −t_gamma_min) = profil
    tail-risk caché. Pur-python (équations normales 3×3 par élimination de Gauss).
    """
    n = len(strat_returns)
    if n < 10 or len(bench_returns) != n:
        return {"pass": True, "reason": "ok", "gamma": 0.0, "t_gamma": 0.0, "n": n}
    # design [1, b, b²]
    X = [[1.0, b, b * b] for b in bench_returns]
    y = list(strat_returns)
    # XtX (3×3) et Xty (3)
    XtX = [[sum(X[k][i] * X[k][j] for k in range(n)) for j in range(3)]
           for i in range(3)]
    Xty = [sum(X[k][i] * y[k] for k in range(n)) for i in range(3)]
    coef = _solve3(XtX, Xty)
    if coef is None:
        return {"pass": True, "reason": "ok", "gamma": 0.0, "t_gamma": 0.0, "n": n}
    gamma = coef[2]
    # erreur-type de gamma via (XtX)^-1 * s²
    resid = [y[k] - (coef[0] + coef[1] * X[k][1] + coef[2] * X[k][2])
             for k in range(n)]
    sse = sum(e * e for e in resid)
    s2 = sse / (n - 3) if n > 3 else 0.0
    inv = _inv3(XtX)
    var_gamma = inv[2][2] * s2 if inv is not None else 0.0
    t_gamma = gamma / math.sqrt(var_gamma) if var_gamma > 0 else 0.0
    risky = t_gamma <= -t_gamma_min  # convexité négative significative
    return {
        "pass": not risky,
        "reason": "convexity_risk" if risky else "ok",
        "gamma": round(gamma, 6),
        "t_gamma": round(t_gamma, 3) if math.isfinite(t_gamma) else t_gamma,
        "n": n,
    }


def _solve3(A, b):
    """Résout A·x = b pour A 3×3 (élimination de Gauss avec pivot). None si singulier."""
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for col in range(3):
        piv = max(range(col, 3), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-15:
            return None
        M[col], M[piv] = M[piv], M[col]
        pv = M[col][col]
        M[col] = [v / pv for v in M[col]]
        for r in range(3):
            if r != col:
                f = M[r][col]
                M[r] = [a - f * b2 for a, b2 in zip(M[r], M[col])]
    return [M[i][3] for i in range(3)]


def _inv3(A):
    """Inverse d'une matrice 3×3 (pour les erreurs-types). None si singulier."""
    cols = []
    for j in range(3):
        e = [1.0 if i == j else 0.0 for i in range(3)]
        x = _solve3(A, e)
        if x is None:
            return None
        cols.append(x)
    return [[cols[j][i] for j in range(3)] for i in range(3)]
