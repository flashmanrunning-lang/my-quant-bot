import os
from datetime import datetime
import yfinance as yf
import pandas as pd

# ==========================================
# ⚙️ CONFIGURATION
# ==========================================
LEDGER_FILE = "portfolio_ledger.csv"
INITIAL_CASH = 100000.0
COINS = ['ETH-USD', 'SOL-USD', 'LINK-USD', 'AVAX-USD', 'NEAR-USD', 'ADA-USD', 'DOT-USD', 'MATIC-USD']

print(f"⏰ Execution Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC")

# ==========================================
# 📂 PORTFOLIO LEDGER SYSTEM (WITH BENCHMARK)
# ==========================================
def load_or_initialize_ledger(current_prices):
    """Loads portfolio state or initializes a fresh one with a BnH benchmark."""
    if os.path.exists(LEDGER_FILE):
        df = pd.read_csv(LEDGER_FILE)
        
        # Backward compatibility: If benchmark rows don't exist yet, inject them now
        if 'BENCHMARK' not in df['Asset_Type'].values:
            print("🔄 Upgrading existing ledger to track Buy & Hold Alpha...")
            # Calculate current strategy value to use as the baseline for the benchmark split
            strat_val = df[df['Asset_Type'].isin(['CASH', 'CRYPTO'])]['Total_Value'].sum()
            bnh_rows = []
            allocation = strat_val / len(COINS)
            for coin in COINS:
                price = current_prices.get(coin, 1.0)
                bnh_rows.append({
                    'Asset': coin, 'Quantity': allocation / price,
                    'Avg_Price': price, 'Total_Value': allocation, 'Asset_Type': 'BENCHMARK'
                })
            df = pd.concat([df, pd.DataFrame(bnh_rows)], ignore_index=True)
            df.to_csv(LEDGER_FILE, index=False)
        return df
    
    # Fresh initialization
    print(f"✨ Fresh simulation started! Allocating ${INITIAL_CASH:,.2f} USDT.")
    rows = []
    
    # 1. Active Strategy starting cash
    rows.append({
        'Asset': 'USDT', 'Quantity': INITIAL_CASH, 
        'Avg_Price': 1.0, 'Total_Value': INITIAL_CASH, 'Asset_Type': 'CASH'
    })
    
    # 2. Buy & Hold Benchmark baseline (split evenly)
    bnh_allocation = INITIAL_CASH / len(COINS)
    for coin in COINS:
        price = current_prices.get(coin, 1.0)
        rows.append({
            'Asset': coin, 'Quantity': bnh_allocation / price,
            'Avg_Price': price, 'Total_Value': bnh_allocation, 'Asset_Type': 'BENCHMARK'
        })
        
    df = pd.DataFrame(rows)
    df.to_csv(LEDGER_FILE, index=False)
    return df

# ==========================================
# 🔍 MARKET DATA & REGIME CHECK
# ==========================================
def get_market_regime():
    """Checks if the live BTC price is currently above its 20-Day EMA."""
    print("Fetching live BTC data...")
    btc = yf.download('BTC-USD', period='60d', interval='1d', progress=False)
    if btc.empty:
        return False
    
    btc['EMA_20'] = btc['Close'].ewm(span=20, adjust=False).mean()
    current_price = btc['Close'].iloc[-1].item()
    current_ema = btc['EMA_20'].iloc[-1].item()
    
    print(f"ℹ️ BTC Live Price: ${current_price:,.2f} | 20-Day EMA: ${current_ema:,.2f}")
    return current_price > current_ema

def get_live_prices():
    """Fetches the latest live prices and calculations for the coin universe."""
    momentum_scores = {}
    current_prices = {}
    
    print("Scanning altcoin market prices and momentum...")
    for coin in COINS:
        try:
            data = yf.download(coin, period='60d', interval='1d', progress=False)
            if len(data) < 46:
                continue
            
            current_p = data['Close'].iloc[-1].item()
            past_p = data['Close'].iloc[-45].item()
            
            momentum = (current_p - past_p) / past_p
            current_prices[coin] = current_p
            
            if momentum > 0:
                momentum_scores[coin] = momentum
        except Exception as e:
            print(f"⚠️ Error scanning {coin}: {e}")
            
    sorted_coins = sorted(momentum_scores.items(), key=lambda x: x[1], reverse=True)
    top_3 = [coin[0] for coin in sorted_coins[:3]]
    return top_3, current_prices

