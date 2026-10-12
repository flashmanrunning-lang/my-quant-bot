"""Tests for the v2 engine (cpp/quantbot_v2.cpp).

Usage: python tests/v2_test.py path/to/quantbot_v2 [--quick]

  1. parity     : C++ backtest == independent Python reference (tests/v2_reference.py) on random markets with
                  staggered listings, missing days and bad prints, with default AND changed parameters
  2. look-ahead : changing or deleting FUTURE prices never changes any past result
  3. live mode  : stepping the live program one day at a time (state file, 260-day data windows, an unfinished
                  "today" candle) gives the same equity as the backtest, and a second run on the same bar changes nothing
  4. properties : weight cap, no leverage, idle market makes no trades, breaker trips on a crash and releases only after
                  the cooldown, hysteresis reduces regime flips, fee accounting adds up
  5. sensitivity: the parity test FAILS when the C++ is run with a changed parameter (so every parameter is wired in and
                  the test can tell), plus structural mutations of the source
"""
import os
import re
import subprocess
import sys
import tempfile

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from v2_reference import run_reference, LIVE  # noqa: E402

BINARY = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith('--') else None
QUICK = '--quick' in sys.argv
TMP = tempfile.mkdtemp()
ALL = LIVE + ['XRP-USD', 'BNB-USD', 'LTC-USD']
results = []


def check(name, ok, detail=''):
    results.append(bool(ok))
    print(('PASS ' if ok else 'FAIL ') + name + ('' if ok else f'   <- {detail}'))


