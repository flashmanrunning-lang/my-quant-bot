# my-quant-bot

Paper-trading crypto bot. Every 3 hours (GitHub Actions) it picks the top-3 positive 45-day momentum coins
from a fixed universe, holds them equal-weight when a macro regime filter is risk-on, and otherwise sits in cash.

- `main.py` - strategy, simulated execution (with fees), benchmark and chart
- `portfolio_ledger.csv` - current simulated positions
- `trades.csv` - append-only log of every simulated trade
- `benchmark_baseline.json` - start prices for the buy & hold benchmark (delete it to restart the benchmark)
- `alpha_performance.csv` / `performance_chart.png` - equity curve vs benchmark

Config (fee rate, rebalance drift threshold) is at the top of `main.py`.
