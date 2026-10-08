"""Parity test: the C++ engine must make the same decisions and keep the same books as the Python reference (main.py).

Both run side by side on identical synthetic markets for many random cycles, including regime flips, missing
tickers, unchanged prices, small drift and big drift. After every cycle we compare the decision category, the ledger,
the equity/benchmark log, the trade log and the benchmark baseline.

Usage: python tests/parity_test.py [path/to/quantbot] [cycles] [seed]
"""
import contextlib
import io
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import types

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BINARY = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(ROOT, 'quantbot')
CYCLES = int(sys.argv[2]) if len(sys.argv) > 2 else 120
SEED = int(sys.argv[3]) if len(sys.argv) > 3 else 1

# ---- fake yfinance for the Python reference -------------------------------------------------------------
MARKET = {}
def fake_download(tickers, period=None, interval=None, progress=None, **kw):
    if isinstance(tickers, str):
        return pd.DataFrame({('Close', tickers): MARKET[tickers]}) if tickers in MARKET else pd.DataFrame()
    cols = {('Close', t): MARKET[t] for t in tickers if t in MARKET}
    return pd.DataFrame(cols) if cols else pd.DataFrame()
yf = types.ModuleType('yfinance'); yf.download = fake_download
sys.modules['yfinance'] = yf

COINS = ['ETH-USD', 'SOL-USD', 'LINK-USD', 'AVAX-USD', 'NEAR-USD', 'ADA-USD', 'DOT-USD']
MACRO = ['^GSPC', 'GC=F', 'DX-Y.NYB', 'CL=F']
days = pd.date_range(end='2026-10-08', periods=60, freq='D')
bdays = days[days.dayofweek < 5]
rng = np.random.default_rng(SEED)
pyrand = random.Random(SEED)

def walk(idx, drift, vol, start, drift_tail=None, tail=0):
    """Random walk; optionally the last `tail` bars use a different drift so prices cross their moving averages."""
    d = np.full(len(idx), drift, dtype=float)
    if drift_tail is not None and tail:
        d[-tail:] = drift_tail
    return pd.Series(start * np.exp(np.cumsum(rng.normal(d, vol))), index=idx)

def edge_bar(series):
    """Put the last close strictly BETWEEN the 20- and 21-bar EMAs of the earlier bars, so a filter that uses the
    wrong EMA span reaches the opposite decision. This is what lets the test catch off-by-one parameter bugs."""
    prev = series.iloc[:-1]
    e20 = prev.ewm(span=20, adjust=False).mean().iloc[-1]
    e21 = prev.ewm(span=21, adjust=False).mean().iloc[-1]
    out = series.copy()
    out.iloc[-1] = e20 + 0.5 * (e21 - e20)
    return out

def build(bull):
    MARKET.clear()
    btc = walk(days, 0.006 if bull else -0.006, 0.012, 60000,
               drift_tail=rng.uniform(-0.012, 0.012), tail=int(rng.integers(2, 22)))
    if pyrand.random() < 0.5:
        btc = edge_bar(btc)
    MARKET['BTC-USD'] = btc
    for c in COINS:
        MARKET[c] = walk(days, rng.normal(0.004, 0.008), 0.014, 100)
    for t in MACRO:
        sign = 1 if pyrand.random() < 0.7 else -1  # some macro assets move against BTC
        base = sign * btc.reindex(bdays).pct_change().fillna(0) * 0.5 + rng.normal(0, 0.004, len(bdays))
        drift = np.full(len(bdays), 0.002 if bull else -0.002)
        k = int(rng.integers(2, 18))
        drift[-k:] = rng.uniform(-0.01, 0.01)
        MARKET[t] = pd.Series(100 * np.exp(np.cumsum(base + drift)), index=bdays)
        if pyrand.random() < 0.5:
            MARKET[t] = edge_bar(MARKET[t])

def write_csvs(data_dir):
    os.makedirs(data_dir, exist_ok=True)
    for f in os.listdir(data_dir):
        os.remove(os.path.join(data_dir, f))
    for t, s in MARKET.items():
        name = re.sub(r'[^A-Za-z0-9._-]', '_', t)
        with open(os.path.join(data_dir, name + '.csv'), 'w') as f:
            f.write('Date,Close\n')
            for d, v in s.items():
                f.write(f'{d.strftime("%Y-%m-%d")},{float(v)!r}\n')

def category(text):
    for key, label in [('Rebalanced', 'trade'), ('Same picks', 'hold-picks'), ('already in cash', 'hold-cash'),
                       ('Regime check failed', 'regime-fail'), ('Skipping this cycle', 'skip-missing-price')]:
        if key in text:
            return label
    return 'unknown'

def close(a, b, tol):
    return abs(a - b) <= tol + 1e-9 * max(abs(a), abs(b))

