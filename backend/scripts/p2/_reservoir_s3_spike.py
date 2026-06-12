#!/usr/bin/env python3
"""Reservoir S3 spike — 14j cohort discovery HORS cohort 232.

Stream-process HL official archive s3://hl-mainnet-node-data/node_fills_by_block/
Score per-wallet : sum(closedPnl - fee) × n_closes × wr.
Filter user ∉ cohort 232. Output top 200 contributors.

Usage:
    python scripts/p2/_reservoir_s3_spike.py --days 14
    python scripts/p2/_reservoir_s3_spike.py --days 1  # smoke
"""
import argparse
import csv
import gc
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
import lz4.frame
from botocore.exceptions import ClientError

BUCKET = 'hl-mainnet-node-data'
PREFIX = 'node_fills_by_block/hourly'
DATA_DIR = Path('/home/dexter/hyperdex/backend/data/p2_reservoir')
DATA_DIR.mkdir(parents=True, exist_ok=True)

COHORT_CSV = Path('/home/dexter/hyperdex/backend/data/p1/consistent_set.csv')

def load_cohort_232():
    """Load current cohort addresses (lowercase)."""
    addrs = set()
    if COHORT_CSV.exists():
        with COHORT_CSV.open() as f:
            r = csv.DictReader(f)
            for row in r:
                a = (row.get('addr') or row.get('address') or '').lower()
                if a.startswith('0x') and len(a) == 42:
                    addrs.add(a)
    return addrs

