import os
import json
from datetime import datetime, timezone
import yfinance as yf
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg') # Forces matplotlib to run headless without needing a GUI/display screen
import matplotlib.pyplot as plt

# ==========================================
# ⚙️ CONFIGURATION
# ==========================================
LEDGER_FILE = "portfolio_ledger.csv"
PERFORMANCE_FILE = "alpha_performance.csv"
BASELINE_FILE = "benchmark_baseline.json"  # start prices for the buy & hold benchmark (delete to restart it)
TRADES_FILE = "trades.csv"                 # append-only audit log of every simulated trade
CHART_FILE = "performance_chart.png" # The new output visual chart
INITIAL_CASH = 100000.0
COINS = ['ETH-USD', 'SOL-USD', 'LINK-USD', 'AVAX-USD', 'NEAR-USD', 'ADA-USD', 'DOT-USD']

MACRO_TICKERS = {
    '^GSPC': 'S&P 500',
    'GC=F': 'Gold',
    'DX-Y.NYB': 'US Dollar Index',
    'CL=F': 'Crude Oil'
}

# Simulation realism. FEE_RATE is an ASSUMPTION (0.10% per trade); set it to your exchange's real rate.
FEE_RATE = 0.001
REBALANCE_DRIFT = 0.05   # only re-equalize an unchanged pick set if a weight is >5 points off target
MIN_MACRO_VOTES = 2      # need at least this many macro tickers to call a regime

current_time_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
print(f"⏰ Execution Time: {current_time_str} UTC")

# ==========================================
# 📂 ADVANCED PERFORMANCE JOURNAL SYSTEM
# ==========================================
def load_or_initialize_state():
    if os.path.exists(LEDGER_FILE):
        df = pd.read_csv(LEDGER_FILE)
        return df[df['Asset_Type'] != 'BENCHMARK']
    rows = [{'Asset': 'USDT', 'Quantity': INITIAL_CASH, 'Avg_Price': 1.0, 'Total_Value': INITIAL_CASH, 'Asset_Type': 'CASH'}]
    return pd.DataFrame(rows)

def load_or_create_baseline(strategy_val, current_prices):
    """The benchmark's start prices are saved ONCE, so buy & hold really tracks price moves."""
    if os.path.exists(BASELINE_FILE):
        with open(BASELINE_FILE) as f:
            baseline = json.load(f)
    else:
        baseline = {
            'started': current_time_str,
            'capital': round(strategy_val, 2),  # benchmark starts level with the bot, so alpha starts at 0%
            'prices': {c: p for c, p in current_prices.items() if c in COINS},
            'last_prices': {},
        }
        print(f"📌 Benchmark baseline created at {current_time_str} with ${baseline['capital']:,.2f}.")
    baseline['last_prices'].update({c: p for c, p in current_prices.items() if c in COINS})
    with open(BASELINE_FILE, 'w') as f:
        json.dump(baseline, f, indent=2)
    return baseline

def benchmark_value(baseline, current_prices):
    slot = baseline['capital'] / len(COINS)
    # Coins with no start price at baseline time are held as flat cash.
    total = slot * (len(COINS) - len(baseline['prices']))
    for coin, start_price in baseline['prices'].items():
        price = current_prices.get(coin, baseline['last_prices'].get(coin, start_price))
        total += slot * price / start_price
    return total

def journal_alpha_performance(strategy_val, current_prices):
    baseline = load_or_create_baseline(strategy_val, current_prices)
    total_bnh_value = benchmark_value(baseline, current_prices)

    alpha_usdt = strategy_val - total_bnh_value
    alpha_pct = ((strategy_val / total_bnh_value) - 1) * 100 if total_bnh_value else 0.0

    new_log = pd.DataFrame([{
        'Timestamp': current_time_str,
        'Active_Bot_Value': round(strategy_val, 2),
        'Benchmark_BnH_Value': round(total_bnh_value, 2),
        'Alpha_USDT': round(alpha_usdt, 2),
        'Alpha_Percent': round(alpha_pct, 2)
    }])

    if os.path.exists(PERFORMANCE_FILE):
        perf_history = pd.read_csv(PERFORMANCE_FILE)
        perf_history = pd.concat([perf_history, new_log], ignore_index=True)
        perf_history.to_csv(PERFORMANCE_FILE, index=False)
    else:
        new_log.to_csv(PERFORMANCE_FILE, index=False)
        perf_history = new_log

    # Generate the visual chart since the dataset is updated
    generate_performance_chart(perf_history)
    return total_bnh_value, alpha_pct

