# edge-factory / ALPHA-FORGE — FINDINGS (livrable, 2026-05-31)

## Ce que c'est

Un labo de **recherche + validation d'edge** (pur-python + numpy pour le lourd, tourne
Paris & Tokyo). Thèse : ne pas chercher le gros-gain-rapide (mort, base rate ~1 %), mais
**construire la machine qui distingue un vrai edge d'une illusion**, puis chasser sur
l'univers réellement tradeable.

**Conclusion après ~20 réfutations indépendantes : aucun edge retail tradable trouvé sur
crypto HL horaire ni actions daily, sous un juge correctement durci. L'asset livré = la
machine de validation + la discipline, pas un edge.** Ce n'est pas un échec — c'est le
résultat honnête que la quasi-totalité des « stratégies » ne survivent pas à un juge
rigoureux. Cf cahier des charges complet : `CAHIER_DES_CHARGES.md` (pipeline 3 étages).

## Le CRITIC = GATE A (le cœur, 5 sous-gates, seuils INTOUCHABLES)

Une hypothèse PASSE seulement si TOUTES passent (agrégation ET stricte).

| Sous-gate | Fichier | Rejette | Seuil |
|---|---|---|---|
| **beta-neutral** | `critic.py` | beta déguisé en alpha (OLS sur bench, exige alpha résiduel) | **t_alpha ≥ 3.0** (HLZ 2016) |
| **DSR** déflaté | `scripts/p2/_dsr_pbo.py` | Sharpe gonflé par le nb d'essais (data-snooping) | DSR > 0.95 |
| **PBO/CSCV** | `scripts/p2/_dsr_pbo.py` | overfit de sélection (best in-sample chanceux ?) | **PBO < 0.2** (LdP) |
| **permutation** | `permutation.py` | data-mining (Sharpe sans structure temporelle) | p < 0.05 |
| **convexité/tail** | `critic.py` | short-vol déguisé (γ<0 sur bench², explose au krach) | t_γ > −2 |

Agrégé par `verdict.evaluate_edge(...)`. Label d'échec précis : `beta_deguise` (|beta|≥0.5)
vs `weak_alpha` (beta≈0, juste pas de signal). **Juge PROUVÉ** par `selftest.py` (4 épreuves) :
best-of-200-bruits rejeté, taux de survie du bruit 0.0 %, edge planté détecté, edge réaliste
ténu détecté (pas de faux-négatif). pyright clean.