def compare(dir_py, dir_cpp, step):
    errs = []
    ledger_paths = [os.path.join(d, 'portfolio_ledger.csv') for d in (dir_py, dir_cpp)]
    if os.path.exists(ledger_paths[0]) != os.path.exists(ledger_paths[1]):
        errs.append('portfolio_ledger.csv exists in only one run')
    elif os.path.exists(ledger_paths[0]):  # neither exists until the first trade
        lp, lc = (pd.read_csv(p) for p in ledger_paths)
        if list(lp.Asset) != list(lc.Asset) or list(lp.Asset_Type) != list(lc.Asset_Type):
            errs.append(f'ledger assets differ: {list(lp.Asset)} vs {list(lc.Asset)}')
        else:
            for col in ['Quantity', 'Avg_Price', 'Total_Value']:
                for a, b in zip(lp[col], lc[col]):
                    if not close(a, b, 1e-6):
                        errs.append(f'ledger {col}: {a} vs {b}')
    pp, pc = (pd.read_csv(os.path.join(d, 'alpha_performance.csv')) for d in (dir_py, dir_cpp))
    if len(pp) != len(pc):
        errs.append(f'perf rows {len(pp)} vs {len(pc)}')
    else:
        for col in ['Active_Bot_Value', 'Benchmark_BnH_Value', 'Alpha_USDT', 'Alpha_Percent']:
            for a, b in zip(pp[col], pc[col]):
                if not close(a, b, 0.0151):
                    errs.append(f'perf {col}: {a} vs {b}')
    tp, tc = (os.path.join(d, 'trades.csv') for d in (dir_py, dir_cpp))
    if os.path.exists(tp) != os.path.exists(tc):
        errs.append('trades.csv exists in only one run')
    elif os.path.exists(tp):
        a, b = pd.read_csv(tp), pd.read_csv(tc)
        if len(a) != len(b) or list(a.Asset) != list(b.Asset) or list(a.Side) != list(b.Side):
            errs.append('trades differ in rows/assets/sides')
        else:
            for col in ['Quantity', 'Price', 'Fee_USDT']:
                for x, y in zip(a[col], b[col]):
                    if not close(x, y, 1e-6):
                        errs.append(f'trades {col}: {x} vs {y}')
    jp, jc = (json.load(open(os.path.join(d, 'benchmark_baseline.json'))) for d in (dir_py, dir_cpp))
    if not close(jp['capital'], jc['capital'], 0.0101):
        errs.append(f"baseline capital {jp['capital']} vs {jc['capital']}")
    for key in ('prices', 'last_prices'):
        if set(jp[key]) != set(jc[key]):
            errs.append(f'baseline {key} coins differ')
        else:
            for c in jp[key]:
                if not close(jp[key][c], jc[key][c], 1e-9):
                    errs.append(f'baseline {key}[{c}] differs')
    return [f'step {step}: {e}' for e in errs[:5]]

if not os.path.exists(BINARY):
    sys.exit(f'binary not found: {BINARY} (build it first)')

dir_py, dir_cpp, data_dir = tempfile.mkdtemp(), tempfile.mkdtemp(), tempfile.mkdtemp()
sys.path.insert(0, ROOT)
import main as reference  # the Python reference; paths are relative to cwd

counts, all_errs = {}, []
build(True)
for step in range(1, CYCLES + 1):
    mode = pyrand.random()
    if step == 1 or mode < 0.40:
        build(pyrand.random() < 0.55)                                  # fresh market, random regime
    elif mode < 0.60:
        pass                                                           # identical prices
    else:
        for c in COINS:                                                # last bar moves a little or a lot
            if pyrand.random() < 0.6:
                s = MARKET[c].copy()
                s.iloc[-1] *= 1 + pyrand.choice([0.01, 0.04, 0.12, -0.03, -0.15, 0.30, 0.45, -0.25, -0.35])
                MARKET[c] = s
    removed = []
    roll = pyrand.random()
    if roll < 0.06 and MACRO:   removed = [pyrand.choice(MACRO)]
    elif roll < 0.10:           removed = [pyrand.choice(COINS)]
    elif roll < 0.12:           removed = ['BTC-USD']
    elif roll < 0.14:           removed = list(MACRO)
    saved = {t: MARKET.pop(t) for t in removed if t in MARKET}
    write_csvs(data_dir)

    ts = f'2026-10-08 {step // 60:02d}:{step % 60:02d}:00'
    os.chdir(dir_py)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        reference.current_time_str = ts
        reference.execute_trading_cycle()
    out_py = buf.getvalue()
    res = subprocess.run([BINARY, '--data-dir', data_dir, '--now', ts], cwd=dir_cpp, capture_output=True, text=True)
    if res.returncode != 0:
        all_errs.append(f'step {step}: C++ exited {res.returncode}: {res.stderr.strip()}')
        break
    cat_py, cat_cpp = category(out_py), category(res.stdout)
    counts[cat_cpp] = counts.get(cat_cpp, 0) + 1
    if cat_py != cat_cpp:
        all_errs.append(f'step {step}: decision differs: python={cat_py} c++={cat_cpp}')
    elif os.path.exists(os.path.join(dir_py, 'benchmark_baseline.json')):
        all_errs += compare(dir_py, dir_cpp, step)
    MARKET.update(saved)
    if all_errs:
        break

print(f'cycles run: {sum(counts.values())}, decision mix: {counts}')
if all_errs:
    print('PARITY FAILED'); [print('  ' + e) for e in all_errs]; sys.exit(1)
print('PARITY OK: C++ matches the Python reference on every cycle')
