"""Independent reference of the v2 rules.

Written from the strategy description with pandas / numpy, NOT translated line by line from cpp/quantbot_v2.cpp, so that
tests/v2_test.py can check the C++ engine against a second implementation. (EMAs come from pandas' ewm, standard
deviations from pandas' std, capped weights from a bisection instead of the C++ redistribution loop.)

The rules, in words:
  Regime  : BTC trend score = (close above EMA50 by +1.5%, latched until it falls 1.5% below)
                            + (EMA20 above EMA50) + (60-day return above 0).
            score 3 -> ON (100%), 2 -> HALF (50%), 0-1 -> OFF (0%). A new state must hold for 2 closed daily bars.
  Picks   : coins with 60-day return > 0 and close above their own EMA50, top 4 by 60-day return.
  Weights : inverse of 30-day volatility, no coin above 35%, times the regime exposure, times
            min(1, 60% / annualised volatility of the picks).
  Breaker : equity 12% under its peak -> cash for at least 5 bars; resumes once the regime is not OFF (peak resets).
  Trading : only if the set of coins changed or a weight is more than 5 points off target; 0.10% fee on traded value.
  All indicators use only the last 200 closed bars. A coin needs 100 bars of history to be traded.
"""
import numpy as np
import pandas as pd

LIVE = ['BTC-USD', 'ETH-USD', 'SOL-USD', 'LINK-USD', 'AVAX-USD', 'NEAR-USD', 'ADA-USD', 'DOT-USD']
DEFAULTS = dict(window=200, min_bars=100, ema_fast=20, ema_slow=50, band=0.015, mom_days=60, confirm_bars=2,
                top_k=4, vol_days=30, cap=0.35, target_vol=0.60, dd_limit=0.12, cooldown=5, drift=0.05, fee=0.001)
COLUMNS = ['Date', 'V2_Value', 'Invested_Pct', 'Regime', 'Breaker', 'Fees_Cum', 'Notional_Cum', 'Trades_Cum',
           'Max_Weight', 'N_Positions']


def capped_weights(u, cap):
    """w_i = min(cap, lam * u_i) with the sum equal to 1 (found by bisection). If every coin hits the cap the
    weights sum to less than 1 and the rest stays in cash."""
    u = np.asarray(u, dtype=float)
    u = u / u.sum()
    n = len(u)
    if cap * n <= 1.0:
        return np.full(n, cap)
    lo, hi = 0.0, 1.0 / u.min() + 1.0
    for _ in range(200):
        lam = 0.5 * (lo + hi)
        if np.minimum(cap, lam * u).sum() < 1.0:
            lo = lam
        else:
            hi = lam
    return np.minimum(cap, 0.5 * (lo + hi) * u)


