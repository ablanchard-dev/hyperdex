"""Détail chiffré : status paper + propositions d'expansion cohorte."""
import json
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

POSITIONS = Path("/opt/app/hyperdex/backend/data/paper/positions.jsonl")
LAUNCHER = Path("/opt/app/hyperdex/backend/data/paper/launcher.log")
FILLS_JSONL = Path("/opt/app/hyperdex/backend/data/p1/fills_raw_p1_5.jsonl")
CSV_96 = Path("/opt/app/hyperdex/backend/data/p1/detailed_97.csv")

now = datetime.now(timezone.utc)

# === 1. paper status ===

# trouver le start ts du run actuel (dernier "HYPERDEX PAPER FULL P2")
start_line = None
if LAUNCHER.exists():
    for line in LAUNCHER.read_text().splitlines()[::-1]:
        if "HYPERDEX PAPER FULL P2" in line:
            m = re.match(r"\[([\d\-T:]+)\]", line)
            if m:
                start_line = m.group(1)
                break

if start_line:
    start_dt = datetime.fromisoformat(start_line).replace(tzinfo=timezone.utc)
    duration_h = (now - start_dt).total_seconds() / 3600
else:
    start_dt = None
    duration_h = 0

# parse positions.jsonl
opens = 0
closes = 0
fundings = 0
opens_by_trader = Counter()
closes_by_trader = Counter()
pnl_by_trader = defaultdict(float)
hold_by_trader_min = defaultdict(list)
total_pnl = 0.0
total_fees = 0.0
last_event_ts = None
if POSITIONS.exists():
    for line in POSITIONS.read_text().splitlines():
        try:
            ev = json.loads(line)
        except Exception:
            continue
        evt = ev.get("event")
        if evt == "open":
            opens += 1
            opens_by_trader[ev["trader"]] += 1
            last_event_ts = ev.get("open_ts_ms")
        elif evt == "close":
            closes += 1
            closes_by_trader[ev["trader"]] += 1
            pnl_by_trader[ev["trader"]] += float(ev.get("net_pnl", 0))
            hold_by_trader_min[ev["trader"]].append(
                float(ev.get("hold_ms", 0)) / 60000.0)
            total_pnl += float(ev.get("net_pnl", 0))
            total_fees += float(ev.get("fees_total", 0))
            last_event_ts = ev.get("exit_ts_ms")
        elif evt == "funding":
            fundings += 1

print("=" * 70)
print("PAPER STATUS")
print("=" * 70)
if start_dt:
    print(f"Started     : {start_dt.isoformat()}  (il y a {duration_h:.1f}h)")
print(f"Now         : {now.isoformat()}")
if last_event_ts:
    last_dt = datetime.fromtimestamp(last_event_ts / 1000, tz=timezone.utc)
    silence = (now - last_dt).total_seconds()
    print(f"Last event  : {last_dt.isoformat()}  (silence: {silence:.0f}s)")
print()
print(f"Total events     : opens={opens}  closes={closes}  funding={fundings}")
if duration_h > 0:
    print(f"Cadence/h        : opens={opens/duration_h:.1f}  closes={closes/duration_h:.1f}")
n_active_traders = len(opens_by_trader)
print(f"Wallets actifs   : {n_active_traders} / 70 ont au moins 1 open ({100*n_active_traders/70:.0f}%)")
print(f"Total PnL        : ${total_pnl:+.3f}")
print(f"Total fees       : ${total_fees:.3f}")
if closes:
    print(f"Avg PnL/close    : ${total_pnl/closes:+.4f}  (gross ≈ ${(total_pnl+total_fees)/closes:+.4f})")
print()

# === 2. wallets PAS encore tradés ===
print("=== Wallets non encore tradés (silencieux depuis start) ===")
print(f"Sur 70 cohorte : {70 - n_active_traders} silencieux")
print()

# === 3. expansion candidates depuis fills_raw_p1_5.jsonl ===
# pour chaque wallet, compter n_fills last 30j et last 7j
# (cache P1.5 a été fait il y a ~24h, donc "last 7d" = 7 derniers j du cache)

if not FILLS_JSONL.exists():
    print("Pas de cache fills_raw_p1_5.jsonl — skip expansion.")
    raise SystemExit(0)

# Charger les 96 Bonferroni-addresses (déjà dans cohorte)
existing_addrs = set()
if CSV_96.exists():
    import csv
    with open(CSV_96) as fh:
        for r in csv.DictReader(fh):
            existing_addrs.add(r["addr"].lower())
print(f"Cohorte Bonferroni actuelle : {len(existing_addrs)} adresses")

# Pour chaque wallet du cache : compter fills last 30d et last 7d
# cache reflète l'instant où on a fait le P1.5 (hier ~).
# On considère "7 derniers jours" relatif à NOW.
ts_30d = (now - timedelta(days=30)).timestamp() * 1000
ts_7d = (now - timedelta(days=7)).timestamp() * 1000

print(f"Scan fills_raw_p1_5.jsonl (stream)...")
candidates = []  # (addr, n_30d, n_7d, sum_pnl_30d, sum_pnl_7d)
n_scanned = 0
with open(FILLS_JSONL) as fh:
    for line in fh:
        try:
            obj = json.loads(line)
        except Exception:
            continue
        addr = obj.get("wallet")
        fills = obj.get("fills", [])
        if not addr or not fills:
            continue
        n_scanned += 1
        if n_scanned % 1000 == 0:
            print(f"  ...{n_scanned}")
        n_30d = 0
        n_7d = 0
        pnl_30d = 0.0
        pnl_7d = 0.0
        for f in fills:
            try:
                ts = int(f.get("time", 0))
                cp = float(f.get("closedPnl", 0))
            except Exception:
                continue
            if ts >= ts_30d:
                n_30d += 1
                pnl_30d += cp
                if ts >= ts_7d:
                    n_7d += 1
                    pnl_7d += cp
        candidates.append((addr, n_30d, n_7d, pnl_30d, pnl_7d))

print(f"Scanned {n_scanned} wallets total.\n")

# Filtres pour candidats expansion:
# - PAS déjà dans cohorte
# - actif derniers 7j : n_7d >= 50 (vraiment actif)
# - PnL 30d > 0 (gagnant net sur 30j, pas un perdant)
exp_candidates = [
    c for c in candidates
    if c[0] not in existing_addrs
    and c[2] >= 50           # n_7d >= 50 = vraiment actif
    and c[3] > 0             # 30d PnL > 0
]

# Trier par PnL 7d desc
exp_candidates.sort(key=lambda x: -x[4])

print(f"=== EXPANSION : wallets PAS dans cohorte, actifs 7j (n_7d≥50), PnL 30d>0 ===")
print(f"Candidats trouvés : {len(exp_candidates)}\n")
print(f"{'wallet':<16}{'n_30d':>7}{'n_7d':>7}{'pnl_30d':>12}{'pnl_7d':>12}")
print("-" * 56)
for c in exp_candidates[:30]:
    print(f"{c[0][:14]:<16}{c[1]:>7}{c[2]:>7}{c[3]:>+12.0f}{c[4]:>+12.0f}")

# Stat agrégée
if exp_candidates:
    pnl_7d_sum = sum(c[4] for c in exp_candidates[:30])
    print(f"\nSum PnL_7d top-30 candidats : ${pnl_7d_sum:+,.0f}")
    n_7d_med = statistics.median(c[2] for c in exp_candidates[:30])
    print(f"Median n_7d top-30 : {n_7d_med:.0f} trades")