# ------------------------------------------------------------------ synthetic markets
def make_market(seed, n=620, crash=False):
    """Wide price table. A regime-switching market factor, coins with different betas and volatility, late listings,
    random missing days and a few bad (zero) prints."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range('2020-01-01', periods=n, freq='D').strftime('%Y-%m-%d')
    f = np.zeros(n)
    i = 0
    while i < n:
        seg = int(rng.integers(35, 120))
        drift = rng.choice([0.009, 0.004, 0.0, -0.004, -0.009])
        f[i:i + seg] = rng.normal(drift, 0.028, min(seg, n - i))
        i += seg
    if crash:
        f[330:336] = -0.065
    out = {}
    for k, t in enumerate(ALL):
        beta = 1.0 if t == 'BTC-USD' else rng.uniform(0.7, 1.9)
        idio = rng.uniform(0.012, 0.045)
        r = beta * f + rng.normal(0, idio, n)
        price = rng.uniform(5, 500) * np.exp(np.cumsum(r))
        s = pd.Series(price, index=dates)
        if t not in ('BTC-USD', 'ETH-USD'):
            s.iloc[:int(rng.choice([0, 0, 30, 90, 200]))] = np.nan          # late listing
        miss = rng.random(n) < 0.012
        miss[:5] = False
        if t != 'BTC-USD':
            s[miss] = np.nan                                                # missing day
        if t == 'ADA-USD':
            s.iloc[int(rng.integers(150, 300))] = 0.0                       # bad print
        out[t] = s
    wide = pd.DataFrame(out)
    wide.index.name = 'Date'
    return wide


def write_wide(df, path):
    """Write the wide price file the engine reads and return the table AS THE ENGINE WILL SEE IT (12 significant
    digits), so the Python reference works on exactly the same numbers."""
    df.to_csv(path, float_format='%.12g')
    return pd.read_csv(path, index_col='Date')


def cpp_backtest(prices_path, start, out, *extra, binary=None, trades=None):
    cmd = [binary or BINARY, '--backtest', '--prices', prices_path, '--start', start, '--out', out]
    if trades:
        cmd += ['--trades-out', trades]
    cmd += list(extra)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f'engine failed: {r.stderr or r.stdout}')
    return pd.read_csv(out)


def same(a, b, tol=1e-8):
    """Compare two result tables. Returns '' when equal, else a short description of the first difference."""
    if len(a) != len(b):
        return f'row count {len(a)} vs {len(b)}'
    if list(a.Date) != list(b.Date):
        return 'dates differ'
    for col in ['Regime', 'Breaker', 'Trades_Cum', 'N_Positions']:
        bad = np.flatnonzero(a[col].to_numpy() != b[col].to_numpy())
        if len(bad):
            return f'{col} differs first on {a.Date.iloc[bad[0]]}: {a[col].iloc[bad[0]]} vs {b[col].iloc[bad[0]]}'
    for col in ['V2_Value', 'Invested_Pct', 'Fees_Cum', 'Notional_Cum', 'Max_Weight']:
        x, y = a[col].to_numpy(float), b[col].to_numpy(float)
        bad = np.flatnonzero(np.abs(x - y) > tol * np.maximum(1.0, np.abs(y)))
        if len(bad):
            return f'{col} differs first on {a.Date.iloc[bad[0]]}: {x[bad[0]]!r} vs {y[bad[0]]!r}'
    return ''


# ------------------------------------------------------------------ build / sanity
if BINARY is None or not os.path.exists(BINARY):
    sys.exit('usage: python tests/v2_test.py path/to/quantbot_v2 [--quick]   (build it first)')

SEEDS = [1, 2, 3] if QUICK else [1, 2, 3, 4, 5, 6]
START = '2020-05-15'
markets, refs, cpp = {}, {}, {}
coverage = dict(off=0, half=0, on=0, breaker=0, trades=0, capped=0, scaled=0, flips=0)
for sd in SEEDS:
    path = os.path.join(TMP, f'm{sd}.csv')
    m = write_wide(make_market(sd, crash=(sd % 3 == 0)), path)
    markets[sd] = m
    refs[sd] = run_reference(m, START)
    cpp[sd] = cpp_backtest(path, START, os.path.join(TMP, f'o{sd}.csv'))

# ------------------------------------------------------------------ 1. parity
for sd in SEEDS:
    diff = same(cpp[sd], refs[sd])
    check(f'parity seed {sd}: C++ equals the Python reference on {len(refs[sd])} days', not diff, diff)
    r = refs[sd]
    coverage['off'] += int((r.Regime == 0).sum())
    coverage['half'] += int((r.Regime == 1).sum())
    coverage['on'] += int((r.Regime == 2).sum())
    coverage['breaker'] += int(r.Breaker.sum())
    coverage['trades'] += int(r.Trades_Cum.iloc[-1])
    coverage['capped'] += int(((r.Max_Weight > 0.30) & (r.N_Positions >= 1)).sum())
    coverage['flips'] += int((r.Regime.diff().fillna(0) != 0).sum())
print('   coverage across seeds:', coverage)
check('the random markets exercise every branch (OFF, HALF, ON, breaker, trades, big positions)',
      all(coverage[k] > 0 for k in ['off', 'half', 'on', 'breaker', 'trades', 'capped', 'flips']), str(coverage))

variants = [dict(ema_slow=40), dict(band=0.03, confirm_bars=3), dict(top_k=3, cap=0.5), dict(target_vol=0.35, vol_days=20),
            dict(dd_limit=0.08, cooldown=9), dict(drift=0.02, fee=0.003), dict(window=120, mom_days=45)]
for k, v in enumerate(variants):
    sd = SEEDS[k % len(SEEDS)]
    args = [x for kv in v.items() for x in ('--set', f'{kv[0]}={kv[1]}')]
    got = cpp_backtest(os.path.join(TMP, f'm{sd}.csv'), START, os.path.join(TMP, 'v.csv'), *args)
    want = run_reference(markets[sd], START, **v)
    diff = same(got, want)
    check(f'parity with changed parameters {v}', not diff, diff)

uni = ['BTC-USD', 'ETH-USD', 'XRP-USD', 'BNB-USD', 'LTC-USD', 'ADA-USD']
sd = SEEDS[0]
got = cpp_backtest(os.path.join(TMP, f'm{sd}.csv'), START, os.path.join(TMP, 'u.csv'), '--universe', ','.join(uni))
check('parity with a different coin universe', not same(got, run_reference(markets[sd], START, universe=uni)))

# ------------------------------------------------------------------ 2. no look-ahead
sd = SEEDS[1]
m = markets[sd]
cut = m.index[420]
trunc = m.loc[:cut]
write_wide(trunc, os.path.join(TMP, 'trunc.csv'))
a = cpp_backtest(os.path.join(TMP, 'trunc.csv'), START, os.path.join(TMP, 'trunc_o.csv'))
b = cpp[sd][cpp[sd].Date <= cut].reset_index(drop=True)
diff = same(a, b, tol=0.0)
check('look-ahead: removing all data after day 420 changes nothing before it (exact)', not diff, diff)
garbled = m.copy()
garbled.loc[garbled.index > cut] = garbled.loc[garbled.index > cut] * np.random.default_rng(9).uniform(0.2, 5, (len(garbled.loc[garbled.index > cut]), garbled.shape[1]))
write_wide(garbled, os.path.join(TMP, 'garbled.csv'))
a = cpp_backtest(os.path.join(TMP, 'garbled.csv'), START, os.path.join(TMP, 'garbled_o.csv'))
diff = same(a[a.Date <= cut].reset_index(drop=True), b, tol=0.0)
check('look-ahead: scrambling all prices after day 420 changes nothing before it (exact)', not diff, diff)

# ------------------------------------------------------------------ 3. live mode == backtest
sd = SEEDS[2]
m = markets[sd]
base = cpp[sd]
dates = list(m.index)
ffilled = m.where(m > 0).ffill()


def write_live_window(day_idx, data_dir, partial_factor):
    """What fetch_data_v2.py leaves in the folder at the first run after bar `day_idx` closed: 260 days of closed
    bars plus an unfinished candle for the new day."""
    os.makedirs(data_dir, exist_ok=True)
    lo = max(0, day_idx - 259)
    for t in LIVE:
        s = m[t].iloc[lo:day_idx + 1]
        s = s.where(s > 0).dropna()
        if s.empty:
            continue
        rows = [(d, v) for d, v in s.items()]
        last = ffilled[t].iloc[day_idx]
        if not np.isnan(last):
            rows.append((dates[day_idx + 1], last * partial_factor))
        with open(os.path.join(data_dir, t + '.csv'), 'w') as f:
            f.write('Date,Close\n')
            for d, v in rows:
                f.write(f'{d},{v:.12g}\n')


def live_steps(first_idx, last_idx, partial_factor, label):
    state = os.path.join(TMP, 'state_' + label)
    data = os.path.join(TMP, 'data_' + label)
    out = []
    for k in range(first_idx, last_idx + 1):
        write_live_window(k, data, partial_factor)
        now = f'{dates[k + 1]} 00:30:00'
        cmd = [BINARY, '--data-dir', data, '--state-dir', state, '--now', now, '--capital', '100000']
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(r.stderr)
        out.append((k, r.stdout))
    return state, out


# the backtest starts at START; start the live stepping on the SAME bar so both begin with identical state
start_idx = dates.index(START)
end_idx = start_idx + (60 if QUICK else 160)
state_dir, outputs = live_steps(start_idx, end_idx, 1.0, 'same')
live = pd.read_csv(os.path.join(state_dir, 'equity.csv')).rename(columns={'Timestamp': 'Date'})
live['Date'] = [dates[k] for k, _ in outputs]            # one row per day (each bar processed once)
want = base[(base.Date >= START) & (base.Date <= dates[end_idx])].reset_index(drop=True)
diff = same(live, want, tol=1e-9)
check(f'live mode: {len(live)} daily steps with a saved state equal the backtest', not diff, diff)

# a second run on the same bar must not trade or change the books
snap = open(os.path.join(state_dir, 'state.txt')).read()
trades_before = os.path.exists(os.path.join(state_dir, 'trades.csv')) and len(open(os.path.join(state_dir, 'trades.csv')).readlines())
write_live_window(end_idx, os.path.join(TMP, 'data_same'), 1.0)
r = subprocess.run([BINARY, '--data-dir', os.path.join(TMP, 'data_same'), '--state-dir', state_dir, '--now',
                    f'{dates[end_idx + 1]} 09:30:00'], capture_output=True, text=True)
eq = pd.read_csv(os.path.join(state_dir, 'equity.csv'))
trades_after = os.path.exists(os.path.join(state_dir, 'trades.csv')) and len(open(os.path.join(state_dir, 'trades.csv')).readlines())
check('live mode: running again on the same closed bar only marks to market',
      'already processed' in r.stdout and open(os.path.join(state_dir, 'state.txt')).read() == snap
      and trades_before == trades_after and abs(eq.V2_Value.iloc[-1] - eq.V2_Value.iloc[-2]) < 1e-9, r.stdout)

# an unfinished "today" candle must not move the signals, even when it is far from the last close
state_dir2, outputs2 = live_steps(start_idx, min(end_idx, start_idx + 120), 1.07, 'partial')
live2 = pd.read_csv(os.path.join(state_dir2, 'equity.csv'))
want2 = base[(base.Date >= START)].reset_index(drop=True).iloc[:len(live2)]
check('live mode: a wildly different unfinished candle does not change the regime',
      list(live2.Regime) == list(want2.Regime))

# ------------------------------------------------------------------ 4. properties
all_days = pd.concat([cpp[s] for s in SEEDS])
check('never more than 100% invested (no leverage)', (all_days.Invested_Pct <= 100 + 1e-9).all())
tr = all_days[all_days.Trades_Cum.diff().fillna(1) != 0]
check('right after a rebalance no coin is above the 35% cap', (tr.Max_Weight <= 0.35 + 1e-9).all(),
      f'max {tr.Max_Weight.max()}')
check('never more than 4 coins held', (all_days.N_Positions <= 4).all())

flat = pd.DataFrame({t: 100.0 + 0.0 * np.arange(300) for t in LIVE}, index=pd.date_range('2020-01-01', periods=300).strftime('%Y-%m-%d'))
flat.index.name = 'Date'
flat = write_wide(flat, os.path.join(TMP, 'flat.csv'))
fl = cpp_backtest(os.path.join(TMP, 'flat.csv'), '2020-06-01', os.path.join(TMP, 'flat_o.csv'))
check('a flat market makes no trades and keeps the capital', fl.Trades_Cum.iloc[-1] == 0 and abs(fl.V2_Value.iloc[-1] - 100000) < 1e-9)

# crash: strong rise for 330 days, then six days of -6.5%
n = 420
idx = pd.date_range('2020-01-01', periods=n).strftime('%Y-%m-%d')
rng = np.random.default_rng(5)
r_ = rng.normal(0.006, 0.01, n)
r_[330:336] = -0.065
crash = pd.DataFrame({t: 100 * np.exp(np.cumsum(r_ + rng.normal(0, 0.004, n))) for t in LIVE}, index=idx)
crash.index.name = 'Date'
crash = write_wide(crash, os.path.join(TMP, 'crash.csv'))
cr = cpp_backtest(os.path.join(TMP, 'crash.csv'), '2020-05-15', os.path.join(TMP, 'crash_o.csv'))
trip = cr[(cr.Breaker == 1)]
ok = len(trip) > 0
if ok:
    first_trip = trip.index[0]
    ok = (cr.Date.iloc[first_trip] <= idx[345]) and cr.Invested_Pct.iloc[first_trip] == 0
    # stays in cash for at least cooldown bars
    ok = ok and (cr.Invested_Pct.iloc[first_trip:first_trip + 5] == 0).all()
check('crash: the circuit breaker trips within days and the portfolio is in cash from that bar on', ok,
      str(cr.iloc[325:345][['Date', 'V2_Value', 'Invested_Pct', 'Regime', 'Breaker']].to_string()))
ref_cr = run_reference(crash, '2020-05-15')
check('crash: the Python reference agrees', not same(cr, ref_cr))

# hysteresis: sideways market, count regime flips with and without the band + confirmation
flips_on = flips_off = 0
for sd in range(20, 26):
    rg = np.random.default_rng(sd)
    n = 500
    walk = 100 * np.exp(np.cumsum(rg.normal(0.0004, 0.022, n)))
    side = pd.DataFrame({t: walk * np.exp(rg.normal(0, 0.01, n)) for t in LIVE},
                        index=pd.date_range('2020-01-01', periods=n).strftime('%Y-%m-%d'))
    side.index.name = 'Date'
    p = os.path.join(TMP, f'side{sd}.csv')
    write_wide(side, p)
    a = cpp_backtest(p, '2020-05-15', os.path.join(TMP, 'sa.csv'))
    b = cpp_backtest(p, '2020-05-15', os.path.join(TMP, 'sb.csv'), '--set', 'band=0', '--set', 'confirm_bars=1')
    flips_on += int((a.Regime.diff().fillna(0) != 0).sum())
    flips_off += int((b.Regime.diff().fillna(0) != 0).sum())
check(f'hysteresis: band + 2-bar confirmation gives fewer regime flips ({flips_on}) than none ({flips_off})',
      flips_on < flips_off)

sd = SEEDS[0]
tfile = os.path.join(TMP, 'trades_fee.csv')
bt = cpp_backtest(os.path.join(TMP, f'm{sd}.csv'), START, os.path.join(TMP, 'fee_o.csv'), trades=tfile)
tdf = pd.read_csv(tfile)
check('fees in the trade log add up to the fees in the results', abs(tdf.Fee_USDT.sum() - bt.Fees_Cum.iloc[-1]) < 1e-6 * max(1, bt.Fees_Cum.iloc[-1]))
check('trade log has as many rows as the trade counter', len(tdf) == bt.Trades_Cum.iloc[-1])

# ------------------------------------------------------------------ 5. can the parity test tell? (parameter wiring + mutations)
sd = SEEDS[0]
mp = os.path.join(TMP, f'm{sd}.csv')
changes = [('cap', '0.45'), ('confirm_bars', '1'), ('band', '0.03'), ('dd_limit', '0.2'), ('top_k', '3'), ('target_vol', '0.9'),
           ('ema_slow', '60'), ('ema_fast', '15'), ('drift', '0.1'), ('fee', '0.002'), ('vol_days', '20'), ('cooldown', '3'),
           ('mom_days', '50'), ('window', '80'), ('min_bars', '160')]
caught = []
for k, v in changes:
    hit = False
    for s2 in SEEDS:
        got = cpp_backtest(os.path.join(TMP, f'm{s2}.csv'), START, os.path.join(TMP, 'mut.csv'), '--set', f'{k}={v}')
        if same(got, refs[s2]):
            hit = True
            break
    caught.append((k, hit))
miss = [k for k, c in caught if not c]
check(f'changing any one of {len(changes)} parameters makes the parity test fail', not miss, f'not detected: {miss}')

src = open(os.path.join(ROOT, 'cpp', 'quantbot_v2.cpp')).read()
mutations = {
    'coin trend filter removed': ('&& c > ema_window(v, ci, f, P.ema_slow)', ''),
    'breaker never releases': ('if (s.cooldown_left == 0 && s.regime != 0)', 'if (false)'),
    'HALF exposure is 70%': ('(s.regime == 2 ? 1.0 : 0.5)', '(s.regime == 2 ? 1.0 : 0.7)'),
    'no volatility scaling': ('d.vol_scale = pvol > 0 ? std::min(1.0, P.target_vol / pvol) : 1.0;', 'd.vol_scale = 1.0;'),
    'weights not inverse-vol': ('w[j] = 1.0 / std::max(stdev(rets[j]), 1e-6);', 'w[j] = 1.0;'),
    'momentum measured over 59 days': ('const double mom = c / v[static_cast<std::size_t>(ci - P.mom_days)] - 1.0;',
                                       'const double mom = c / v[static_cast<std::size_t>(ci - P.mom_days + 1)] - 1.0;'),
    'ranking picks the weakest coins': ('return a.mom > b.mom;', 'return a.mom < b.mom;'),
    'fee understated by half': ('const double fee = P.fee * notional;', 'const double fee = P.fee * notional * 0.5;'),
}
if QUICK:
    mutations = dict(list(mutations.items())[:3])
for name, (old, new) in mutations.items():
    if old not in src:
        check(f'mutation "{name}"', False, 'pattern not found in the source (test needs updating)')
        continue
    mpath = os.path.join(TMP, 'mut.cpp')
    open(mpath, 'w').write(src.replace(old, new, 1))
    mbin = os.path.join(TMP, 'mut_bin')
    c = subprocess.run(['g++', '-std=c++17', '-O2', '-o', mbin, mpath], capture_output=True, text=True)
    if c.returncode != 0:
        check(f'mutation "{name}"', False, c.stderr[:200])
        continue
    hit = any(same(cpp_backtest(os.path.join(TMP, f'm{s2}.csv'), START, os.path.join(TMP, 'mut.csv'), binary=mbin), refs[s2])
              for s2 in SEEDS)
    check(f'mutation "{name}" is caught by the parity test', hit)

print(f'\n{sum(results)}/{len(results)} checks passed')
sys.exit(0 if all(results) else 1)
