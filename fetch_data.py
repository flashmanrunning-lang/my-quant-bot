"""Download daily closes for every ticker the C++ engine needs and write them to data/<ticker>.csv.

The C++ program does no networking: it only reads these files. A ticker that fails to download has its
old file DELETED, so stale data is never reused (the engine then treats it as missing and skips the cycle
or the vote, exactly like the Python version did).
"""
import os
import re
import sys

import yfinance as yf

COINS = ['ETH-USD', 'SOL-USD', 'LINK-USD', 'AVAX-USD', 'NEAR-USD', 'ADA-USD', 'DOT-USD']
OTHERS = ['BTC-USD', '^GSPC', 'GC=F', 'DX-Y.NYB', 'CL=F', 'VOO']  # VOO is only used for the chart comparison
DATA_DIR = sys.argv[1] if len(sys.argv) > 1 else 'data'


def safe_name(ticker):  # must match safe_name() in cpp/quantbot.cpp
    return re.sub(r'[^A-Za-z0-9._-]', '_', ticker)


os.makedirs(DATA_DIR, exist_ok=True)
failed = []
for ticker in COINS + OTHERS:
    path = os.path.join(DATA_DIR, safe_name(ticker) + '.csv')
    if os.path.exists(path):
        os.remove(path)
    try:
        df = yf.download(ticker, period='60d', interval='1d', progress=False, auto_adjust=True)
        if df.empty:
            raise RuntimeError('empty download')
        close = df['Close']
        if hasattr(close, 'columns'):
            close = close.iloc[:, 0]
        close = close.dropna()
        if close.empty:
            raise RuntimeError('no closes')
        with open(path, 'w') as f:
            f.write('Date,Close\n')
            for date, value in close.items():
                f.write(f'{date.strftime("%Y-%m-%d")},{float(value)!r}\n')
        print(f'ok   {ticker}: {len(close)} rows')
    except Exception as e:
        failed.append(ticker)
        print(f'FAIL {ticker}: {e}')

print(f'{len(COINS + OTHERS) - len(failed)}/{len(COINS + OTHERS)} tickers written')
