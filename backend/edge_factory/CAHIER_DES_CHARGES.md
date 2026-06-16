# ALPHA-FORGE — Cahier des charges (état réel, 2026-05-31)

> Réécrit pour cadrer la vraie ambition : une fabrique d'edge interne et durable,
> de la recherche jusqu'au live, sans jamais lâcher la discipline anti-illusion.
> Décrit ce qui EXISTE (vérifié), ce qui est À CONSTRUIRE, et la frontière entre les deux.
> Base héritée : 182 tests verts, 30 modules cœur, ~17 réfutations propres, 0 edge rentable.

## 1. Mission (UNE phrase)

**Une fabrique qui chasse des edges rares, les juge sans pitié, durcit les survivants
hors-échantillon, et les amène jusqu'au capital réel — chaque étage tuant une illusion
que l'étage précédent ne peut pas tuer.**

L'appli n'est PAS un bot de trading « one-click », ni un produit Numerai. C'est une
chaîne **recherche → validation → durcissage → déploiement**, pensée pour durer et pour
ne produire que des edges en lesquels on peut vraiment croire.

## 2. Le changement de cadrage (vs version précédente)

L'ancien doc décrivait un labo de recherche avec un verdict assumé : « l'asset = la
machine + la discipline, pas un edge », et scope négatif « pas de live ».

**Ce qui change** : on assume le pipeline complet jusqu'au live, mais sans trahir la
philosophie. La clé est de ne PAS voir le live comme « un module exec en plus », mais
comme **deux gates de validation supplémentaires**, chacun contre une illusion précise :

- Le CRITIC valide « ce signal est-il statistiquement réel sur l'historique ? »
- Le live introduit deux nouvelles façons de se mentir (overfit temporel, exécution réelle)
  → donc deux gates de plus, pas un raccourci.

**Ce qui NE change pas** : seuils intouchables, no-look-ahead, coûts réels d'emblée,
pré-enregistrement, TDD. Le live se gagne, il ne se décrète pas.