# ==========================================
# 💼 TRADING & PERFORMANCE ENGINE
# ==========================================
def execute_trading_cycle():
    # 1. Fetch live market rates
    top_3_targets, current_prices = get_live_prices()
    
    # 2. Load current database state
    ledger = load_or_initialize_ledger(current_prices)
    
    # 3. Calculate ACTIVE STRATEGY valuation
    strategy_cash = ledger[ledger['Asset_Type'] == 'CASH']['Quantity'].sum()
    strategy_crypto = 0.0
    for _, row in ledger[ledger['Asset_Type'] == 'CRYPTO'].iterrows():
        ticker = row['Asset']
        price = current_prices.get(ticker, row['Avg_Price'])
        strategy_crypto += row['Quantity'] * price
        
    total_strategy_value = strategy_cash + strategy_crypto
    
    # 4. Calculate BUY & HOLD BENCHMARK valuation
    total_bnh_value = 0.0
    benchmark_rows = ledger[ledger['Asset_Type'] == 'BENCHMARK'].copy()
    for idx, row in benchmark_rows.iterrows():
        ticker = row['Asset']
        price = current_prices.get(ticker, row['Avg_Price'])
        current_val = row['Quantity'] * price
        benchmark_rows.at[idx, 'Total_Value'] = current_val
        total_bnh_value += current_val

    # 5. Calculate Alpha Performance
    alpha_usdt = total_strategy_value - total_bnh_value
    alpha_pct = ((total_strategy_value / total_bnh_value) - 1) * 100
    alpha_sign = "+" if alpha_usdt >= 0 else ""

    print("\n==================================================")
    print(f"💳 Active Bot Net Worth:   ${total_strategy_value:,.2f} USDT")
    print(f"📉 Buy & Hold Benchmark:   ${total_bnh_value:,.2f} USDT")
    print(f"🏆 Strategy Outperformance (Alpha): {alpha_sign}${alpha_usdt:,.2f} ({alpha_sign}{alpha_pct:.2f}%)")
    print("==================================================\n")
    
    # 6. Apply Execution Logic (Regime Shift Processing)
    is_bull = get_market_regime()
    new_strategy_rows = []
    
    if not is_bull:
        print("🔴 BEAR MARKET REGIME: Capital sitting safely in liquid USDT cash.")
        new_strategy_rows.append({
            'Asset': 'USDT', 'Quantity': total_strategy_value, 
            'Avg_Price': 1.0, 'Total_Value': total_strategy_value, 'Asset_Type': 'CASH'
        })
    else:
        print(f"🟢 BULL MARKET REGIME: Target assets to hold: {top_3_targets}")
        if not top_3_targets:
            print("⚠️ No assets show positive momentum. Holding cash.")
            new_strategy_rows.append({
                'Asset': 'USDT', 'Quantity': total_strategy_value, 
                'Avg_Price': 1.0, 'Total_Value': total_strategy_value, 'Asset_Type': 'CASH'
            })
        else:
            target_slot_size = total_strategy_value / len(top_3_targets)
            remaining_cash = total_strategy_value
            
            for coin in top_3_targets:
                price = current_prices[coin]
                allocated_capital = target_slot_size
                qty_to_hold = allocated_capital / price
                
                new_strategy_rows.append({
                    'Asset': coin, 'Quantity': qty_to_hold,
                    'Avg_Price': price, 'Total_Value': allocated_capital, 'Asset_Type': 'CRYPTO'
                })
                remaining_cash -= allocated_capital
                
            if remaining_cash > 0:
                new_strategy_rows.append({
                    'Asset': 'USDT', 'Quantity': remaining_cash, 
                    'Avg_Price': 1.0, 'Total_Value': remaining_cash, 'Asset_Type': 'CASH'
                })

    # 7. Merge active tracking back with static benchmark ledger rows & save
    final_ledger = pd.concat([pd.DataFrame(new_strategy_rows), benchmark_rows], ignore_index=True)
    final_ledger.to_csv(LEDGER_FILE, index=False)
    print("💾 Portfolio ledger records updated successfully.")

if __name__ == "__main__":
    execute_trading_cycle()
