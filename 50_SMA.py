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

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Global variables
ORDER_ID = 1

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
        self.date = datetime.now(pytz.timezone("America/New_York"))

# Bot Logic
class Bot:
    def __init__(self):
        self.ib = IBApi()
        self.bars = []
        self.current_bar = Bar()
        self.req_id = 1
        self.sma_period = 50
        self.position_active = False  # Track open positions
        self.timezone = pytz.timezone("America/New_York")
        self.initial_bar_time = datetime.now(self.timezone)

        # Initialize connection
        try:
            self.ib.connect("127.0.0.1", 7497, 1)
            logger.info("Connected to IB API")
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            raise

        # Start IB thread
        ib_thread = threading.Thread(target=self.run_loop, daemon=True)
        ib_thread.start()
        time.sleep(2)  # Wait for connection

        # Get user inputs
        self.symbol = input("Enter the symbol to trade: ").upper()
        self.bar_size = int(input("Enter bar size in minutes: "))
        self.bar_size_str = f"{self.bar_size} min{'s' if self.bar_size > 1 else ''}"

        # Request historical data
        query_time = (datetime.now(self.timezone) - timedelta(days=2)).replace(
            hour=16, minute=0, second=0, microsecond=0
        ).strftime("%Y%m%d %H:%M:%S")
        contract = self.create_contract()
        self.ib.reqIds(-1)
        self.ib.reqHistoricalData(
            self.req_id, contract, "", "2 D", self.bar_size_str, "TRADES", 1, 1, True, []
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

    def bracket_order(self, parent_order_id, action, quantity, profit_target, stop_loss):
        """Create bracket order."""
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

        # Stop loss
        stop_order = Order()
        stop_order.orderId = parent_order_id + 2
        stop_order.orderType = "STP"
        stop_order.action = "SELL"
        stop_order.totalQuantity = quantity
        stop_order.auxPrice = round(stop_loss, 2)
        stop_order.parentId = parent_order_id
        stop_order.transmit = True

        return [parent, profit_order, stop_order]

    def on_bar_update(self, reqId, bar, realtime):
        """Process bar updates."""
        global ORDER_ID
        try:
            # Historical data
            if not realtime:
                self.bars.append(bar)
                return

            # Real-time bar
            bar_time = datetime.strptime(bar.date, "%Y%m%d %H:%M:%S").astimezone(self.timezone)
            minutes_diff = (bar_time - self.initial_bar_time).total_seconds() / 60.0
            self.current_bar.date = bar_time

            # Update current bar
            if self.current_bar.open == 0:
                self.current_bar.open = bar.open
            if self.current_bar.high == 0 or bar.high > self.current_bar.high:
                self.current_bar.high = bar.high
            if self.current_bar.low == 0 or bar.low < self.current_bar.low:
                self.current_bar.low = bar.low
            self.current_bar.close = bar.close

            # On bar close
            if minutes_diff > 0 and minutes_diff % self.bar_size < 1e-6:
                self.initial_bar_time = bar_time
                logger.info(f"Bar closed: {bar_time}")

                # Calculate SMA
                if len(self.bars) >= self.sma_period:
                    closes = np.array([b.close for b in self.bars[-self.sma_period:]])
                    sma = ta.trend.sma(pd.Series(closes), self.sma_period).iloc[-1]
                    logger.info(f"SMA: {sma:.2f}")

                    # Check buy conditions
                    last_bar = self.bars[-1]
                    if (not self.position_active and
                        bar.close > last_bar.high and
                        self.current_bar.low > last_bar.low and
                        bar.close > sma and
                        last_bar.close < sma):
                        logger.info("Buy signal triggered")
                        profit_target = bar.close * 1.02
                        stop_loss = bar.close * 0.99
                        quantity = 1
                        bracket = self.bracket_order(ORDER_ID, "BUY", quantity, profit_target, stop_loss)
                        contract = self.create_contract()
                        oca_group = f"OCA_{ORDER_ID}"
                        for order in bracket:
                            order.ocaGroup = oca_group
                            self.ib.placeOrder(order.orderId, contract, order)
                        ORDER_ID += 3
                        self.position_active = True  # Mark position as open

                # Append closed bar
                self.bars.append(self.current_bar)
                self.current_bar = Bar()

        except Exception as e:
            logger.error(f"Bar update error: {e}")

# Start Bot
if __name__ == "__main__":
    try:
        bot = Bot()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot initialization failed: {e}")