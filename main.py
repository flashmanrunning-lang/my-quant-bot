import os
from datetime import datetime
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
CHART_FILE = "performance_chart.png" # The new output visual chart
INITIAL_CASH = 100000.0
COINS = ['ETH-USD', 'SOL-USD', 'LINK-USD', 'AVAX-USD', 'NEAR-USD', 'ADA-USD', 'DOT-USD', 'POL-USD']

MACRO_TICKERS = {
    '^GSPC': 'S&P 500',
    'GC=F': 'Gold',
    'DX-Y.NYB': 'US Dollar Index',
    'CL=F': 'Crude Oil'
}

current_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
print(f"⏰ Execution Time: {current_time_str} UTC")

# ==========================================
# 📂 ADVANCED PERFORMANCE JOURNAL SYSTEM
# ==========================================
def load_or_initialize_state(current_prices):
    if os.path.exists(LEDGER_FILE):
        df = pd.read_csv(LEDGER_FILE)
        return df[df['Asset_Type'] != 'BENCHMARK']
    rows = [{'Asset': 'USDT', 'Quantity': INITIAL_CASH, 'Avg_Price': 1.0, 'Total_Value': INITIAL_CASH, 'Asset_Type': 'CASH'}]
    return pd.DataFrame(rows)

def journal_alpha_performance(strategy_val, current_prices):
    bnh_base_allocation = INITIAL_CASH / len(COINS)
    total_bnh_value = 0.0
    
    for coin in COINS:
        hist = yf.download(coin, period='60d', interval='1d', progress=False)
        if not hist.empty:
            start_price = hist['Close'].iloc[0].item()
            current_price = current_prices.get(coin, start_price)
            token_qty = bnh_base_allocation / start_price
            total_bnh_value += token_qty * current_price
        else:
            total_bnh_value += bnh_base_allocation

    alpha_usdt = strategy_val - total_bnh_value
    alpha_pct = ((strategy_val / total_bnh_value) - 1) * 100
    
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
    all_tickers = ['BTC-USD'] + list(MACRO_TICKERS.keys())
    data = yf.download(all_tickers, period='60d', interval='1d', progress=False)['Close']
    if data.empty or 'BTC-USD' not in data.columns: return False

    returns = data.pct_change().dropna()
    btc_series = data['BTC-USD']
    btc_ema20 = btc_series.ewm(span=20, adjust=False).mean()
    btc_bull = btc_series.iloc[-1].item() > btc_ema20.iloc[-1].item()
    
    macro_votes = []
    for ticker, name in MACRO_TICKERS.items():
        if ticker not in data.columns: continue
        correlation = returns['BTC-USD'].rolling(30).corr(returns[ticker]).iloc[-1]
        asset_series = data[ticker].dropna()
        if len(asset_series) < 2: continue
        asset_ema20 = asset_series.ewm(span=20, adjust=False).mean()
        asset_above_ema = asset_series.iloc[-1].item() > asset_ema20.iloc[-1].item()
        
        vote = asset_above_ema if correlation >= 0 else not asset_above_ema
        macro_votes.append(vote)
        
    return btc_bull and (sum(macro_votes) >= (len(macro_votes) / 2))

def get_live_prices():
    momentum_scores, current_prices = {}, {}
    for coin in COINS:
        try:
            data = yf.download(coin, period='60d', interval='1d', progress=False)
            if len(data) < 46: continue
            current_p = data['Close'].iloc[-1].item()
            momentum = (current_p - data['Close'].iloc[-45].item()) / data['Close'].iloc[-45].item()
            current_prices[coin] = current_p
            if momentum > 0: momentum_scores[coin] = momentum
        except Exception as e: pass
    return [c[0] for c in sorted(momentum_scores.items(), key=lambda x: x[1], reverse=True)[:3]], current_prices

# ==========================================
# 💼 EXECUTION CONTROL
# ==========================================
def execute_trading_cycle():
    top_3_targets, current_prices = get_live_prices()
    ledger = load_or_initialize_state(current_prices)
    
    strategy_cash = ledger[ledger['Asset_Type'] == 'CASH']['Quantity'].sum()
    strategy_crypto = sum(row['Quantity'] * current_prices.get(row['Asset'], row['Avg_Price']) for _, row in ledger[ledger['Asset_Type'] == 'CRYPTO'].iterrows())
    total_strategy_value = strategy_cash + strategy_crypto
    
    bnh_value, alpha_performance_metric = journal_alpha_performance(total_strategy_value, current_prices)
    
    print("\n==================================================")
    print(f"💳 Active Bot Valuation:  ${total_strategy_value:,.2f} USDT")
    print(f"📉 Benchmark Buy & Hold:  ${bnh_value:,.2f} USDT")
    print(f"🏆 Alpha Outperformance:  {alpha_performance_metric:+.2f}%")
    print("==================================================\n")
    
    is_bull = calculate_macro_regime()
    new_strategy_rows = []
    
    if not is_bull or not top_3_targets:
        new_strategy_rows.append({'Asset': 'USDT', 'Quantity': total_strategy_value, 'Avg_Price': 1.0, 'Total_Value': total_strategy_value, 'Asset_Type': 'CASH'})
    else:
        target_slot_size = total_strategy_value / len(top_3_targets)
        remaining_cash = total_strategy_value
        for coin in top_3_targets:
            price = current_prices[coin]
            new_strategy_rows.append({'Asset': coin, 'Quantity': target_slot_size / price, 'Avg_Price': price, 'Total_Value': target_slot_size, 'Asset_Type': 'CRYPTO'})
            remaining_cash -= target_slot_size
        if remaining_cash > 0:
            new_strategy_rows.append({'Asset': 'USDT', 'Quantity': remaining_cash, 'Avg_Price': 1.0, 'Total_Value': remaining_cash, 'Asset_Type': 'CASH'})

    pd.DataFrame(new_strategy_rows).to_csv(LEDGER_FILE, index=False)

if __name__ == "__main__":
    execute_trading_cycle()