def run_reference(prices, start, universe=None, capital=100000.0, **override):
    p = {**DEFAULTS, **override}
    uni = [c for c in (universe or LIVE) if c in prices.columns]
    px = prices[uni].copy()
    px = px.where(px > 0)                      # zero / negative prints are bad data
    px = px.dropna(axis=1, how='all').dropna(how='all')  # calendar = dates where some coin has a price
    firsts = {c: px[c].first_valid_index() for c in px.columns}
    px = px.ffill()                            # NaN stays before a coin's first price, forward fill after
    dates = list(px.index)
    pos = {d: i for i, d in enumerate(dates)}
    first = {c: pos[firsts[c]] for c in px.columns if firsts[c] is not None}
    arr = {c: px[c].to_numpy(dtype=float) for c in px.columns}

    def ema(c, i, span):
        lo = max(first[c], i - p['window'] + 1)
        return pd.Series(arr[c][lo:i + 1]).ewm(span=span, adjust=False).mean().iloc[-1]

    cash, qty = float(capital), {}
    regime, pending, pending_n, c1 = 0, -1, 0, -1
    peak, breaker, cooldown_left = 0.0, False, 0
    fees = notional = 0.0
    ntrades = 0
    started = False
    rows = []

    for i, d in enumerate(dates):
        if d < start:
            continue
        price = {c: arr[c][i] for c in arr}
        equity = cash + sum(q * price[c] for c, q in qty.items())

        # --- regime
        raw = 0
        if 'BTC-USD' in arr:
            bf = first['BTC-USD']
            if i - bf + 1 >= p['min_bars'] and i - p['mom_days'] >= bf:
                c = arr['BTC-USD'][i]
                es, ef = ema('BTC-USD', i, p['ema_slow']), ema('BTC-USD', i, p['ema_fast'])
                if c > es * (1 + p['band']):
                    c1 = 1
                elif c < es * (1 - p['band']):
                    c1 = 0
                elif c1 < 0:
                    c1 = 1 if c > es else 0
                score = c1 + int(ef > es) + int(c / arr['BTC-USD'][i - p['mom_days']] - 1 > 0)
                raw = 2 if score == 3 else (1 if score == 2 else 0)
        if not started:
            regime, pending, pending_n = raw, -1, 0
        elif raw == regime:
            pending, pending_n = -1, 0
        else:
            if raw == pending:
                pending_n += 1
            else:
                pending, pending_n = raw, 1
            if pending_n >= p['confirm_bars']:
                regime, pending, pending_n = pending, -1, 0
        started = True

        # --- circuit breaker
        if not breaker:
            peak = max(peak, equity)
            if equity <= peak * (1 - p['dd_limit']):
                breaker, cooldown_left = True, p['cooldown']
        else:
            if cooldown_left > 0:
                cooldown_left -= 1
            if cooldown_left == 0 and regime != 0:
                breaker, peak = False, equity

        # --- targets
        tw = {}
        if not breaker and regime > 0:
            cands = []
            for c in arr:
                f = first[c]
                if i - f + 1 < p['min_bars'] or i - max(p['mom_days'], p['vol_days']) < f:
                    continue
                mom = arr[c][i] / arr[c][i - p['mom_days']] - 1
                if mom > 0 and arr[c][i] > ema(c, i, p['ema_slow']):
                    cands.append((c, mom))
            cands = sorted(cands, key=lambda x: -x[1])[:p['top_k']]      # stable: ties keep universe order
            if cands:
                rets = pd.DataFrame({c: pd.Series(arr[c][i - p['vol_days']:i + 1]).pct_change().dropna().to_numpy()
                                     for c, _ in cands})
                vols = rets.std()                                         # sample std, like the C++ engine
                w = capped_weights(1.0 / np.maximum(vols.to_numpy(), 1e-6), p['cap'])
                port_vol = (rets.to_numpy() @ w).std(ddof=1) * np.sqrt(365.0)
                scale = min(1.0, p['target_vol'] / port_vol) if port_vol > 0 else 1.0
                exposure = (1.0 if regime == 2 else 0.5) * scale
                tw = {c: w[j] * exposure for j, (c, _) in enumerate(cands)}

        # --- trade?
        hv = {c: q * price[c] for c, q in qty.items()}
        held = {c for c, q in qty.items() if q > 1e-12}
        want = {c for c, x in tw.items() if x > 1e-12}
        everything = set(qty) | want
        trade = held != want
        if not trade and equity > 0:
            trade = max([abs(hv.get(c, 0.0) / equity - tw.get(c, 0.0)) for c in everything] + [0.0]) > p['drift']
        if trade:
            deltas = {c: abs(tw.get(c, 0.0) * equity - hv.get(c, 0.0)) for c in everything}
            fee = p['fee'] * sum(deltas.values())
            net = equity - fee
            new_qty = {c: tw[c] * net / price[c] for c in want}
            for c in everything:
                if abs(new_qty.get(c, 0.0) - qty.get(c, 0.0)) >= 1e-12:
                    ntrades += 1
            cash = net - sum(tw[c] * net for c in want)
            qty = new_qty
            fees += fee
            notional += sum(deltas.values())

        invested = sum(q * price[c] for c, q in qty.items() if q > 1e-12)
        eq_after = cash + invested
        vals = [q * price[c] for c, q in qty.items() if q > 1e-12]
        rows.append([d, eq_after, 100.0 * invested / eq_after if eq_after > 0 else 0.0, regime, int(breaker), fees,
                     notional, ntrades, max(vals) / eq_after if vals else 0.0, len(vals)])
    return pd.DataFrame(rows, columns=COLUMNS)