def log_trades(old_qty, new_qty, trade_values, prices):
    rows = []
    for asset in sorted(set(old_qty) | set(new_qty)):
        delta = new_qty.get(asset, 0.0) - old_qty.get(asset, 0.0)
        if abs(delta) < 1e-12:
            continue
        rows.append({'Timestamp': current_time_str, 'Asset': asset, 'Side': 'BUY' if delta > 0 else 'SELL',
                     'Quantity': abs(delta), 'Price': prices[asset],
                     'Fee_USDT': FEE_RATE * abs(trade_values.get(asset, 0.0))})
    if rows:
        pd.DataFrame(rows).to_csv(TRADES_FILE, mode='a', header=not os.path.exists(TRADES_FILE), index=False)

# ==========================================
# 🎨 AUTO-GRAPH RENDERING ENGINE
# ==========================================
def generate_performance_chart(df):
    """Generates an equity curve graph and saves it as a PNG file."""
    try:
        if len(df) < 2:
            print("📊 Chart generation skipped: Waiting for more historical timeline data rows.")
            return

        # Parse string timestamps into proper matplotlib datetime elements
        df['PlotTime'] = pd.to_datetime(df['Timestamp'])
        
        # Build layout grid setup (Top Panel for Equity, Bottom Panel for Relative Alpha)
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7), gridspec_kw={'height_ratios': [2, 1]}, sharex=True)
        
        # Panel 1: Strategy vs Benchmark Capital Growth Lines
        ax1.plot(df['PlotTime'], df['Active_Bot_Value'], label='Active Bot Strategy', color='#00b0ff', lw=2.5)
        ax1.plot(df['PlotTime'], df['Benchmark_BnH_Value'], label='Buy & Hold Benchmark', color='#90a4ae', lw=1.5, linestyle='--')
        ax1.set_title('Live Dynamic Tracking Dashboard: Active Quant Bot vs Benchmark', fontsize=12, fontweight='bold', pad=12)
        ax1.set_ylabel('Portfolio Assets Value (USDT)', fontsize=10)
        ax1.legend(loc='upper left')
        ax1.grid(True, linestyle=':', alpha=0.6)
        
        # Format the numbers clean (e.g. 101,230)
        ax1.get_yaxis().set_major_formatter(plt.FuncFormatter(lambda x, loc: "{:,}".format(int(x))))
        
        # Panel 2: Alpha Area Percentage Spread
        ax2.fill_between(df['PlotTime'], df['Alpha_Percent'], 0, where=(df['Alpha_Percent'] >= 0), color='#00e676', alpha=0.3, label='Positive Alpha')
        ax2.fill_between(df['PlotTime'], df['Alpha_Percent'], 0, where=(df['Alpha_Percent'] < 0), color='#ff1744', alpha=0.3, label='Negative Alpha')
        ax2.plot(df['PlotTime'], df['Alpha_Percent'], color='#4caf50' if df['Alpha_Percent'].iloc[-1] >= 0 else '#f44336', lw=1.2)
        ax2.axhline(0, color='black', lw=0.8, linestyle=':')
        ax2.set_ylabel('Net Alpha Edge (%)', fontsize=10)
        ax2.set_xlabel('Timeline Horizon (3-Hour Automated Update Cycles)', fontsize=10)
        ax2.grid(True, linestyle=':', alpha=0.6)
        
        # Handle axis formatting
        fig.autofmt_xdate()
        plt.tight_layout()
        
        # Save output image
        plt.savefig(CHART_FILE, dpi=200)
        plt.close()
        print("📊 New visual performance graph plotted and updated successfully.")
    except Exception as e:
        print(f"⚠️ Performance chart generation failed: {e}")

# ==========================================
# 🧠 DYNAMIC MACRO REGIME ENGINE
# ==========================================
def calculate_macro_regime():
    """True = risk-on. Raises if there isn't enough data to decide (the caller then holds positions)."""
    all_tickers = ['BTC-USD'] + list(MACRO_TICKERS.keys())
    raw = yf.download(all_tickers, period='60d', interval='1d', progress=False)
    if raw.empty or 'Close' not in raw:
        raise RuntimeError("macro download returned no data")
    data = raw['Close']
    if 'BTC-USD' not in data.columns:
        raise RuntimeError("macro data unavailable (no BTC-USD series)")

    btc_series = data['BTC-USD'].dropna()
    btc_ema20 = btc_series.ewm(span=20, adjust=False).mean()
    btc_bull = btc_series.iloc[-1].item() > btc_ema20.iloc[-1].item()

    macro_votes = []
    for ticker, name in MACRO_TICKERS.items():
        if ticker not in data.columns: continue
        asset_series = data[ticker].dropna()
        if len(asset_series) < 25: continue

        # Compare returns over the SAME dates: BTC trades on weekends, these markets don't.
        btc_on_asset_dates = btc_series.reindex(asset_series.index)
        pair = pd.concat([btc_on_asset_dates.pct_change(), asset_series.pct_change()], axis=1).dropna().tail(30)
        if len(pair) < 20: continue
        correlation = pair.iloc[:, 0].corr(pair.iloc[:, 1])
        if pd.isna(correlation): continue

        asset_ema20 = asset_series.ewm(span=20, adjust=False).mean()
        asset_above_ema = asset_series.iloc[-1].item() > asset_ema20.iloc[-1].item()
        macro_votes.append(asset_above_ema if correlation >= 0 else not asset_above_ema)

    if len(macro_votes) < MIN_MACRO_VOTES:
        raise RuntimeError(f"only {len(macro_votes)} usable macro votes (need {MIN_MACRO_VOTES})")
    return btc_bull and (sum(macro_votes) >= (len(macro_votes) / 2))

