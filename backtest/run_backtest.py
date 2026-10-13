"""Backtest v1 vs v2 on the committed price history (backtest/prices.csv).

v1 : the REAL C++ binary (cpp/quantbot) stepped one day at a time on a sliding 60-day data window, state kept in a temp dir.
v2 : the real C++ engine in --backtest mode.
Benchmarks: BTC buy-and-hold, equal-weight basket (buy-and-hold of the universe), VOO.
Universe A = live coins (hindsight-biased). Universe B = frozen top-10 of 2021-02-14 (fair).
Writes backtest/results_<A|B>.csv and backtest/sensitivity.csv.   Usage: python backtest/run_backtest.py [--no-sens]
"""
import os, re, shutil, subprocess, sys, tempfile
from concurrent.futures import ProcessPoolExecutor
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BT = os.path.join(ROOT, 'backtest')
START, CAPITAL = '2021-02-15', 100000.0
UNI = {'A': ['BTC-USD', 'ETH-USD', 'SOL-USD', 'LINK-USD', 'AVAX-USD', 'NEAR-USD', 'ADA-USD', 'DOT-USD'],
       'B': ['BTC-USD', 'ETH-USD', 'XRP-USD', 'ADA-USD', 'DOT-USD', 'BNB-USD', 'LTC-USD', 'LINK-USD', 'BCH-USD', 'XLM-USD']}
MACRO = ['^GSPC', 'GC=F', 'DX-Y.NYB', 'CL=F']


def build(src, out, coins=None):
    text = open(src).read()
    if coins:
        lst = ', '.join(f'"{c}"' for c in coins if c != 'BTC-USD')
        text = re.sub(r'const std::vector<std::string> COINS = \{[^}]*\};', f'const std::vector<std::string> COINS = {{{lst}}};', text, count=1)
    p = out + '.cpp'
    open(p, 'w').write(text)
    subprocess.run(['g++', '-O2', '-std=c++17', '-o', out, p], check=True)
    return out


def safe(t):
    return re.sub(r'[^A-Za-z0-9._-]', '_', t)


def run_v1(binary, prices, coins, tag):
    work = tempfile.mkdtemp(prefix=f'v1{tag}_')
    data, state = os.path.join(work, 'data'), os.path.join(work, 'state')
    os.makedirs(data); os.makedirs(state)
    tick = list(coins) + MACRO
    dates = [d for d in prices.index if d >= START]
    rows, fee_cum = [], 0.0
    for d in dates:
        day = pd.Timestamp(d)
        lo = (day - pd.Timedelta(days=60)).strftime('%Y-%m-%d')
        win = prices.loc[(prices.index >= lo) & (prices.index <= d)]
        for t in tick:
            s = win[t].dropna()
            pd.DataFrame({'Date': s.index, 'Close': s.values}).to_csv(os.path.join(data, safe(t) + '.csv'), index=False, float_format='%.12g')
        now = (day + pd.Timedelta(days=1)).strftime('%Y-%m-%d') + ' 00:00:00'
        r = subprocess.run([binary, '--data-dir', data, '--state-dir', state, '--now', now], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f'v1 failed on {d}: {r.stdout[-300:]} {r.stderr[-300:]}')
        led = pd.read_csv(os.path.join(state, 'portfolio_ledger.csv'))
        tv = led['Total_Value'].sum()
        inv = led.loc[led['Asset_Type'] != 'CASH', 'Total_Value'].sum()
        rows.append((d, tv, 100 * inv / tv if tv else 0.0))
    out = pd.DataFrame(rows, columns=['Date', 'V1_Value', 'V1_Invested_Pct']).set_index('Date')
    tp = os.path.join(state, 'trades.csv')
    tr = pd.read_csv(tp) if os.path.exists(tp) else pd.DataFrame(columns=['Timestamp', 'Fee_USDT'])
    tr['Date'] = tr['Timestamp'].astype(str).str[:10]
    # trades are stamped with the run time (day after the bar); map back to the bar date
    tr['Date'] = (pd.to_datetime(tr['Date']) - pd.Timedelta(days=1)).dt.strftime('%Y-%m-%d')
    fee = tr.groupby('Date')['Fee_USDT'].sum().reindex(out.index).fillna(0)
    cnt = tr.groupby('Date').size().reindex(out.index).fillna(0)
    out['V1_Fees_Cum'] = fee.cumsum()
    out['V1_Notional_Cum'] = out['V1_Fees_Cum'] / 0.001
    out['V1_Trades_Cum'] = cnt.cumsum()
    shutil.rmtree(work, ignore_errors=True)
    return out


