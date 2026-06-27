import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime
import os
import warnings

warnings.filterwarnings('ignore')
DB_FILE = "virtual_wallet.csv"

def load_wallet():
    if not os.path.exists(DB_FILE):
        df = pd.DataFrame([{'USDT': 10000.0, 'ETH': 0.0, 'SOL': 0.0, 'ADA': 0.0, 'XRP': 0.0, 'DOGE': 0.0, 'LTC': 0.0, 'LINK': 0.0, 'BCH': 0.0}])
        df.to_csv(DB_FILE, index=False)
        return df.iloc[0].to_dict()
    return pd.read_csv(DB_FILE).iloc[0].to_dict()

def save_wallet(wallet):
    pd.DataFrame([wallet]).to_csv(DB_FILE, index=False)

class CloudExchange:
    def __init__(self): 
        self.wallet = load_wallet()
    
    def get_historical_klines(self, symbol):
        yf_ticker = symbol.replace('USDT', '-USD')
        df = yf.download(yf_ticker, period="120d", interval="1d", progress=False)
        if isinstance(df.columns, pd.MultiIndex): 
            df.columns = [col[0] for col in df.columns]
        return [[idx.value // 10**6, float(row['Open']), float(row['High']), float(row['Low']), float(row['Close']), float(row['Volume']), 0, 0, 0, 0, 0, 0] for idx, row in df.iterrows()]
    
    def execute_market_buy(self, symbol, usdt_amount, current_price):
        asset = symbol.replace('USDT', '')
        if self.wallet['USDT'] >= usdt_amount:
            self.wallet['USDT'] -= usdt_amount
            self.wallet[asset] += (usdt_amount / current_price)
            print(f"   🟢 LIVE BUY: {symbol} at ${current_price:,.2f}")
            save_wallet(self.wallet)
            
    def execute_market_sell(self, symbol, current_price):
        asset = symbol.replace('USDT', '')
        if self.wallet[asset] > 0:
            self.wallet['USDT'] += (self.wallet[asset] * current_price)
            print(f"   🔴 LIVE SELL: {symbol} at ${current_price:,.2f}")
            self.wallet[asset] = 0.0
            save_wallet(self.wallet)

# 🚀 Serverless Engine Run
if __name__ == "__main__":
    exchange = CloudExchange()
    PRODUCTION_UNIVERSE = ['ETHUSDT', 'SOLUSDT', 'ADAUSDT', 'XRPUSDT', 'DOGEUSDT', 'LTCUSDT', 'LINKUSDT', 'BCHUSDT']
    print("⚠️ CLOUD ENGINE ACTIVATED...")
    
     try:
        import datetime as dt
        hk_time = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=8)
        print(f"\n🤖 RUN CHECK: {hk_time.strftime('%Y-%m-%d %H:%M:%S')} (HKT)")
        btc_raw = exchange.get_historical_klines('BTCUSDT')
        btc_df = pd.DataFrame(btc_raw, columns=['time', 'open', 'high', 'low', 'close', 'vol', 'c_time', 'q_vol', 'trades', 'tb_base', 'tb_quote', 'ignore'])
        current_btc_close = btc_df['close'].iloc[-1]
        ema_20 = btc_df['close'].ewm(span=20, adjust=False).mean().iloc[-1]
        sma_3 = btc_df['close'].rolling(3).mean().iloc[-1]
        roc_5 = btc_df['close'].pct_change(5).iloc[-1]
        
        regime = "BEAR"
        if current_btc_close > ema_20: regime = "BULL"
        elif current_btc_close < ema_20 and roc_5 < -0.05: regime = "OVERSOLD_COOLDOWN"
        elif current_btc_close > sma_3: regime = "BEAR_SQUEEZE_ABORT"

        print(f"📊 BTC: ${current_btc_close:,.2f} | REGIME: {regime}")
        wallet = exchange.wallet
        owned_tickers = [f"{asset}USDT" for asset, qty in wallet.items() if asset != 'USDT' and qty > 0]

        if regime == "BULL":
            mom_scores, last_prices = {}, {}
            for coin in PRODUCTION_UNIVERSE:
                raw = exchange.get_historical_klines(coin)
                df = pd.DataFrame(raw, columns=['time', 'open', 'high', 'low', 'close', 'vol', 'c_time', 'q_vol', 'trades', 'tb_base', 'tb_quote', 'ignore'])
                mom_scores[coin] = (df['close'].iloc[-1] / df['close'].iloc[-45]) - 1
                last_prices[coin] = df['close'].iloc[-1]
            top_3 = sorted(mom_scores, key=mom_scores.get, reverse=True)[:3]
            
            for held_coin in owned_tickers:
                if held_coin not in top_3: exchange.execute_market_sell(held_coin, last_prices[held_coin])
            
            # Recalculate wallet cash balance after potential sells
            wallet = load_wallet()
            needed_slots = 3 - len([x for x in top_3 if x in owned_tickers])
            
            if needed_slots > 0:
                alloc = wallet['USDT'] / needed_slots
                for target_coin in top_3:
                    if target_coin not in owned_tickers and alloc > 10: 
                        exchange.execute_market_buy(target_coin, alloc, last_prices[target_coin])
        else:
            for held_coin in owned_tickers:
                raw = exchange.get_historical_klines(held_coin)
                exchange.execute_market_sell(held_coin, float(raw[-1][4]))
                
        # Final Printout
        wallet = load_wallet()
        print(f"💼 END RUN PORTFOLIO: Cash: ${wallet['USDT']:,.2f}")
        print("📥 WALLET UPDATE WRITTEN TO LEDGER. SHUTTING DOWN ENGINE.")
    except Exception as e:
        print(f"🔥 ERROR: {e}")