### Pourquoi ce durcissage (audit d'une revue externe, 2026-05-31)
Une revue (Claude web, markdown only) a soulevé 8 doutes ; auditée contre le code + la
littérature → 6/8 confirmés, corrigés : t 2→3.0 (Harvey-Liu-Zhu), PBO 0.5→0.2 (López de
Prado), n_trials réel (déflation honnête = nb total de hunters, pas la grille d'1 famille),
gate convexité ajouté (anti short-vol), test de faux-négatif ajouté. Un juge laxiste rendait
un futur survivant suspect → durci AVANT de pouvoir croire un 1er PASS.

## Discipline (non-négociable)

- **Univers = live** (`universe.py`) : on ne cherche QUE dans le tradeable réel — 17 perps
  HL ≥10M$/j, coûts réels (taker 4.5 bps + spread médian), granularité 1h exécutable.
- **No-look-ahead** : signal à i = passé only ; testé par prefix-invariance.
- **Parité backtest=live** : exec_lag=1 (fill i+1, jamais le close décisionnel).
- **Coûts sourcés d'emblée** (`costs.py` + frais HL officiels vérifiés).
- **Pré-enregistrement** : univers + grille fixés AVANT, lancés UNE fois (pas de p-hacking).
- **Investiguer AVANT de conclure** : un résultat choquant = souvent un bug (fixture
  variance-nulle, .pyc obsolète, échelle de temps), pas une réfutation. Vérifier le dict brut.
- **TDD** RED→GREEN ; smoke + 0 régression à chaque étape.

## Le moteur de chasse (Étage 1, opérationnel)

`python run_hunt.py` : fetch univers live 1× → enregistre ~40 familles (hunters) → juge
chacune au CRITIC 5-gates → leaderboard (survivants d'abord) + `research_memory`. Ajouter
une famille = une fabrique `make_*_hunter` dans `hunters.py`. Modules : `hunt.py` (Registry),
`hunters.py`, `universe.py`, adapters (`hl_adapter`, `equities_adapter`, `coinalyze`).

## Log des réfutations (~20, toutes propres)

| # | Angle | Verdict |
|---|---|---|
| 1-3 | Copy-trading HL (sélection-perf ×3) | beta/lottery/look-ahead, Spearman≈0 OOS |
| 4 | ICT/SMC (Dexterio) | no edge |
| 5-6 | Momentum/mean-rev/breakout per-symbol (HL+actions) | = beta déguisé |
| 7 | New-listing momentum HL | univers trop mince (N=2) |
| 8-11 | Cross-sectional 12m momentum (24→215 noms S&P600) | t=0.88-1.69, PBO 0.84, sous-seuil |
| 12 | Cross-sectional reversion HL daily | t=0.72 faible |
| 13 | Funding carry cross-sectional (price-exposé) | t=−2.5 à −3.5 (squeeze) |
| 14a | Funding carry **delta-neutral** | PASS idéalisé → CASSÉ au cost-stress (mirage basse-vol) |
| 14b | XS reversal intraday HL 1h | plat même en gross |
| 15 | Lead-lag BTC→alts cross-sectional | t_alpha −2.4 à −9.2 (continuation, pas rattrapage) |
| 16 | Transfer entropy BTC↔alts (non-linéaire) | BTC lead alts (arbé), alt→BTC = 0 sig |
| 17 | Liq-spike contrarian (BTC 8 mois) | −0.10 %/trade, empire avec + de data |
| 18 | OI-divergence (per-coin + cross-sectional) | grille pré-enreg 0/12, t<0.75 (le t~1.6 per-coin = artefact de sélection) |
| 19 | OI/volume ratio (accumulation passive) | grille 0/12, t<0.65 |
| 20 | Saisonnalité horaire UTC (profil train-only) | 0/3, t=−2.84 (profil ne se reproduit pas OOS) |

**Pattern structurel** : directionnel = **beta** ; market-neutral propre = **plat en gross**
ou **mangé par les coûts**. Sur HL horaire, toute la microstructure dérivée (funding, OI,
liquidations) + le calendaire sont **efficients**. Les 3 murs (beta / coûts / déjà-arbé) ne
se franchissent pas avec de la data gratuite horaire.

## Frontière data-payante (non franchie, décision opérateur)

| Famille | Coût | Retail ? |
|---|---|---|
| **PEAD / earnings** (fondamentaux point-in-time) | ~$50-150/mo | 🟡 le seul vrai maybe |
| Vol-risk-premium (vendre la vol) | ~$100-500/mo | 🟡 = même risque carry rejeté |
| Microstructure tick/L2 (sous-horaire) | $0 si auto-record HL | 🔴 alpha contesté HFT |
| News/NLP, alt-data | $200-$$$$ | 🔴 vitesse machine / arbé |

## État

**~227 tests verts, 39 modules cœur, pyright clean, 0 régression.** Étage 1 (chasse +
juge durci) complet et opérationnel. Étages 2 (forward/paper) & 3 (live) = dessinés dans
le cahier des charges, **non construits** (règle : ne se construisent que tirés par un 1er
survivant GATE A). **0 survivant à ce jour.** Priorité = trouver le 1er PASS GATE A dans
l'univers live, sinon continuer à documenter les réfutations. Reprise = nouvelle hypothèse
data-distincte, ou data-payante (PEAD), en rebranchant le moteur de chasse.