def run_v2(binary, prices_path, coins, tag, sets=(), fee=None, out=None):
    out = out or os.path.join(tempfile.mkdtemp(prefix=f'v2{tag}_'), 'o.csv')
    cmd = [binary, '--backtest', '--prices', prices_path, '--start', START, '--out', out, '--capital', str(CAPITAL), '--universe', ','.join(coins)]
    for s in sets:
        cmd += ['--set', s]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stdout[-300:] + r.stderr[-300:])
    return pd.read_csv(out, index_col='Date')


def universe_job(tag):
    prices = pd.read_csv(os.path.join(BT, 'prices.csv'), index_col='Date')
    coins = UNI[tag]
    v1bin = build(os.path.join(ROOT, 'cpp/quantbot.cpp'), os.path.join(tempfile.mkdtemp(), 'v1'), coins if tag == 'B' else None)
    v2bin = build(os.path.join(ROOT, 'cpp/quantbot_v2.cpp'), os.path.join(tempfile.mkdtemp(), 'v2'))
    v1 = run_v1(v1bin, prices, coins, tag)
    v2 = run_v2(v2bin, os.path.join(BT, 'prices.csv'), coins, tag)
    d = v2.index
    px = prices.ffill().reindex(d)
    res = pd.DataFrame(index=d)
    res['V1'] = v1['V1_Value'].reindex(d)
    res['V2'] = v2['V2_Value']
    res['BTC'] = CAPITAL * px['BTC-USD'] / px['BTC-USD'].iloc[0]
    basket = [c for c in coins if px[c].iloc[0] > 0]
    res['Basket'] = sum(CAPITAL / len(basket) * px[c] / px[c].iloc[0] for c in basket)
    res['VOO'] = CAPITAL * px['VOO'] / px['VOO'].iloc[0]
    for c in ['V1_Invested_Pct', 'V1_Fees_Cum', 'V1_Notional_Cum', 'V1_Trades_Cum']:
        res[c] = v1[c].reindex(d)
    res['V2_Invested_Pct'] = v2['Invested_Pct']
    for src, dst in [('Regime', 'V2_Regime'), ('Breaker', 'V2_Breaker'), ('Fees_Cum', 'V2_Fees_Cum'),
                     ('Notional_Cum', 'V2_Notional_Cum'), ('Trades_Cum', 'V2_Trades_Cum')]:
        res[dst] = v2[src]
    res.to_csv(os.path.join(BT, f'results_{tag}.csv'), float_format='%.6f')
    return tag, v2bin


def sens(v2bin, tag='B'):
    base = run_v2(v2bin, os.path.join(BT, 'prices.csv'), UNI[tag], 's')['V2_Value']
    cases = [('base', [])] + [(f'{k}={v}', [f'{k}={v}']) for k, v in [
        ('ema_slow', 40), ('ema_slow', 70), ('mom_days', 45), ('mom_days', 90), ('top_k', 3), ('top_k', 6),
        ('cap', 0.25), ('cap', 0.5), ('target_vol', 0.4), ('target_vol', 0.8), ('dd_limit', 0.08), ('dd_limit', 0.2),
        ('confirm_bars', 1), ('confirm_bars', 3), ('drift', 0.1), ('fee', 0.003)]]
    rows = []
    for name, sets in cases:
        r = run_v2(v2bin, os.path.join(BT, 'prices.csv'), UNI[tag], 's', sets)
        v = r['V2_Value']; dd = (v / v.cummax() - 1).min()
        yrs = len(v) / 365.25
        rows.append((name, v.iloc[-1], (v.iloc[-1] / CAPITAL) ** (1 / yrs) - 1, dd, r['Fees_Cum'].iloc[-1], r['Trades_Cum'].iloc[-1]))
    pd.DataFrame(rows, columns=['Case', 'Final', 'CAGR', 'MaxDD', 'Fees', 'Trades']).to_csv(os.path.join(BT, 'sensitivity.csv'), index=False, float_format='%.4f')


if __name__ == '__main__':
    with ProcessPoolExecutor(2) as ex:
        out = dict(ex.map(universe_job, ['A', 'B']))
    if '--no-sens' not in sys.argv:
        sens(out['B'])
    print('done')
