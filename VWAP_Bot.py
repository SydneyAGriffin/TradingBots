# Imports
import logging
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order
import pandas as pd
import numpy as np
import ta
from datetime import datetime, timedelta
import pytz
import threading
import time
import math

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Global variables
ORDER_ID = 1
PACIFIC_TZ = pytz.timezone("America/Los_Angeles")
EASTERN_TZ = pytz.timezone("America/New_York")

# IB API Class
class IBApi(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.data = {}  # Store historical data per reqId

    def historicalData(self, reqId, bar):
        """Handle historical bar data."""
        try:
            self.data.setdefault(reqId, []).append(bar)
            bot.on_bar_update(reqId, bar, False)
        except Exception as e:
            logger.error(f"Historical data error: {e}")

    def historicalDataUpdate(self, reqId, bar):
        """Handle real-time bar updates."""
        try:
            bot.on_bar_update(reqId, bar, True)
        except Exception as e:
            logger.error(f"Real-time data error: {e}")

    def historicalDataEnd(self, reqId, start, end):
        """Log end of historical data."""
        logger.info(f"Historical data ended for reqId {reqId}")

    def nextValidId(self, orderId):
        """Set next valid order ID."""
        global ORDER_ID
        ORDER_ID = orderId
        logger.info(f"Next valid order ID: {ORDER_ID}")

    def error(self, reqId, errorCode, errorString):
        """Handle API errors."""
        logger.error(f"Error {errorCode}: {errorString}")

# Bar Object
class Bar:
    def __init__(self):
        self.open = 0.0
        self.high = 0.0
        self.low = 0.0
        self.close = 0.0
        self.volume = 0
        self.date = datetime.now(PACIFIC_TZ)

# Bot Logic
class VWAPBot:
    def __init__(self):
        self.ib = IBApi()
        self.bars = []
        self.current_bar = Bar()
        self.req_id = 1
        self.sma_period = 50
        self.position_active = False
        self.trades_today = 0
        self.last_trade_date = None
        self.vwap_cumulative_price_volume = 0.0
        self.vwap_cumulative_volume = 0
        self.vwap = 0.0
        self.sma = None
        self.account_balance = 1000.0  # Starting balance (adjust as needed)

        # Initialize connection at 2:00 AM PDT
        self.start_connection()

    def start_connection(self):
        """Connect to IB and sleep until 6:30 AM PDT."""
        try:
            self.ib.connect("127.0.0.1", 7497, 1)
            logger.info("Connected to IB API")
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            raise

        # Start IB thread
        ib_thread = threading.Thread(target=self.run_loop, daemon=True)
        ib_thread.start()
        time.sleep(2)

        # Get user inputs
        self.symbol = input("Enter the symbol to trade: ").upper()
        self.bar_size = int(input("Enter bar size in minutes: "))
        self.bar_size_str = f"{self.bar_size} min{'s' if self.bar_size > 1 else ''}"

        # Sleep until 6:30 AM PDT
        now = datetime.now(PACIFIC_TZ)
        target_time = now.replace(hour=6, minute=30, second=0, microsecond=0)
        if now.hour >= 6 and now.minute >= 30:
            target_time += timedelta(days=1)
        seconds_to_sleep = (target_time - now).total_seconds()
        if seconds_to_sleep > 0:
            logger.info(f"Sleeping until 6:30 AM PDT ({seconds_to_sleep:.0f} seconds)")
            time.sleep(seconds_to_sleep)

        # Initialize trading
        self.initialize_trading()

    def initialize_trading(self):
        """Start trading at 6:30 AM PDT."""
        logger.info("Market open, starting VWAP strategy")
        self.reset_vwap()
        contract = self.create_contract()
        self.ib.reqIds(-1)
        self.ib.reqHistoricalData(
            self.req_id, contract, "", "1 D", self.bar_size_str, "TRADES", 1, 1, True, []
        )

    def create_contract(self):
        """Create IB contract."""
        contract = Contract()
        contract.symbol = self.symbol
        contract.secType = "STK"
        contract.exchange = "SMART"
        contract.currency = "USD"
        return contract

    def run_loop(self):
        """Run IB event loop."""
        try:
            self.ib.run()
        except Exception as e:
            logger.error(f"IB run loop error: {e}")

    def reset_vwap(self):
        """Reset VWAP at 6:30 AM PDT."""
        self.vwap_cumulative_price_volume = 0.0
        self.vwap_cumulative_volume = 0
        self.vwap = 0.0
        self.trades_today = 0
        self.last_trade_date = datetime.now(PACIFIC_TZ).date()
        logger.info("VWAP reset for new trading day")

    def get_position_size(self):
        """Determine number of shares based on account balance."""
        if self.account_balance > 5000:
            return 2  # Scale to 2 shares if balance > $5,000
        return 1  # Default to 1 share

    def bracket_order(self, parent_order_id, action, quantity, profit_target):
        """Create bracket order with trailing stop."""
        contract = self.create_contract()
        # Parent order
        parent = Order()
        parent.orderId = parent_order_id
        parent.orderType = "MKT"
        parent.action = action
        parent.totalQuantity = quantity
        parent.transmit = False

        # Profit target
        profit_order = Order()
        profit_order.orderId = parent_order_id + 1
        profit_order.orderType = "LMT"
        profit_order.action = "SELL"
        profit_order.totalQuantity = quantity
        profit_order.lmtPrice = round(profit_target, 2)
        profit_order.parentId = parent_order_id
        profit_order.transmit = False

        # Trailing stop
        trailing_stop_order = Order()
        trailing_stop_order.orderId = parent_order_id + 2
        trailing_stop_order.orderType = "TRAIL"
        trailing_stop_order.action = "SELL"
        trailing_stop_order.totalQuantity = quantity
        trailing_stop_order.trailingPercent = 2.0  # 2% trailing stop
        trailing_stop_order.parentId = parent_order_id
        trailing_stop_order.transmit = True

        return [parent, profit_order, trailing_stop_order]

    def on_bar_update(self, reqId, bar, realtime):
        """Process bar updates."""
        global ORDER_ID
        try:
            now = datetime.now(PACIFIC_TZ)
            # Reset VWAP daily at 6:30 AM PDT
            if now.date() != self.last_trade_date and now.hour >= 6 and now.minute >= 30:
                self.reset_vwap()

            # Historical data
            if not realtime:
                self.bars.append(bar)
                return

            # Real-time bar
            bar_time = datetime.strptime(bar.date, "%Y%m%d %H:%M:%S").astimezone(EASTERN_TZ)
            self.current_bar.date = bar_time

            # Update current bar
            if self.current_bar.open == 0:
                self.current_bar.open = bar.open
            if self.current_bar.high == 0 or bar.high > self.current_bar.high:
                self.current_bar.high = bar.high
            if self.current_bar.low == 0 or bar.low < self.current_bar.low:
                self.current_bar.low = bar.low
            self.current_bar.close = bar.close
            self.current_bar.volume = bar.volume

            # On bar close
            minutes_diff = (bar_time - datetime.combine(bar_time.date(), datetime.min.time(), EASTERN_TZ)).total_seconds() / 60.0
            if minutes_diff > 0 and minutes_diff % self.bar_size < 1e-6:
                # Skip first 15 minutes (9:30–9:45 AM EDT)
                if bar_time.hour == 9 and bar_time.minute < 45:
                    logger.info("Skipping trade: Within first 15 minutes of market open")
                    return

                # Update VWAP
                typical_price = (bar.high + bar.low + bar.close) / 3
                price_volume = typical_price * bar.volume
                self.vwap_cumulative_price_volume += price_volume
                self.vwap_cumulative_volume += bar.volume
                if self.vwap_cumulative_volume > 0:
                    self.vwap = self.vwap_cumulative_price_volume / self.vwap_cumulative_volume
                logger.info(f"VWAP: {self.vwap:.2f}, Price: {bar.close:.2f}")

                # Calculate 50 SMA
                if len(self.bars) >= self.sma_period:
                    closes = np.array([b.close for b in self.bars[-self.sma_period:]])
                    self.sma = ta.trend.sma(pd.Series(closes), self.sma_period).iloc[-1]
                    logger.info(f"50 SMA: {self.sma:.2f}")

                    # Check volume filter (avg volume over last 10 bars)
                    avg_volume = np.mean([b.volume for b in self.bars[-10:]]) if len(self.bars) >= 10 else 0
                    if avg_volume < 10000:
                        logger.info("Skipping trade: Average volume too low")
                        return

                    # Check buy conditions
                    if (not self.position_active and
                        self.trades_today < 3 and
                        bar.close < self.vwap * 0.99 and
                        bar.close > self.sma):
                        logger.info(f"Buy signal: Price {bar.close:.2f} < VWAP {self.vwap:.2f}, > 50 SMA {self.sma:.2f}")
                        quantity = self.get_position_size()
                        profit_target = self.vwap * 1.01
                        bracket = self.bracket_order(ORDER_ID, "BUY", quantity, profit_target)
                        contract = self.create_contract()
                        oca_group = f"OCA_{ORDER_ID}"
                        for order in bracket:
                            order.ocaGroup = oca_group
                            self.ib.placeOrder(order.orderId, contract, order)
                        ORDER_ID += 3
                        self.position_active = True
                        self.trades_today += 1
                        logger.info(f"Placed buy order for {quantity} shares")

                # Append closed bar
                self.bars.append(self.current_bar)
                self.current_bar = Bar()

        except Exception as e:
            logger.error(f"Bar update error: {e}")

# Start Bot
if __name__ == "__main__":
    try:
        bot = VWAPBot()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot initialization failed: {e}")