**Principe de séquencement honnête.** On a 0 survivant à ce jour. On dessine le
pipeline complet maintenant (gratuit, clarifiant), on applique la discipline « univers =
live » à la recherche tout de suite (ça rend l'Étage 1 honnête), mais on ne construit
les Étages 2 et 3 que quand un edge frappe vraiment à la porte. Pas de chaîne de production
pour un produit qui n'existe pas encore.

## 3. Où ça vit

`hyperdex/backend/edge_factory/` — **application autonome**. Réutilise UNIQUEMENT
`InfoClient` (HL API) du reste de HyperDex ; ne modifie rien d'autre. Pur-python pour le
cœur, numpy autorisé pour les calculs lourds. Aucun lien avec le bot copy-trading HyperDex
ni avec d'autres projets.

L'Étage 3 (live) restera isolé : un edge ne touche du capital réel que par une décision
humaine explicite après les 3 gates. **Pas d'auto-déploiement.**

## 4. Le pipeline en 3 étages — les 3 gates (le cœur du cadrage)

```
  ÉTAGE 1 — LABO              ÉTAGE 2 — DURCISSAGE          ÉTAGE 3 — LIVE
  (existe à ~80%)             (à construire au besoin)      (à construire au besoin)

  génération d'hypothèses     survivant figé                déploiement capital réel
        │                         │                              │
        ▼                         ▼                              ▼
  ┌───────────────┐         ┌───────────────┐            ┌───────────────┐
  │   GATE A      │         │   GATE B      │            │   GATE C      │
  │   = CRITIC    │  ─PASS─▶ │  = FORWARD    │  ─PASS─▶   │  = LIVE       │
  │ (4 sous-gates)│         │   / PAPER     │            │ exécution+cap │
  └───────────────┘         └───────────────┘            └───────────────┘
   réel sur l'historique?    survit hors-échantillon,      survit à l'exécution
   TUE le data-mining        régime actuel?                réelle et au capital?
                             TUE l'overfit temporel        TUE l'illusion d'exéc.
        │                         │                              │
        ▼                         ▼                              ▼
   research_memory          forward_log (à créer)         live_log (à créer)
   leaderboard              décision GO/NO-GO humaine      monitoring + kill-switch
```

| Gate | Question à laquelle il répond | Illusion qu'il tue | Existe ? |
|---|---|---|---|
| **A — CRITIC** | Le signal est-il statistiquement réel sur l'historique ? | Data-mining / hasard | ✅ oui (4 sous-gates) |
| **B — Forward/Paper** | Survit-il en avant, hors-échantillon, dans le régime actuel ? | Overfit temporel / régime mort | ❌ à construire |
| **C — Live** | Survit-il à l'exécution réelle et à mon capital ? | Slippage / capacité / latence / adverse selection | ❌ à construire |

Un edge n'avance d'un étage que s'il PASS le gate. Un FAIL est documenté (jamais perdu),
exactement comme une réfutation de l'Étage 1.

## 5. Architecture en couches

### Étage 1 — le moteur de chasse (existe)

```
                    ┌─────────────── LE MOTEUR DE CHASSE ───────────────┐
  DONNÉES           │   hunt.py (Registry) ── hunters.py (fabriques)     │
  ┌──────────────┐  │        │ register(nom, hunter)                     │
  │ hl_adapter   │──┼──▶ une famille = un "hunter" ()→{strat,bench,...}  │
  │ equities_ad. │  │        │                                           │
  │ coinalyze    │  │        ▼                                           │
  │ liq_recorder │  │   verdict.evaluate_edge  ◀── GATE A (4 sous-gates) │
  └──────────────┘  │        │   1. beta-neutral (critic.py)             │
                    │        │   2. DSR déflaté (_dsr_pbo.py)            │
  FAMILLES D'EDGE   │        │   3. PBO/CSCV (_dsr_pbo.py)              │
  ┌──────────────┐  │        │   4. permutation (permutation.py)         │
  │ cross_section│  │        ▼                                           │
  │ funding      │  │   research_memory.py (journal JSON)                │
  │ hawkes(_sig) │  │        │                                           │
  │ liq_spike    │  │        ▼                                           │
  │ lead_lag     │  │   leaderboard (survivants d'abord) ──▶ ÉTAGE 2     │
  │ transfer_ent.│  └───────────────────────────────────────────────────┘
  └──────────────┘
  OUTILS : costs.py, metrics.py, ridge.py + gbm.py + neutralize.py, generator/llm/autonomous.
```

### Étages 2 & 3 — à construire (squelette cible, rien d'existant)

```
  ÉTAGE 2 — DURCISSAGE                      ÉTAGE 3 — LIVE
  ┌────────────────────────┐               ┌────────────────────────┐
  │ robustness.py          │ paramètres    │ executor.py (OMS)      │ ordres réels (HL)
  │  (stabilité params,    │ stables ?     │ sizing.py / risk.py    │ taille, stop, kill
  │   sensibilité, régimes)│               │ paper_executor.py      │ (sim exéc fidèle)
  │ forward_runner.py      │ rejoue en     │ monitor.py             │ alerte si dérive vs
  │  (out-of-sample / WFO) │ avant         │  (live vs paper vs OOS)│ paper/backtest
  │ forward_log (JSONL)    │               │ live_log (JSONL)       │ kill-switch
  └────────────────────────┘               └────────────────────────┘
        GATE B                                    GATE C
```

## 6. Inventaire réel des modules

### Étage 1 — existe (30 cœur)

#### Le CRITIC = GATE A (juge, intouchable)

| Module | Rôle | Seuil |
|---|---|---|
| `verdict.py` | agrège les 4 sous-gates en 1 verdict PASS/FAIL | — |
| `critic.py` | sous-gate beta-neutral (alpha résiduel OLS) | t_alpha ≥ 2 |
| `scripts/p2/_dsr_pbo.py` | sous-gates DSR (déflaté n_trials) + PBO/CSCV | DSR>0.95, PBO<0.5 |
| `permutation.py` | sous-gate permutation (tue le data-mining) | p<0.05 |
| `selftest.py` | prouve que le juge marche (best-of-noise rejeté, bruit→0) | — |

#### Le moteur de chasse

| Module | Rôle |
|---|---|
| `hunt.py` | Registry : register/judge/hunt_all/leaderboard |
| `hunters.py` | fabriques qui branchent les familles → Registry |
| `run_hunt.py` | point d'entrée : `python run_hunt.py` = la chasse complète |
| `research_memory.py` | journal persistant (tested/rejected/survived, dédup) |

#### Familles d'edge (les chasseurs)

| Module | Hypothèse | Statut |
|---|---|---|
| `cross_sectional.py` | momentum/reversion long-short market-neutral | réfuté (HL+actions) |
| `funding.py` | carry funding delta-neutral | réfuté (cost-eaten) |
| `hawkes.py` + `hawkes_signal.py` | cascades liquidation auto-excitantes | moteur OK, data tick manquante |
| `liq_spike.py` | spike liquidation → mean-reversion contrarian | réfuté (BTC 8 mois) |
| `lead_lag.py` | lead-lag BTC→alts | réfuté |
| `transfer_entropy.py` | flux d'info directionnel (Schreiber) | réfuté (BTC lead, non-exploitable) |
| `recency.py` | new-listing | réfuté (univers trop mince) |

#### Adapters de données

`hl_adapter.py` (HL perps), `equities_adapter.py` (Yahoo actions), `coinalyze.py`
(liquidations REST, clé OK), `liq_recorder.py` (Binance WS, bloqué Paris).

#### Outils transverses & modélisation

`costs.py`, `metrics.py`, `adapter.py` — `ridge.py`, `gbm.py`, `neutralize.py`,
`numerai_connector.py` (Numerai = outil/débouché, pas mission).

#### Génération d'hypothèses

`hypothesis_dsl.py`, `llm_hypothesis.py`, `generator.py`, `autonomous.py`.

### Étages 2 & 3 — à construire (rien n'existe encore)

| Module cible | Étage | Rôle | Gate |
|---|---|---|---|
| `robustness.py` | 2 | stabilité des paramètres, sensibilité, comportement par régime | B |
| `forward_runner.py` | 2 | rejeu out-of-sample / walk-forward (WFO) sur données non vues | B |
| `forward_log` (JSONL) | 2 | journal forward (comme research_memory mais en avant) | B |
| `paper_executor.py` | 3 | simulation d'exécution fidèle (carnet, latence, slippage modélisé) | C |
| `executor.py` (OMS) | 3 | passage d'ordres réels (HL), idempotent, réconcilié | C |
| `sizing.py` / `risk.py` | 3 | taille de position, stops, limites d'exposition | C |
| `monitor.py` + kill-switch | 3 | compare live vs paper vs OOS, alerte/coupe si dérive | C |
| `live_log` (JSONL) | 3 | journal live (PnL réel, fills, écarts vs attendu) | C |

## 7. La discipline de l'univers (« univers = live ») — NOUVEAU, central

**Règle : on ne cherche QUE dans l'univers réellement tradeable.** Chercher un edge sur un
univers qu'on ne peut pas exécuter (mauvais actifs, mauvais venue, timeframe irréaliste)
rend tout l'aval fictif.

Donc on fige d'abord l'univers live :

1. **Venue(s) d'exécution** (ex. HL perps) et leurs contraintes réelles (tick, lot, frais, funding).
2. **Actifs effectivement liquides** et tradeables là-bas.
3. **Granularité exécutable** (la timeframe à laquelle on peut vraiment passer/sortir).

Et on contraint toute la recherche de l'Étage 1 à cet univers, **dès maintenant** — pas
seulement pour l'Étage 3. C'est l'extension naturelle de la règle « coûts réels d'emblée » :
désormais **univers réel d'emblée** aussi. Un edge trouvé hors de cet univers ne compte pas.

## 8. Workflow (comment on opère, concrètement)

**Étage 1 — chasser (boucle quotidienne possible) :**

1. Ajouter une famille = écrire `make_X_hunter(data,...)` dans `hunters.py`
   → retourne `()→{strat, bench, n_trials, sr_variance}`, **sur l'univers live uniquement**.
2. L'enregistrer dans `run_hunt.build_registry()`.
3. `python run_hunt.py` : fetch 1×, juge toutes les familles au GATE A, sort le leaderboard,
   logge en `research_memory`.
4. Lire le verdict : survivant (PASS A) → candidat Étage 2 ; sinon réfutation documentée.

**Étage 2 — durcir (déclenché par un survivant, à construire) :**

5. Figer le survivant (params gelés, pré-enregistrés).
6. `robustness.py` : le survivant tient-il quand on bouge les params / change de régime ?
7. `forward_runner.py` : rejeu out-of-sample / walk-forward sur données non vues → GATE B.
8. Décision humaine GO/NO-GO vers l'Étage 3.

**Étage 3 — déployer (déclenché par GATE B PASS + GO humain, à construire) :**

9. Paper d'abord (`paper_executor.py`) : l'edge survit-il à l'exécution simulée fidèle ?
10. Live progressif (capital minimal), `monitor.py` compare live vs paper vs OOS.
11. Kill-switch si dérive. PnL réel logué, écarts analysés → boucle de retour vers Étage 1.

## 9. Règles non négociables (discipline)

- **Seuils des 3 gates intouchables** (jamais baissés pour faire passer un candidat).
- **Univers = live** (on ne cherche que dans le tradeable réel — §7).
- **No-look-ahead** (signal à i = passé only ; testé par prefix-invariance).
- **Coûts réels d'emblée** (exec_lag=1, slippage/fees sourcés).
- **Pré-enregistrement** (univers+grille fixés avant, un seul run, pas de p-hacking).
- **Un gate par illusion** (ne jamais sauter B pour aller plus vite vers C).
- **Pas d'auto-déploiement** (le live se gagne par décision humaine après les 3 gates).
- **Investiguer avant de conclure** (vérifier le dict brut, pas le print).
- **TDD** : RED→GREEN pour toute logique. Smoke + 0 régression à chaque étape.
- **Pas de commit auto.** Data lourde / clés / .jsonl gitignored.

## 10. État & verdict honnête

- **Étage 1 marche** : prouvé par `selftest` (rejette le bruit, garde le signal planté) et
  par ~17 réfutations propres sur vraie data.
- **Étages 2 & 3** : rien de construit — squelette cible seulement (§5, §6).
- **0 edge rentable à ce jour** (familles prix/funding/liq toutes réfutées).
- L'asset reste **la machine + la discipline**. Le pipeline complet est dessiné ; il ne
  sera construit que tiré par un vrai survivant. **Priorité absolue : trouver le 1er PASS A
  dans l'univers live.**

## 11. Roadmap

| # | Chantier | Étage | Pourquoi | Effort |
|---|---|---|---|---|
| **P0** | Figer l'univers live + contraindre l'Étage 1 dessus | 1 | rend toute la recherche honnête, prérequis de tout | faible |
| **P1** | Brancher funding + liq-spike live dans run_hunt (Coinalyze) | 1 | compléter la chasse auto | faible |
| **P2** | Nouveaux angles crypto : open interest extrêmes, basis cross-venue | 1 | data qu'on a, peu exploitée | moyen |
| **P3** | Générateur LLM → hunters | 1 | l'IA propose des familles, pas l'opérateur | moyen |
| **P4** | Data tick liquidations (recorder Tokyo) pour activer Hawkes | 1 | seule famille au moteur prouvé mais sans data | moyen |
| **P5** | Robustesse code : pyright clean, label « beta_deguise » précisé | 1 | qualité | faible |
| **P6** | GATE B : `robustness.py` + `forward_runner.py` + forward_log | 2 | à construire quand un survivant existe | moyen |
| **P7** | GATE C : `paper_executor` → `executor`/`sizing`/`risk` → `monitor`/kill-switch | 3 | à construire après un PASS B + GO humain | élevé |

**P6/P7 ne démarrent PAS tant qu'aucun edge n'a passé GATE A dans l'univers live.**

## 12. Ce qu'on ne fait PAS (scope négatif)

- **Pas d'auto-déploiement** : aucun edge ne touche du capital sans les 3 gates + GO humain.
- **Pas de bot live « autonome »** qui trade tout seul sans surveillance (monitor + kill-switch obligatoires).
- **Pas de baisse de seuils**, jamais, sur aucun des 3 gates.
- **Pas de saut de gate** (jamais B→skip, jamais aller en live sur un edge non durci).
- **Pas de Numerai comme mission** (gardé comme outil/débouché). **Pas de marchés de prédiction.**
- **Pas de promesse de gains** (aucun edge prouvé à ce jour).
