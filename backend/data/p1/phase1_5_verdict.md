# HyperDex Phase 1.5 — Verdict GATE refiné

_Run : 2026-05-23T08:55:05.602031+00:00_

## Méthodo
- Univers : leaderboard filtré (accountVal $5,000-$100,000,000, monthPnL>0 OU allTimePnL>0, weeklyVol>$0, hard_cap 10000)
- Fenêtre : 90j  /  Holdout OOS : 30j
- Filtre hold : 5min ≤ median ≤ 48h (>48h **EXCLU** par doctrine opérateur)
- Métriques : closedPnl total, Sharpe par fill, max DD, hold-time, sub-window consistency (3 sous-périodes train)
- Bonferroni : alpha=0.05/3479=0.000014, z_crit=4.34

## Résultats
- Wallets retenus (n>=50, hold ≤ 48h) : **3479**
- Copyables (train+/holdout+/hold_n>=20) : **1126**
- + Sub-window consistency (train 3/3 + holdout +) : **335**
- + Bonferroni : **97 VALIDÉS**

## Test agrégé
- Top quartile par **train PnL** → holdout : $+16,225,860  vs bottom : $+14,051,755  spread=$+2,174,105
- Top quartile par **Sharpe** → holdout : $+34,494,778  vs bottom : $-8,087,944  spread=$+42,582,722

## VERDICT
### **OUI — 97 traders validés** (consistance + Bonferroni + rang Sharpe prédit OOS). → Phase 2.

## Traders VALIDÉS (passent les 3 tests)
| wallet | n | profile | hold_med (min) | train | holdout | Sharpe | max_DD | t_stat |
|---|---|---|---|---|---|---|---|---|
| 0x25608292189b | 2150 | swing<=48h | 1197 | $+227,766 | $+33,496 | 33.39 | $535 | 10.65 |
| 0x0e3f5bb797e3 | 10000 | intraday | 32 | $+45,423 | $+24,097 | 25.76 | $805 | 14.24 |
| 0x1005996ecc88 | 8771 | intraday | 19 | $+48,129 | $+19,217 | 24.88 | $7,491 | 13.84 |
| 0x02159593e155 | 9878 | intraday | 19 | $+85,756 | $+35,293 | 24.44 | $14,671 | 13.78 |
| 0xa8123bfb8301 | 9173 | intraday | 19 | $+62,821 | $+25,343 | 24.38 | $10,676 | 13.96 |
| 0x4221ae35dd1c | 3239 | swing<=48h | 540 | $+40,997 | $+6,280 | 23.89 | $143 | 10.00 |
| 0xc16b8ddcef47 | 4024 | swing<=48h | 1922 | $+243,117 | $+18,878 | 19.89 | $9,507 | 14.87 |
| 0x1aa5b4b70b21 | 3497 | intraday | 6 | $+10,997 | $+1,045 | 18.38 | $410 | 8.82 |
| 0xfed459402e31 | 3953 | intraday | 141 | $+14,945 | $+5,518 | 16.82 | $89 | 8.51 |
| 0x9bd642632487 | 10000 | swing<=48h | 261 | $+47,893 | $+69,427 | 15.92 | $945 | 11.35 |
| 0x3bbe8e02f72b | 10000 | intraday | 151 | $+100,979 | $+38,072 | 15.67 | $13,837 | 5.23 |
| 0x5e77acddecd8 | 717 | intraday | 30 | $+20,280 | $+14,091 | 14.29 | $544 | 11.35 |
| 0xb65dd7c56afb | 2640 | intraday | 85 | $+1,350 | $+1,376 | 14.14 | $21 | 8.77 |
| 0xfb51e66f1923 | 4252 | swing<=48h | 322 | $+29,000 | $+15,305 | 13.40 | $561 | 8.31 |
| 0x8e9c5b74a5c0 | 2560 | intraday | 76 | $+8,197 | $+39,393 | 13.09 | $0 | 11.27 |
| 0xd9ffc44a0324 | 2987 | intraday | 37 | $+29,651 | $+9,206 | 13.07 | $4 | 6.66 |
| 0x04a97ae7f350 | 4048 | intraday | 107 | $+256,201 | $+40,470 | 12.66 | $897 | 4.62 |
| 0x129f8031ada0 | 963 | intraday | 36 | $+1,205 | $+970 | 12.43 | $40 | 8.25 |
| 0x11eee2e0a613 | 8288 | intraday | 69 | $+170,496 | $+57,103 | 11.85 | $3,066 | 6.44 |
| 0xbe10fd36393c | 10000 | swing<=48h | 244 | $+240,629 | $+65,139 | 11.84 | $10,874 | 4.99 |
| 0x9417f0013ad7 | 2719 | intraday | 88 | $+11,190 | $+6,408 | 11.70 | $582 | 5.40 |
| 0x5d9d19a3e500 | 5453 | swing<=48h | 1052 | $+67,728 | $+72,050 | 11.37 | $3,171 | 7.87 |
| 0xb3b60a1fd4d8 | 3444 | swing<=48h | 871 | $+136,874 | $+92,127 | 11.31 | $13,596 | 7.23 |
| 0x81f6b887657b | 8625 | intraday | 15 | $+17,332 | $+4,654 | 11.28 | $3,367 | 8.26 |
| 0xf97ad6704bae | 7776 | intraday | 140 | $+105,239 | $+57,780 | 11.27 | $18,895 | 5.32 |
| 0x34d3a17095ef | 4538 | swing<=48h | 311 | $+104,152 | $+97,879 | 11.06 | $22,450 | 7.09 |
| 0xc0cecc9cc4ab | 1167 | intraday | 150 | $+1,326 | $+3,039 | 10.68 | $1 | 8.27 |
| 0xacf09af6853b | 4233 | intraday | 46 | $+9,021 | $+4,053 | 10.57 | $1,666 | 7.38 |
| 0x64174450c492 | 10000 | swing<=48h | 1092 | $+24,570 | $+18,903 | 10.55 | $3,102 | 6.89 |
| 0x47add9a56df6 | 603 | swing<=48h | 1319 | $+4,193 | $+2,858 | 10.51 | $41 | 7.08 |

