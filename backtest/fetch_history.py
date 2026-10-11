"""Download daily closes for the backtest universe into ONE wide CSV (default: backtest/prices.csv).

Yahoo is not reachable from every environment, so this is meant to run on GitHub Actions (see
.github/workflows/backtest.yml). One row per calendar date; a blank cell means that ticker had no price that day
(not listed yet, or a market holiday for stocks and ETFs). Today's unfinished daily candle is dropped, so every row
is a CLOSED bar.

Usage: python backtest/fetch_history.py [output.csv] [start-date]
"""
import sys
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

COINS = ['BTC-USD', 'ETH-USD', 'SOL-USD', 'LINK-USD', 'AVAX-USD', 'NEAR-USD', 'ADA-USD', 'DOT-USD']
OTHERS = ['^GSPC', 'GC=F', 'DX-Y.NYB', 'CL=F', 'VOO']  # macro tickers are only used by v1; VOO is a benchmark
OUT = sys.argv[1] if len(sys.argv) > 1 else 'backtest/prices.csv'
START = sys.argv[2] if len(sys.argv) > 2 else '2020-06-01'

today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
cols, problems = {}, []
for ticker in COINS + OTHERS:
    try:
        df = yf.download(ticker, start=START, interval='1d', auto_adjust=True, progress=False)
        if df.empty:
            raise RuntimeError('empty download')
        close = df['Close']
        if hasattr(close, 'columns'):  # newer yfinance returns a one-column frame
            close = close.iloc[:, 0]
        close = close.dropna()
        close = close[close > 0]
        close.index = pd.to_datetime(close.index).strftime('%Y-%m-%d')
        close = close[close.index < today]  # closed bars only
        cols[ticker] = close
        print(f'ok   {ticker:10s} {close.index[0]} -> {close.index[-1]}  rows={len(close)}')
    except Exception as e:  # keep going so the log shows every problem at once
        problems.append(ticker)
        print(f'FAIL {ticker}: {e}')

wide = pd.DataFrame(cols).sort_index()
wide.index.name = 'Date'

for ticker in COINS:
    if ticker not in wide or wide[ticker].count() < 1200:
        problems.append(f'{ticker} has too little history')
if problems:
    print('PROBLEMS:', problems)
    sys.exit(1)
last = wide.index[-1]
if (pd.Timestamp(today) - pd.Timestamp(last)).days > 4:
    print(f'PROBLEM: newest row is {last}, too old')
    sys.exit(1)

wide.to_csv(OUT, float_format='%.12g')
print(f'wrote {OUT}: {wide.shape[0]} rows x {wide.shape[1]} tickers, {wide.index[0]} -> {last}')
