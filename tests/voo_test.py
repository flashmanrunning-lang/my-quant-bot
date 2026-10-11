"""Tests for the VOO (S&P 500 ETF) buy & hold comparison in the C++ engine.

Usage: python tests/voo_test.py [path/to/quantbot]
Each case runs the real binary against small CSV fixtures (no network, no Python reference needed).
"""
import csv
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BINARY = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(ROOT, 'quantbot')
COINS = ['ETH-USD', 'SOL-USD', 'LINK-USD', 'AVAX-USD', 'NEAR-USD', 'ADA-USD', 'DOT-USD']
results = []

def check(name, ok, detail=''):
    results.append(ok)
    print(('PASS ' if ok else 'FAIL ') + name + ('' if ok else f'   <- {detail}'))

def write_series(data, ticker, price):
    name = ''.join(c if c.isalnum() or c in '._-' else '_' for c in ticker)
    with open(os.path.join(data, name + '.csv'), 'w') as f:
        f.write('Date,Close\n')
        for i in range(60):
            f.write(f'2026-08-{(i % 28) + 1:02d},{price!r}\n' if False else f'2026-{8 + i // 30:02d}-{(i % 30) + 1:02d},{price!r}\n')

def setup_market(data, voo=None):
    os.makedirs(data, exist_ok=True)
    for c in COINS:
        write_series(data, c, 100.0)      # flat coins: no momentum picks, regime check fails (no BTC) -> bot just holds
    voo_path = os.path.join(data, 'VOO.csv')
    if voo is None:
        if os.path.exists(voo_path): os.remove(voo_path)
    else:
        write_series(data, 'VOO', voo)

def run(state, data, ts):
    r = subprocess.run([BINARY, '--data-dir', data, '--state-dir', state, '--now', ts], capture_output=True, text=True)
    if r.returncode != 0:
        print('binary failed:', r.stderr)
        sys.exit(1)
    return r.stdout

def rows(state):
    with open(os.path.join(state, 'alpha_performance.csv')) as f:
        return list(csv.reader(f))

def baseline(state):
    return json.load(open(os.path.join(state, 'benchmark_baseline.json')))

if not os.path.exists(BINARY):
    sys.exit(f'binary not found: {BINARY}')

# ---- A-D: normal life of the comparison -----------------------------------------------------------------
state, data = tempfile.mkdtemp(), tempfile.mkdtemp()
setup_market(data, voo=500.0)
run(state, data, '2026-10-08 00:00:00')
r = rows(state)
check('new CSV has the VOO_Hold_Value column', r[0][-1] == 'VOO_Hold_Value' and len(r[0]) == 6, r[0])
check('first row: VOO line starts level with the bot', r[1][1] == '100000.00' and r[1][5] == '100000.00', r[1])
b = baseline(state)
check('baseline stores VOO start price and capital', b.get('voo_start') == 500.0 and b.get('voo_capital') == 100000.0, b)

setup_market(data, voo=550.0)
run(state, data, '2026-10-08 03:00:00')
check('VOO +10% -> VOO line = start capital x 1.10', rows(state)[2][5] == '110000.00', rows(state)[2])
check('VOO start price is not reset by later runs', baseline(state)['voo_start'] == 500.0)

setup_market(data, voo=None)
out = run(state, data, '2026-10-08 06:00:00')
r = rows(state)
check('VOO data missing -> run still succeeds with an empty VOO cell', r[3][5] == '' and 'No VOO data' in out, r[3])

setup_market(data, voo=600.0)
run(state, data, '2026-10-08 09:00:00')
check('VOO data back -> line resumes from the SAME start price', rows(state)[4][5] == '120000.00', rows(state)[4])
check('every CSV row has the same number of fields', len({len(x) for x in rows(state)}) == 1)

# ---- E: migrating an old 5-column CSV and an old baseline with no VOO fields ------------------------------
state, data = tempfile.mkdtemp(), tempfile.mkdtemp()
setup_market(data, voo=500.0)
with open(os.path.join(state, 'alpha_performance.csv'), 'w') as f:
    f.write('Timestamp,Active_Bot_Value,Benchmark_BnH_Value,Alpha_USDT,Alpha_Percent\n'
            '2026-10-07 00:00:00,100000.0,100000.0,0.0,0.0\n2026-10-07 03:00:00,101000.0,100000.0,1000.0,1.0\n')
with open(os.path.join(state, 'portfolio_ledger.csv'), 'w') as f:
    f.write('Asset,Quantity,Avg_Price,Total_Value,Asset_Type\nUSDT,102578.83,1.0,102578.83,CASH\n')
json.dump({'started': '2026-10-07 00:00:00', 'capital': 100000.0, 'prices': {c: 100.0 for c in COINS},
           'last_prices': {c: 100.0 for c in COINS}}, open(os.path.join(state, 'benchmark_baseline.json'), 'w'))
run(state, data, '2026-10-08 00:00:00')
r = rows(state)
check('migration: header gains the VOO column', r[0][-1] == 'VOO_Hold_Value' and len(r[0]) == 6, r[0])
check('migration: old rows keep their values and get an empty VOO cell',
      r[1][:5] == ['2026-10-07 00:00:00', '100000.0', '100000.0', '0.0', '0.0'] and r[1][5] == '' and r[2][5] == '', r[1:3])
check('migration: VOO starts level with the bot value at that moment', r[3][5] == '102578.83', r[3])
b = baseline(state)
check('migration: original benchmark capital untouched, VOO fields added',
      b['capital'] == 100000.0 and b['voo_start'] == 500.0 and abs(b['voo_capital'] - 102578.83) < 0.011, b)

# ---- G: VOO missing on day one, appears later --------------------------------------------------------------
state, data = tempfile.mkdtemp(), tempfile.mkdtemp()
setup_market(data, voo=None)
run(state, data, '2026-10-08 00:00:00')
check('no VOO on day one -> empty cell, no VOO fields in baseline', rows(state)[1][5] == '' and 'voo_start' not in baseline(state))
setup_market(data, voo=480.0)
run(state, data, '2026-10-08 03:00:00')
check('VOO appears later -> starts level with the bot then', rows(state)[2][5] == '100000.00' and baseline(state)['voo_start'] == 480.0, rows(state)[2])

print(f'\n{sum(results)}/{len(results)} checks passed')
sys.exit(0 if all(results) else 1)