## Traders consistants (sub-window OK + train+/holdout+) non-Bonferroni
| wallet | n | profile | hold_med | train | holdout | Sharpe | t_stat |
|---|---|---|---|---|---|---|---|
| 0x64bddab5f13a | 10000 | swing<=48h | 965 | $+249,184 | $+24,839 | 8.03 | 2.18 |
| 0xb0337ad3871e | 793 | swing<=48h | 503 | $+1,368 | $+519 | 0.58 | 0.16 |
| 0x30031f21e492 | 6220 | swing<=48h | 512 | $+42,980 | $+1,521 | 11.06 | 2.31 |
| 0x13a4d58ccb2c | 2000 | swing<=48h | 441 | $+68,320 | $+4,372 | 9.66 | 2.75 |
| 0xd10e2b1b6600 | 9014 | swing<=48h | 370 | $+210,996 | $+131,879 | 8.51 | 4.26 |
| 0x535191d6d499 | 2296 | swing<=48h | 483 | $+60,322 | $+16,516 | 3.52 | 1.65 |
| 0x0afe2b931a1f | 427 | swing<=48h | 627 | $+35,215 | $+97,207 | 4.80 | 3.89 |
| 0x938fcf34101e | 1711 | swing<=48h | 1834 | $+7,927 | $+36,252 | 3.45 | 3.01 |
| 0x3e4515b00413 | 3223 | intraday | 170 | $+184,767 | $+16,607 | 2.06 | 1.51 |

## Distribution profile (population N=3479)
- swing<=48h : 1856
- intraday : 1623