def get_live_prices():
    momentum_scores, current_prices = {}, {}
    for coin in COINS:
        try:
            data = yf.download(coin, period='60d', interval='1d', progress=False)
            if data.empty:
                print(f"⚠️ No price data for {coin}")
                continue
            current_p = data['Close'].iloc[-1].item()
            current_prices[coin] = current_p
            if len(data) < 46: continue
            momentum = (current_p - data['Close'].iloc[-45].item()) / data['Close'].iloc[-45].item()
            if momentum > 0: momentum_scores[coin] = momentum
        except Exception as e:
            print(f"⚠️ Price fetch failed for {coin}: {e}")
    return [c[0] for c in sorted(momentum_scores.items(), key=lambda x: x[1], reverse=True)[:3]], current_prices

# ==========================================
# 💼 EXECUTION CONTROL
# ==========================================
def execute_trading_cycle():
    top_3_targets, current_prices = get_live_prices()
    ledger = load_or_initialize_state()

    crypto_rows = ledger[ledger['Asset_Type'] == 'CRYPTO']
    missing = [a for a in crypto_rows['Asset'] if a not in current_prices]
    if missing:
        print(f"⚠️ No price for held asset(s) {missing}. Skipping this cycle; positions unchanged.")
        return

    strategy_cash = ledger[ledger['Asset_Type'] == 'CASH']['Quantity'].sum()
    holdings = {row['Asset']: row['Quantity'] for _, row in crypto_rows.iterrows()}
    holding_values = {a: q * current_prices[a] for a, q in holdings.items()}
    total_strategy_value = strategy_cash + sum(holding_values.values())

    bnh_value, alpha_performance_metric = journal_alpha_performance(total_strategy_value, current_prices)

    print("\n==================================================")
    print(f"💳 Active Bot Valuation:  ${total_strategy_value:,.2f} USDT")
    print(f"📉 Benchmark Buy & Hold:  ${bnh_value:,.2f} USDT")
    print(f"🏆 Alpha Outperformance:  {alpha_performance_metric:+.2f}%")
    print("==================================================\n")

    try:
        is_bull = calculate_macro_regime()
    except Exception as e:
        print(f"⚠️ Regime check failed ({e}). Holding current positions; no trades this cycle.")
        return

    # Desired portfolio: equal weight in the top picks if risk-on, otherwise all cash.
    if is_bull and top_3_targets:
        target_values = {c: total_strategy_value / len(top_3_targets) for c in top_3_targets}
    else:
        target_values = {}

    # Decide whether a trade is worth making at all.
    if not target_values:
        if not holdings:
            print("😴 Risk-off and already in cash. No trade.")
            return
    elif set(holdings) == set(target_values):
        n = len(target_values)
        drift = max([abs(v / total_strategy_value - 1 / n) for v in holding_values.values()] + [strategy_cash / total_strategy_value])
        if drift <= REBALANCE_DRIFT:
            print(f"✅ Same picks, max weight drift {drift:.1%} <= {REBALANCE_DRIFT:.0%}. No trade.")
            return

    # Execute: charge FEE_RATE on every dollar bought or sold.
    assets = set(target_values) | set(holdings)
    trade_values = {a: target_values.get(a, 0.0) - holding_values.get(a, 0.0) for a in assets}
    fee = FEE_RATE * sum(abs(v) for v in trade_values.values())
    net_value = total_strategy_value - fee

    new_strategy_rows, new_qty = [], {}
    if target_values:
        slot = net_value / len(target_values)
        for coin in target_values:
            price = current_prices[coin]
            new_qty[coin] = slot / price
            new_strategy_rows.append({'Asset': coin, 'Quantity': slot / price, 'Avg_Price': price, 'Total_Value': slot, 'Asset_Type': 'CRYPTO'})
    else:
        new_strategy_rows.append({'Asset': 'USDT', 'Quantity': net_value, 'Avg_Price': 1.0, 'Total_Value': net_value, 'Asset_Type': 'CASH'})

    log_trades(holdings, new_qty, trade_values, current_prices)
    pd.DataFrame(new_strategy_rows).to_csv(LEDGER_FILE, index=False)
    print(f"🔁 Rebalanced into {sorted(target_values) or 'CASH'}; fees paid: ${fee:,.2f}")

if __name__ == "__main__":
    execute_trading_cycle()
