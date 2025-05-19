import ccxt
import pandas as pd
import numpy as np
import time
import logging
from datetime import datetime

# Set up logging
logging.basicConfig(
    filename='trading_bot.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Bot configuration
EXCHANGE = 'binance'
SYMBOL = 'BTC/USDT'  # Trading pair
TIMEFRAME = '1h'     # Candlestick timeframe
SHORT_SMA = 10       # Short SMA period
LONG_SMA = 50        # Long SMA period
POSITION_SIZE = 0.001  # Fixed position size in BTC
SLEEP_INTERVAL = 3600  # Sleep for 1 hour between loops
API_KEY = 'your_api_key_here'  # Replace with your API key
API_SECRET = 'your_api_secret_here'  # Replace with your API secret

# Initialize exchange
exchange = ccxt.binance({
    'apiKey': API_KEY,
    'secret': API_SECRET,
    'enableRateLimit': True
})

def fetch_ohlcv(symbol, timeframe, limit=100):
    """Fetch OHLCV data from the exchange."""
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except Exception as e:
        logging.error(f"Error fetching OHLCV: {e}")
        return None

def calculate_sma(df, short_period, long_period):
    """Calculate short and long SMAs and generate signals."""
    df['short_sma'] = df['close'].rolling(window=short_period).mean()
    df['long_sma'] = df['close'].rolling(window=long_period).mean()
    
    # Generate signals
    df['signal'] = 0
    df['signal'] = np.where(df['short_sma'] > df['long_sma'], 1, 0)
    df['signal'] = np.where(df['short_sma'] < df['long_sma'], -1, df['signal'])
    
    # Detect changes in signal
    df['position'] = df['signal'].diff()
    return df

def place_order(symbol, side, amount):
    """Place a market order."""
    try:
        if side == 'buy':
            order = exchange.create_market_buy_order(symbol, amount)
            logging.info(f"Buy order placed: {amount} {symbol} at market price")
        elif side == 'sell':
            order = exchange.create_market_sell_order(symbol, amount)
            logging.info(f"Sell order placed: {amount} {symbol} at market price")
        return order
    except Exception as e:
        logging.error(f"Error placing {side} order: {e}")
        return None

def get_balance(asset):
    """Fetch the balance of a specific asset."""
    try:
        balance = exchange.fetch_balance()
        return balance['free'].get(asset, 0)
    except Exception as e:
        logging.error(f"Error fetching balance: {e}")
        return 0

def main():
    logging.info("Trading bot started")
    
    while True:
        try:
            # Fetch market data
            df = fetch_ohlcv(SYMBOL, TIMEFRAME, limit=LONG_SMA + 1)
            if df is None or len(df) < LONG_SMA:
                logging.warning("Insufficient data, retrying...")
                time.sleep(SLEEP_INTERVAL)
                continue

            # Calculate SMAs and signals
            df = calculate_sma(df, SHORT_SMA, LONG_SMA)
            latest = df.iloc[-1]

            # Check balances
            btc_balance = get_balance('BTC')
            usdt_balance = get_balance('USDT')
            logging.info(f"BTC balance: {btc_balance}, USDT balance: {usdt_balance}")

            # Execute trades based on signals
            if latest['position'] == 2:  # Short SMA crosses above Long SMA -> Buy
                if usdt_balance > POSITION_SIZE * latest['close']:
                    place_order(SYMBOL, 'buy', POSITION_SIZE)
                else:
                    logging.warning("Insufficient USDT balance to buy")
            elif latest['position'] == -2:  # Short SMA crosses below Long SMA -> Sell
                if btc_balance >= POSITION_SIZE:
                    place_order(SYMBOL, 'sell', POSITION_SIZE)
                else:
                    logging.warning("Insufficient BTC balance to sell")

            logging.info("Cycle completed, sleeping...")
            time.sleep(SLEEP_INTERVAL)

        except Exception as e:
            logging.error(f"Error in main loop: {e}")
            time.sleep(SLEEP_INTERVAL)

if __name__ == "__main__":
    main()