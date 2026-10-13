"""Append one row to compare_live.csv (v1 vs v2 vs BTC vs VOO, all rebased to 100,000 on the first row) and redraw
comparison_chart.png. Read-only on the strategies' own files.  Usage: python compare_live.py [data_v2_dir]
"""
import csv, json, os, sys
from datetime import datetime, timezone

import pandas as pd

import compare_strategies

DATA = sys.argv[1] if len(sys.argv) > 1 else 'data_v2'
BASE, LOG, PNG = 'compare_live_baseline.json', 'compare_live.csv', 'comparison_chart.png'
CAPITAL = 100000.0


def last_close(t):
    df = pd.read_csv(os.path.join(DATA, t + '.csv'))
    return float(df['Close'].iloc[-1])


def v1_value():
    led = pd.read_csv('portfolio_ledger.csv')
    return float(led.loc[led['Asset_Type'] != 'BENCHMARK', 'Total_Value'].sum())


def v2_value():
    eq = pd.read_csv('v2/equity.csv')
    return float(eq['V2_Value'].iloc[-1])


v1, v2, btc, voo = v1_value(), v2_value(), last_close('BTC-USD'), last_close('VOO')
if os.path.exists(BASE):
    b = json.load(open(BASE))
else:
    b = {'v1': v1, 'v2': v2, 'btc': btc, 'voo': voo, 'started': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}
    json.dump(b, open(BASE, 'w'), indent=1)
row = [datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'), CAPITAL * v1 / b['v1'], CAPITAL * v2 / b['v2'],
       CAPITAL * btc / b['btc'], CAPITAL * voo / b['voo']]
new = not os.path.exists(LOG)
with open(LOG, 'a', newline='') as f:
    w = csv.writer(f)
    if new:
        w.writerow(['Date', 'V1', 'V2', 'BTC', 'VOO'])
    w.writerow([row[0]] + [f'{x:.2f}' for x in row[1:]])
df = pd.read_csv(LOG)
if len(df) >= 3:
    compare_strategies.main(LOG, PNG, 'Live paper trading')
else:
    print(f'only {len(df)} rows so far, chart starts at 3')
