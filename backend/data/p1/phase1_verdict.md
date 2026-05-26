# HyperDex Phase 1 — Verdict GATE

_Run : 2026-05-22T22:16:27.745448+00:00_

## Méthodo
- Univers : top 300 leaderboard, accountValue $10,000-$10,000,000, monthPnL>0
- Fenêtre : 90j  |  Holdout out-of-sample : 30j (train -90 à -30, holdout -30 à 0)
- Filtre copiable : profil intraday/swing, n>=50, train_pnl>0, holdout_pnl>0, holdout_n>=20
- Bonferroni correction : alpha=0.05/107=0.000467, z_crit=3.50

## Résultats
- Wallets analysés : **107**
- Candidats copiables (train+/holdout+/profile) : **14**
- Survivants Bonferroni (t-stat holdout > z_crit) : **10**

## Test agrégé — le rang train prédit-il le holdout ?
- Top quartile train (13 wallets) → holdout PnL cumulé : **$+656,916**
- Bottom quartile train (13 wallets) → holdout PnL cumulé : **$+4,681,381**
- Spread top-bottom = **$-4,024,466**  (NUL ou NÉGATIF = pas de prédiction (overfit))

## VERDICT
### **NON — pas d'edge copiable détecté.**
Bonferroni : 10 survivants (seuil cible >=5). Spread top-bot : $-4,024,466. **Projet à enterrer ou repenser** (doctrine : pas de live sans preuve).

## Top survivants Bonferroni (max 20)
| wallet | n | profile | hold_med_min | train_pnl | holdout_pnl | t_stat |
|---|---|---|---|---|---|---|
| 0xa3d843b6a057 | 8029 | swing | 1667 | $+27,120 | $+1,515,243 | 21.52 |
| 0x549e6dd8453e | 5555 | swing | 3051 | $+203,567 | $+269,209 | 10.51 |
| 0x1e48f1007fa1 | 5504 | swing | 1257 | $+125,806 | $+262,161 | 8.32 |
| 0x071d9fe61ce3 | 908 | swing | 695 | $+155,597 | $+238,373 | 13.56 |
| 0x80fb5880f381 | 8504 | intraday | 98 | $+73,313 | $+118,021 | 6.94 |
| 0x94dac1facdc4 | 10000 | intraday | 69 | $+85,549 | $+110,210 | 13.71 |
| 0xad227f63d34e | 10000 | swing | 935 | $+119,205 | $+93,853 | 8.35 |
| 0x5559da6ec434 | 7923 | swing | 3261 | $+870,033 | $+76,069 | 5.20 |
| 0x77746ff04a70 | 2951 | intraday | 199 | $+2,555 | $+38,399 | 7.55 |
| 0xbb10bda01f56 | 205 | swing | 1335 | $+77 | $+12,327 | 4.70 |

## Top candidats copiables (avant Bonferroni, max 30)
| wallet | n | profile | hold_med_min | train_pnl | holdout_pnl | t_stat |
|---|---|---|---|---|---|---|
| 0xa3d843b6a057 | 8029 | swing | 1667 | $+27,120 | $+1,515,243 | 21.52 |
| 0x549e6dd8453e | 5555 | swing | 3051 | $+203,567 | $+269,209 | 10.51 |
| 0x1e48f1007fa1 | 5504 | swing | 1257 | $+125,806 | $+262,161 | 8.32 |
| 0x071d9fe61ce3 | 908 | swing | 695 | $+155,597 | $+238,373 | 13.56 |
| 0x80fb5880f381 | 8504 | intraday | 98 | $+73,313 | $+118,021 | 6.94 |
| 0x94dac1facdc4 | 10000 | intraday | 69 | $+85,549 | $+110,210 | 13.71 |
| 0x77eeda199553 | 4000 | intraday | 8 | $+908,158 | $+105,820 | 2.87 |
| 0xad227f63d34e | 10000 | swing | 935 | $+119,205 | $+93,853 | 8.35 |
| 0x5559da6ec434 | 7923 | swing | 3261 | $+870,033 | $+76,069 | 5.20 |
| 0x77746ff04a70 | 2951 | intraday | 199 | $+2,555 | $+38,399 | 7.55 |
| 0xcf67e4da9e9c | 7784 | swing | 484 | $+69,332 | $+33,127 | 1.14 |
| 0x0871deb34bfd | 10000 | intraday | 18 | $+68,551 | $+13,777 | 2.78 |
| 0xbb10bda01f56 | 205 | swing | 1335 | $+77 | $+12,327 | 4.70 |
| 0x8f8d2d2565bf | 10000 | intraday | 51 | $+86,196 | $+830 | 2.93 |

## Distribution profile (population)
- swing : 42
- HFT : 29
- intraday : 19
- position : 17