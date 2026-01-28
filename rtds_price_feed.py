"""
Polymarket RTDS Live Price Feed
===============================
Connects to Polymarket's Real-Time Data Stream WebSocket for live BTC/USD prices.
This is the same price feed Polymarket uses for 15-minute market settlement.

WebSocket: wss://ws-live-data.polymarket.com
Topic: crypto_prices_chainlink
Symbol: btc/usd
"""

import asyncio
import json
import time
from threading import Thread, Event
from collections import deque

try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    print("WARNING: websockets package not installed. Run: pip3 install websockets")


class RTDSPriceFeed:
    """Real-time BTC price from Polymarket RTDS WebSocket."""

    WS_URL = "wss://ws-live-data.polymarket.com"
    PING_INTERVAL = 5  # seconds

    def __init__(self):
        self.current_price = None
        self.last_update = 0
        self.window_start_price = None  # Price at T=0 of current window
        self.window_start_time = None
        self._stop_event = Event()
        self._thread = None
        self._price_history = deque(maxlen=100)
        self._connected = False

    def start(self):
        """Start WebSocket connection in background daemon thread."""
        if not WEBSOCKETS_AVAILABLE:
            print("[RTDS] Cannot start - websockets package not installed")
            return False

        self._stop_event.clear()
        self._thread = Thread(target=self._run_loop, daemon=True, name="RTDS-Price-Feed")
        self._thread.start()
        print("[RTDS] Starting WebSocket connection...")
        return True

    def _run_loop(self):
        """Run asyncio event loop in background thread."""
        asyncio.run(self._connect())

    async def _connect(self):
        """Connect to RTDS WebSocket and receive price updates."""
        RESUB_INTERVAL = 3  # Re-subscribe every 3 seconds to get fresh batch data

        while not self._stop_event.is_set():
            try:
                async with websockets.connect(
                    self.WS_URL,
                    ping_interval=self.PING_INTERVAL,
                    ping_timeout=10
                ) as ws:
                    self._connected = True
                    print(f"[RTDS] Connected to {self.WS_URL}")

                    # Subscribe to crypto_prices_chainlink topic for BTC/USD
                    # The subscription uses "crypto_prices_chainlink" but response comes back as "crypto_prices"
                    subscribe_msg = {
                        "action": "subscribe",
                        "subscriptions": [{
                            "topic": "crypto_prices_chainlink",
                            "type": "*",
                            "filters": json.dumps({"symbol": "btc/usd"})
                        }]
                    }

                    async def resubscribe_task():
                        """Periodically re-subscribe to get fresh batch data."""
                        while not self._stop_event.is_set():
                            try:
                                await ws.send(json.dumps(subscribe_msg))
                                await asyncio.sleep(RESUB_INTERVAL)
                            except:
                                break

                    # Start resubscription task
                    resub_task = asyncio.create_task(resubscribe_task())

                    try:
                        # Receive messages
                        async for msg in ws:
                            if self._stop_event.is_set():
                                break

                            # Skip empty messages
                            if not msg or not msg.strip():
                                continue

                            try:
                                data = json.loads(msg)
                                self._handle_message(data)
                            except json.JSONDecodeError:
                                continue
                    finally:
                        resub_task.cancel()
                        try:
                            await resub_task
                        except asyncio.CancelledError:
                            pass

            except Exception as e:
                self._connected = False
                if not self._stop_event.is_set():
                    print(f"[RTDS] Connection error: {e}, reconnecting in 2s...")
                    await asyncio.sleep(2)

    def _handle_message(self, data):
        """Process incoming WebSocket message."""
        topic = data.get("topic")

        # Handle crypto_prices topic (the actual format from Polymarket RTDS)
        # Note: We subscribe to "crypto_prices_chainlink" but response comes as "crypto_prices"
        # Format: {"topic": "crypto_prices", "payload": {"data": [{timestamp, value}, ...], "symbol": "btc/usd"}}
        if topic == "crypto_prices":
            payload = data.get("payload", {})
            symbol = payload.get("symbol", "")

            # Check if this is BTC/USD data
            if symbol == "btc/usd" or "btc" in str(symbol).lower():
                # Data is an array of {timestamp, value} objects - get the latest
                data_array = payload.get("data", [])
                if data_array and isinstance(data_array, list):
                    # Get the most recent data point (highest timestamp)
                    latest = max(data_array, key=lambda x: x.get("timestamp", 0))
                    price = latest.get("value")
                    if price:
                        self.current_price = float(price)
                        self.last_update = time.time()
                        self._price_history.append((self.last_update, self.current_price))
                        self._check_window_boundary()

        # Also handle crypto_prices_chainlink if it ever appears (for future compatibility)
        elif topic == "crypto_prices_chainlink":
            payload = data.get("payload", {})
            if payload.get("symbol") == "btc/usd":
                price = payload.get("value")
                if price:
                    self.current_price = float(price)
                    self.last_update = time.time()
                    self._price_history.append((self.last_update, self.current_price))
                    self._check_window_boundary()

    def _check_window_boundary(self):
        """Capture price at window start (every 15 minutes at :00, :15, :30, :45)."""
        current_time = int(time.time())
        window_start = (current_time // 900) * 900

        if self.window_start_time != window_start:
            self.window_start_time = window_start
            self.window_start_price = self.current_price
            print(f"[RTDS] New window. Price to beat: ${self.current_price:,.2f}")

    def get_price_with_age(self):
        """Get current price and age in seconds (compatible with Chainlink interface)."""
        if self.current_price is None:
            return None, 0
        age = int(time.time() - self.last_update)
        return self.current_price, age

    def get_window_delta(self):
        """Get delta from window start price (the 'price to beat')."""
        if self.current_price is None or self.window_start_price is None:
            return None
        return self.current_price - self.window_start_price

    def get_window_info(self):
        """Get full window info for debugging."""
        return {
            "window_start_price": self.window_start_price,
            "window_start_time": self.window_start_time,
            "current_price": self.current_price,
            "last_update": self.last_update,
            "delta": self.get_window_delta(),
            "connected": self._connected
        }

    def is_connected(self):
        """Check if WebSocket is connected and receiving data."""
        if not self._connected:
            return False
        # Consider stale if no update in 30 seconds
        return (time.time() - self.last_update) < 30

    def stop(self):
        """Stop the WebSocket connection."""
        self._stop_event.set()
        self._connected = False
        print("[RTDS] Stopped")


# Convenience function for compatibility
def get_rtds_price_with_age(feed):
    """Get price and age from RTDS feed (drop-in replacement for Chainlink)."""
    if feed is None:
        return None, 0
    return feed.get_price_with_age()
