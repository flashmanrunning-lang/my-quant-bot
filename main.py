import os
import time
import requests
import hmac
import hashlib
import pandas as pd
import numpy as np
from datetime import datetime
import warnings

warnings.filterwarnings('ignore')

# 🌐 Binance Testnet Configurations
API_URL = "https://testnet.binance.vision"
API_KEY = os.environ.get("BINANCE_API_KEY")
SECRET_KEY = os.environ.get("BINANCE_SECRET_KEY")

def binance_request(endpoint, method="GET", params=None):
    if params is None: params = {}
    params['timestamp'] = int(time.time() * 1000)
    
    # Generate cryptographic signature required by Binance
    query_string = '&'.join([f"{k}={v}" for k, v in params.items()])
    signature = hmac.new(SECRET_KEY.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()
    params['signature'] = signature
    
    headers = {'X-MBX-APIKEY': API_KEY}
    url = f"{API_URL}{endpoint}"
    
    if method == "GET":
        response = requests.get(url, headers=headers, params=params)
    else:
        response = requests.post(url, headers=headers, params=params)
    return response.json()

def get_historical_klines(symbol):
    url = f"{API_URL}/api/v3/klines"
    params = {'symbol': symbol, 'interval': '1d', 'limit': 120}
    data = requests.get(url, params=params).json()
    return [[int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5])] for x in data]

def get_asset_balance(asset):
    account_info = binance_request("/api/v3/account", "GET")
    for balance in account_info.get('balances', []):
        if balance['asset'] == asset:
            return float(balance['free'])
    return 0.0

def execute_market_order(symbol, side, quantity=None, quote_quantity=None):
    params = {'symbol': symbol, 'side': side, 'type': 'MARKET'}
    if quantity: params['quantity'] = quantity
    if quote_quantity: params['quoteOrderQty'] = round(quote_quantity, 2)
    
    res = binance_request("/api/v3/order", "POST", params)
    if "orderId" in res:
        print(f"   🔥 BINANCE {side} EXECUTED: {symbol}")
    else:
        print(f"   ❌ BINANCE ERROR: {res.get('msg', res)}")

if __name__ == "__main__":
    if not API_KEY or not SECRET_KEY:
        print("🔥 CRITICAL ERROR: API Keys missing from GitHub Secrets.")
        exit(1)
        
    PRODUCTION_UNIVERSE = ['ETHUSDT', 'SOLUSDT', 'ADAUSDT', 'XRPUSDT', 'DOGEUSDT', 'LTCUSDT', 'LINKUSDT', 'BCHUSDT']
    print("⚠️ LIVE BINANCE TESTNET ENGINE ARMED...")
    
    try:
        # Calculate Hong Kong Time Log
        hk_time = datetime.utcnow() + pd.Timedelta(hours=8)
        print(f"\n🤖 RUN CHECK: {hk_time.strftime('%Y-%m-%d %H:%M:%S')} (HKT)")
        
        # Trend Analysis via Bitcoin
        btc_raw = get_historical_klines('BTCUSDT')
        btc_df = pd.DataFrame(btc_raw, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        current_btc_close = btc_df['close'].iloc[-1]
        ema_20 = btc_df['close'].ewm(span=20, adjust=False).mean().iloc[-1]
        sma_3 = btc_df['close'].rolling(3).mean().iloc[-1]
        roc_5 = btc_df['close'].pct_change(5).iloc[-1]
        
        regime = "BEAR"
        if current_btc_close > ema_20: regime = "BULL"
        elif current_btc_close < ema_20 and roc_5 < -0.05: regime = "OVERSOLD_COOLDOWN"
        elif current_btc_close > sma_3: regime = "BEAR_SQUEEZE_ABORT"

        print(f"📊 BTC: ${current_btc_close:,.2f} | REGIME: {regime}")
        
        # Scan what we currently hold on the live Testnet exchange
        owned_tickers = []
        for coin in PRODUCTION_UNIVERSE:
            asset = coin.replace('USDT', '')
            if get_asset_balance(asset) > 0.001:  # Filtering dust balances
                owned_tickers.append(coin)

        if regime == "BULL":
            mom_scores = {}
            for coin in PRODUCTION_UNIVERSE:
                raw = get_historical_klines(coin)
                df = pd.DataFrame(raw, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
                mom_scores[coin] = (df['close'].iloc[-1] / df['close'].iloc[-45]) - 1
            top_3 = sorted(mom_scores, key=mom_scores.get, reverse=True)[:3]
            
            # 1. Sell assets no longer in top 3
            for held_coin in owned_tickers:
                if held_coin not in top_3:
                    asset = held_coin.replace('USDT', '')
                    execute_market_order(held_coin, "SELL", quantity=get_asset_balance(asset))
            
            # 2. Buy into the new top 3 leaders
            usdt_balance = get_asset_balance('USDT')
            needed_slots = 3 - len([x for x in top_3 if x in owned_tickers])
            
            if needed_slots > 0 and usdt_balance > 10:
                alloc = usdt_balance / needed_slots
                for target_coin in top_3:
                    if target_coin not in owned_tickers:
                        execute_market_order(target_coin, "BUY", quote_quantity=alloc)
        else:
            # Bear Market Safety Routine: Liquidate everything back to USDT
            print("📉 BEAR REGIME DETECTED. LIQUIDATING ALTS FOR SAFETY...")
            for held_coin in owned_tickers:
                asset = held_coin.replace('USDT', '')
                execute_market_order(held_coin, "SELL", quantity=get_asset_balance(asset))
                
        print(f"💼 END RUN: Live Testnet USDT Balance = ${get_asset_balance('USDT'):,.2f}")
    except Exception as e:
        print(f"🔥 ENGINE ERROR: {e}")
