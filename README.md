# my-quant-bot

Paper-trading crypto bot. Every 3 hours (GitHub Actions) it picks the top-3 positive 45-day momentum coins
from a fixed universe, holds them equal-weight when a macro regime filter is risk-on, and otherwise sits in cash.

## How a run works
1. `fetch_data.py` downloads daily closes (yfinance) into `data/*.csv`. A ticker that fails has its old file deleted.
2. `cpp/quantbot.cpp` (the strategy engine, C++17, no networking) reads those files, trades, and updates the state files.
3. `plot_chart.py` redraws `performance_chart.png` from `alpha_performance.csv`.

## Files
- `cpp/quantbot.cpp` - strategy, simulated execution (with fees), benchmark, ledgers
- `portfolio_ledger.csv` - current simulated positions
- `trades.csv` - append-only log of every simulated trade
- `benchmark_baseline.json` - start prices for the buy & hold benchmark (delete it to restart the benchmark)
- `alpha_performance.csv` / `performance_chart.png` - equity curve vs benchmark
- `main.py` - the original Python implementation, kept as the reference for the parity test
- `tests/parity_test.py` - runs `main.py` and the C++ engine side by side on random markets and compares every decision and every file

## Run locally
```
pip install -r requirements.txt
python fetch_data.py data
g++ -std=c++17 -O2 -Wall -Wextra -o quantbot cpp/quantbot.cpp
./quantbot --data-dir data          # add --now "YYYY-MM-DD HH:MM:SS" to pin the timestamp
python plot_chart.py
python tests/parity_test.py ./quantbot 100 1     # binary, cycles, seed
```

Config (fee rate, rebalance drift, macro vote minimum) is at the top of `cpp/quantbot.cpp`.