def process_lz4_into_stats(s3, key, wallet_stats):
    """Stream-process 1 LZ4 file, accumulate stats. Return (size_mb, n_events)."""
    local = DATA_DIR / f"_tmp_{key.split('/')[-2]}_{key.split('/')[-1]}"
    n_events = 0
    size_mb = 0.0
    try:
        s3.download_file(BUCKET, key, str(local),
                        ExtraArgs={'RequestPayer': 'requester'})
        size_mb = local.stat().st_size / 1024 / 1024

        with lz4.frame.open(local, 'rb') as f:
            buf = b''
            for chunk in iter(lambda: f.read(65536), b''):
                buf += chunk
                while b'\n' in buf:
                    line, _, buf = buf.partition(b'\n')
                    if not line.strip():
                        continue
                    try:
                        obj = json.loads(line)
                        for event in obj.get('events', []):
                            if len(event) == 2:
                                user_addr, fill = event
                                if not isinstance(fill, dict):
                                    continue
                                user = user_addr.lower() if user_addr else ''
                                if not user.startswith('0x'):
                                    continue
                                try:
                                    pnl = float(fill.get('closedPnl', 0))
                                    fee = float(fill.get('fee', 0))
                                except (TypeError, ValueError):
                                    continue
                                net = pnl - fee
                                t = int(fill.get('time', 0))
                                w = wallet_stats[user]
                                w['pnl_net'] += net
                                w['n_fills'] += 1
                                if net > 0:
                                    w['n_wins'] += 1
                                    w['sum_wins'] += net
                                    if net > w['max_win']:
                                        w['max_win'] = net
                                elif net < 0:
                                    w['n_losses'] += 1
                                    w['sum_losses'] += abs(net)
                                    if abs(net) > w['max_loss']:
                                        w['max_loss'] = abs(net)
                                # Welford online std (pnl per fill)
                                w['n_obs'] += 1
                                delta = net - w['mean']
                                w['mean'] += delta / w['n_obs']
                                delta2 = net - w['mean']
                                w['m2'] += delta * delta2
                                # Time tracking for active span
                                if t > 0:
                                    if w['first_t'] == 0 or t < w['first_t']:
                                        w['first_t'] = t
                                    if t > w['last_t']:
                                        w['last_t'] = t
                                w['coins'].add(fill.get('coin', ''))
                                n_events += 1
                    except json.JSONDecodeError:
                        pass
        return size_mb, n_events
    except ClientError as e:
        print(f"  ERR DL {key}: {e.response['Error']['Code']}")
        return 0.0, 0
    finally:
        if local.exists():
            local.unlink()  # clean tmp

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=1)
    ap.add_argument('--end-date', type=str, default=None,
                   help='YYYYMMDD (default: yesterday)')
    args = ap.parse_args()

    end_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime('%Y%m%d') \
                if not args.end_date else args.end_date
    days = []
    for i in range(args.days):
        d = (datetime.strptime(end_date, '%Y%m%d') - timedelta(days=i)).strftime('%Y%m%d')
        days.append(d)
    days.sort()  # ascending
    print(f"=== Reservoir S3 spike : {args.days}d ({days[0]} → {days[-1]}) ===")

    cohort_232 = load_cohort_232()
    print(f"Cohort 232 baseline (CSV): {len(cohort_232)} addresses")

    s3 = boto3.client('s3', region_name='us-east-1')

    wallet_stats = defaultdict(lambda: {
        'pnl_net': 0.0, 'n_fills': 0, 'n_wins': 0, 'n_losses': 0,
        'sum_wins': 0.0, 'sum_losses': 0.0, 'max_win': 0.0, 'max_loss': 0.0,
        'mean': 0.0, 'm2': 0.0, 'n_obs': 0,  # Welford running variance
        'first_t': 0, 'last_t': 0,
        'coins': set(),
    })
    total_mb_dl = 0
    total_events = 0
    t0 = time.time()

    for day_idx, day in enumerate(days):
        # List hourly files
        r = s3.list_objects_v2(
            Bucket=BUCKET, Prefix=f"{PREFIX}/{day}/",
            MaxKeys=100, RequestPayer='requester'
        )
        files = [o['Key'] for o in r.get('Contents', [])]
        day_size_mb = sum(o['Size'] for o in r.get('Contents', [])) / 1024 / 1024
        print(f"\n[{day_idx+1}/{len(days)}] day {day}: {len(files)} files, {day_size_mb:.0f} MB")

        for j, key in enumerate(files):
            if j % 6 == 0:
                elapsed = time.time() - t0
                print(f"  [{j+1}/{len(files)}] {key.split('/')[-1]} (elapsed {elapsed:.0f}s, "
                      f"events={total_events}, wallets={len(wallet_stats)})")
            size_mb, n_ev = process_lz4_into_stats(s3, key, wallet_stats)
            total_mb_dl += size_mb
            total_events += n_ev

            # (accumulation faite dans process_lz4_into_stats)
        gc.collect()
        print(f"  day {day} done. cumulative: {len(wallet_stats)} unique wallets, {total_events} events")

    # Score + filter v2 (wider, save ALL profitable WR<90%)
    print(f"\n=== Scoring {len(wallet_stats)} wallets (filtre wider) ===")
    scored = []
    rejected_n_low = 0
    rejected_unprofitable = 0
    rejected_wr_low = 0
    rejected_wr_high = 0
    for user, stats in wallet_stats.items():
        n_resolved = stats['n_wins'] + stats['n_losses']
        n_fills = stats['n_fills']

        # FILTER opérateur 2026-05-28 :
        # - profitable (pnl_net > 0)
        # - WR ∈ [55%, 90%) — sweet spot copyable retail (exclude lottery + MM/wash)
        # - n_fills ≥ 20 (sample minimum statistique)
        if n_fills < 20:
            rejected_n_low += 1
            continue
        if stats['pnl_net'] <= 0:
            rejected_unprofitable += 1
            continue
        wr = stats['n_wins'] / n_resolved if n_resolved > 0 else 0
        if wr < 0.55:
            rejected_wr_low += 1
            continue
        if wr >= 0.90:
            rejected_wr_high += 1
            continue

        n_coins = len(stats['coins'])
        pnl_net = stats['pnl_net']
        avg_pnl_per_trade = pnl_net / n_fills if n_fills > 0 else 0

        # Welford std + Sharpe approx
        variance = stats['m2'] / stats['n_obs'] if stats['n_obs'] > 1 else 0
        std_pnl = variance ** 0.5
        sharpe_approx = (stats['mean'] / std_pnl) if std_pnl > 0 else 0
        # Active span en jours
        active_days = (stats['last_t'] - stats['first_t']) / 86400000 if stats['first_t'] > 0 else 0
        fills_per_day = n_fills / active_days if active_days > 0 else n_fills

        # win/loss ratio
        avg_win = stats['sum_wins'] / stats['n_wins'] if stats['n_wins'] > 0 else 0
        avg_loss = stats['sum_losses'] / stats['n_losses'] if stats['n_losses'] > 0 else 0
        win_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 0

        # Tier classification (v2 — only categorize what we kept)
        if wr < 0.40:
            tier = 'low_wr_winner'  # WR bas mais profitable (variance haute)
        elif wr < 0.55:
            tier = 'moderate_wr'    # 40-55% WR profitable
        elif n_coins >= 3 and 50 <= n_fills <= 3000:
            tier = 'multi_coin_copyable'  # sweet spot diversifié
        elif n_coins == 1:
            tier = 'specialist_single_coin'  # 1 coin spécialiste
        else:
            tier = 'standard'

        # Composite score multi-dim
        # Pondère : Sharpe (consistance) × edge_per_trade × WR × log(N) × diversification
        import math
        log_n = math.log10(n_fills + 1)
        diversif = min(n_coins / 5.0, 1.0)  # cap à 1 si ≥5 coins
        composite = sharpe_approx * avg_pnl_per_trade * wr * log_n * (0.5 + 0.5 * diversif)

        scored.append({
            'addr': user,
            'tier': tier,
            'pnl_net': round(pnl_net, 2),
            'avg_pnl_per_trade': round(avg_pnl_per_trade, 4),
            'std_pnl': round(std_pnl, 4),
            'sharpe_approx': round(sharpe_approx, 3),
            'max_win': round(stats['max_win'], 2),
            'max_loss': round(stats['max_loss'], 2),
            'avg_win': round(avg_win, 4),
            'avg_loss': round(avg_loss, 4),
            'win_loss_ratio': round(win_loss_ratio, 3),
            'n_fills': n_fills,
            'n_wins': stats['n_wins'],
            'n_losses': stats['n_losses'],
            'wr': round(wr, 4),
            'n_coins': n_coins,
            'active_days': round(active_days, 2),
            'fills_per_day': round(fills_per_day, 1),
            'composite_score': round(composite, 3),
            'pnl_x_wr': round(pnl_net * wr, 2),  # alt scoring
            'in_cohort_232': user in cohort_232,
        })
    print(f"  rejected n_fills<20: {rejected_n_low:,}")
    print(f"  rejected unprofitable: {rejected_unprofitable:,}")
    print(f"  rejected WR<55%: {rejected_wr_low:,}")
    print(f"  rejected WR≥90%: {rejected_wr_high:,}")
    print(f"  → kept (WR 55-90% profitable): {len(scored):,}")

    # Sort by composite_score (Sharpe × edge × WR × log(N) × diversif)
    scored.sort(key=lambda x: x['composite_score'], reverse=True)

    # Tier distribution
    tier_counts = defaultdict(int)
    for w in scored:
        tier_counts[w['tier']] += 1
    print(f"\n=== Tier distribution (filtre wider) ===")
    for tier, n in sorted(tier_counts.items(), key=lambda x: -x[1]):
        print(f"  {tier:30s} : {n:>7,}")

    cohort_in_top = [w for w in scored if w['in_cohort_232']]
    new_top = [w for w in scored if not w['in_cohort_232']]

    print(f"\n=== Top 20 GLOBAL (par composite_score) ===")
    for i, w in enumerate(scored[:20]):
        in_c = '🟢' if w['in_cohort_232'] else '🆕'
        print(f"  {i+1:2d}. {in_c} {w['addr'][:14]}... tier={w['tier']:20s} sharpe={w['sharpe_approx']:>5.2f} pnl=${w['pnl_net']:+,.0f} edge=${w['avg_pnl_per_trade']:+.2f} n={w['n_fills']:>4d} wr={w['wr']:.1%} coins={w['n_coins']} wlr={w['win_loss_ratio']:.2f}")

    print(f"\n=== Top 30 NOUVEAUX hors cohort 232 (par composite_score) ===")
    for i, w in enumerate(new_top[:30]):
        print(f"  {i+1:2d}. {w['addr'][:14]}... tier={w['tier']:20s} sharpe={w['sharpe_approx']:>5.2f} pnl=${w['pnl_net']:+,.0f} edge=${w['avg_pnl_per_trade']:+.2f} n={w['n_fills']:>4d} wr={w['wr']:.1%} coins={w['n_coins']}")

    print(f"\n=== Top 10 IN cohort 232 (par composite_score) ===")
    for i, w in enumerate(cohort_in_top[:10]):
        print(f"  {i+1:2d}. {w['addr'][:14]}... tier={w['tier']:20s} sharpe={w['sharpe_approx']:>5.2f} pnl=${w['pnl_net']:+,.0f} edge=${w['avg_pnl_per_trade']:+.2f} n={w['n_fills']:>4d} wr={w['wr']:.1%} coins={w['n_coins']}")

    # Gap analysis : top 10 cohort vs top 10 nouveaux (composite)
    sum_cohort_top10 = sum(w['pnl_net'] for w in cohort_in_top[:10])
    sum_new_top10 = sum(w['pnl_net'] for w in new_top[:10])
    gap_copyable_pct = (sum_new_top10 / sum_cohort_top10 * 100) if sum_cohort_top10 > 0 else 0

    sum_cohort_copyable_top10 = sum_cohort_top10
    sum_new_copyable_top10 = sum_new_top10

    print(f"\n=== Gap analysis COPYABLE seul ===")
    print(f"  Sum top 10 cohort copyable PnL: ${sum_cohort_copyable_top10:+,.2f}")
    print(f"  Sum top 10 nouveaux copyable PnL: ${sum_new_copyable_top10:+,.2f}")
    print(f"  Gap copyable = {gap_copyable_pct:.1f}%")
    if gap_copyable_pct > 30:
        print(f"  ⚠️ GAP > 30% → ÉTL complet justifié")
    else:
        print(f"  ✓ GAP < 30% → cohort 232 suffisant pour Slice 1")

    # Write output v2 — save ALL filtered wallets
    out = DATA_DIR / f'reservoir_spike_{args.days}d_v2.json'
    out.write_text(json.dumps({
        'days': args.days,
        'end_date': end_date,
        'total_events': total_events,
        'total_mb_dl': total_mb_dl,
        'wallets_seen': len(wallet_stats),
        'cohort_232_size': len(cohort_232),
        'rejected_n_low': rejected_n_low,
        'rejected_unprofitable': rejected_unprofitable,
        'rejected_wr_low': rejected_wr_low,
        'rejected_wr_high': rejected_wr_high,
        'kept': len(scored),
        'tier_counts': dict(tier_counts),
        'gap_top10_pct': gap_copyable_pct,
        'sum_cohort_top10_pnl': sum_cohort_top10,
        'sum_new_top10_pnl': sum_new_top10,
        'all_scored': scored,  # ALL wallets matching wider filter, sorted by composite_score
    }, indent=2, default=str))
    print(f"\n→ wrote {out}")

    elapsed = time.time() - t0
    est_egress = total_mb_dl / 1024 * 0.09
    print(f"\n=== Stats ===")
    print(f"  Total DL: {total_mb_dl:.0f} MB ({total_mb_dl/1024:.2f} GB)")
    print(f"  Estimated AWS egress cost: ${est_egress:.2f}")
    print(f"  Total elapsed: {elapsed:.0f}s ({elapsed/60:.1f}min)")
    print(f"  Events processed: {total_events:,}")
    print(f"  Unique wallets: {len(wallet_stats):,}")

if __name__ == '__main__':
    sys.exit(main() or 0)
