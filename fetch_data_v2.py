"""Download ~1 year of daily closes for the v2 engine into data_v2/<ticker>.csv (Date,Close).

v2 needs 200 bars of history, so this pulls 400 calendar days. Same rule as fetch_data.py: a ticker that fails
has its old file DELETED so stale data is never reused. BTC-USD is required by v2 (it drives the regime); VOO is
only for the live comparison chart.
"""
import os
import re
import sys

import yfinance as yf

TICKERS = ['BTC-USD', 'ETH-USD', 'SOL-USD', 'LINK-USD', 'AVAX-USD', 'NEAR-USD', 'ADA-USD', 'DOT-USD', 'VOO']
DATA_DIR = sys.argv[1] if len(sys.argv) > 1 else 'data_v2'


def safe_name(t):  # must match safe_name() in the C++ engines
    return re.sub(r'[^A-Za-z0-9._-]', '_', t)


os.makedirs(DATA_DIR, exist_ok=True)
failed = []
for t in TICKERS:
    path = os.path.join(DATA_DIR, safe_name(t) + '.csv')
    if os.path.exists(path):
        os.remove(path)
    try:
        df = yf.download(t, period='400d', interval='1d', progress=False, auto_adjust=True)
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
            for d, v in close.items():
                f.write(f'{d.strftime("%Y-%m-%d")},{float(v)!r}\n')
        print(f'ok   {t}: {len(close)} rows')
    except Exception as e:
        failed.append(t)
        print(f'FAIL {t}: {e}')
print(f'{len(TICKERS) - len(failed)}/{len(TICKERS)} tickers written')
