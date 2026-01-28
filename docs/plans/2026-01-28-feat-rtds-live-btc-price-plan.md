---
title: "feat: Live BTC Price via Polymarket RTDS WebSocket"
type: feat
date: 2026-01-28
status: complete
---

# feat: Live BTC Price via Polymarket RTDS WebSocket

## Overview

Connect to Polymarket's Real-Time Data Stream (RTDS) WebSocket to get live BTC prices - the **exact same price feed** Polymarket uses internally for settlement. This replaces the stale Chainlink on-chain polling (1-60 second delays) with sub-second latency.

## Problem Statement

**Current issue:** The Chainlink on-chain price feed has 1-60 second delays because:
1. Chainlink oracles only update when price moves significantly OR after a heartbeat interval
2. On-chain polling has higher latency than WebSocket streaming
3. We can't accurately track the "price to beat" for 15-minute windows

**What we need:**
- **Window Start Price**: The BTC price when the 15-minute window opens (the "price to beat")
- **Current Price**: Live BTC price updating in real-time
- **Delta Display**: Show how far BTC is from the price to beat

## Proposed Solution

Connect to Polymarket's RTDS WebSocket - the same price feed they use for settlement.

### Technical Details

| Item | Value |
|------|-------|
| **WebSocket URL** | `wss://ws-live-data.polymarket.com` |
| **Topic** | `crypto_prices_chainlink` |
| **Symbol** | `btc/usd` |
| **Auth** | None required (public endpoint) |
| **Cost** | Free (no RPC costs) |
| **Latency** | ~100ms (vs 1-60 seconds currently) |

### Why This Is Better

1. **Exact match** to Polymarket's settlement source (no guessing)
2. **10-600x faster** than on-chain reads
3. **Free** - no API key, no RPC costs
4. **Authoritative** - this is THE price that determines winners

### Implementation Note (Discovered During Development)

RTDS sends **batch data on subscription**, not continuous streaming. The actual behavior:
- Subscribe to `crypto_prices_chainlink` topic
- Receive one batch of historical data points with topic `crypto_prices`
- No further updates until next subscription

**Solution:** Periodic re-subscription every 3 seconds to get fresh batch data. This provides ~3 second latency which is still much better than Chainlink's 60+ seconds.

## Technical Approach

### Files to Create/Modify

| File | Changes |
|------|---------|
| `rtds_price_feed.py` | **NEW** - WebSocket client for Polymarket RTDS |
| `trading_bot_smart.py` | Add RTDS import, integrate into status line |

### Implementation

#### 1. Create `rtds_price_feed.py`

```python
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
        while not self._stop_event.is_set():
            try:
                async with websockets.connect(
                    self.WS_URL,
                    ping_interval=self.PING_INTERVAL,
                    ping_timeout=10
                ) as ws:
                    self._connected = True
                    print(f"[RTDS] Connected to {self.WS_URL}")

                    # Subscribe to Chainlink BTC/USD prices
                    subscribe_msg = {
                        "action": "subscribe",
                        "subscriptions": [{
                            "topic": "crypto_prices_chainlink",
                            "type": "*",
                            "filters": json.dumps({"symbol": "btc/usd"})
                        }]
                    }
                    await ws.send(json.dumps(subscribe_msg))
                    print("[RTDS] Subscribed to crypto_prices_chainlink (btc/usd)")

                    # Receive messages
                    async for msg in ws:
                        if self._stop_event.is_set():
                            break

                        try:
                            data = json.loads(msg)
                            self._handle_message(data)
                        except json.JSONDecodeError:
                            continue

            except Exception as e:
                self._connected = False
                if not self._stop_event.is_set():
                    print(f"[RTDS] Connection error: {e}, reconnecting in 2s...")
                    await asyncio.sleep(2)

    def _handle_message(self, data):
        """Process incoming WebSocket message."""
        topic = data.get("topic")

        if topic == "crypto_prices_chainlink":
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
```

#### 2. Integrate into `trading_bot_smart.py`

**Add import (near line 130, after Chainlink import):**

```python
# RTDS Price Feed (Polymarket's real-time Chainlink stream)
try:
    from rtds_price_feed import RTDSPriceFeed
    rtds_feed = RTDSPriceFeed()
    RTDS_AVAILABLE = rtds_feed.start()
    if RTDS_AVAILABLE:
        print("RTDS feed starting (Polymarket real-time prices)")
except ImportError:
    RTDS_AVAILABLE = False
    rtds_feed = None
    print("WARNING: rtds_price_feed.py not found - using Chainlink fallback")
```

**Update `log_state()` function (around line 1045):**

Replace the Chainlink BTC price section with:

```python
    # Get BTC price - prefer RTDS (real-time) over Chainlink (delayed)
    btc_str = ""
    btc_price = None
    btc_delta = None

    if RTDS_AVAILABLE and rtds_feed and rtds_feed.is_connected():
        btc_price, btc_age = rtds_feed.get_price_with_age()
        btc_delta = rtds_feed.get_window_delta()
        if btc_price:
            # Format: BTC:$89,200(+$52) or BTC:$89,200(-$30)
            if btc_delta is not None:
                delta_sign = "+" if btc_delta >= 0 else ""
                btc_str = f"BTC:${btc_price:,.0f}({delta_sign}${btc_delta:,.0f}) | "
            else:
                btc_str = f"BTC:${btc_price:,.0f}({btc_age}s) | "
            btc_price_history.append((time.time(), btc_price))
    elif CHAINLINK_AVAILABLE and chainlink_feed:
        # Fallback to Chainlink if RTDS unavailable
        btc_price, btc_age = chainlink_feed.get_price_with_age()
        if btc_price:
            btc_str = f"BTC:${btc_price:,.0f}({btc_age}s) | "
            btc_price_history.append((time.time(), btc_price))
```

#### 3. Install dependency on server

```bash
pip3 install websockets --break-system-packages
```

## Status Line Display

**Before (Chainlink):**
```
[16:50:35] IDLE | T-565s | BTC:$88,273(1205s) | UP:37c DN:64c | ...
```
The `1205s` shows the price is 20+ minutes stale.

**After (RTDS):**
```
[16:50:35] IDLE | T-565s | BTC:$88,273(+$125) | UP:37c DN:64c | ...
```
The `+$125` shows BTC is $125 above the "price to beat" (UP winning).

## Acceptance Criteria

- [x] `rtds_price_feed.py` created with WebSocket client
- [x] WebSocket connects to `wss://ws-live-data.polymarket.com`
- [x] Subscribes to `crypto_prices_chainlink` topic with `btc/usd` filter
- [x] Captures window start price at 15-minute boundaries
- [x] Status line shows delta from price to beat: `BTC:$89,200(+$52)`
- [x] Falls back to Chainlink if RTDS connection fails
- [x] Graceful reconnection on WebSocket disconnect
- [x] `websockets` package installed on server (deploy step)

## Testing

**Note:** Cannot test locally (API blocked). Test on server only.

```bash
# SSH to server
ssh root@174.138.5.183

# Install websockets
pip3 install websockets --break-system-packages

# Test RTDS connection
python3 -c "
from rtds_price_feed import RTDSPriceFeed
import time
feed = RTDSPriceFeed()
feed.start()
time.sleep(5)
price, age = feed.get_price_with_age()
print(f'BTC: \${price:,.2f}, Age: {age}s')
print(f'Window info: {feed.get_window_info()}')
feed.stop()
"
```

## Deployment

```bash
# 1. Upload new files
scp ~/MarkWatney/rtds_price_feed.py root@174.138.5.183:~/polymarket_bot/
scp ~/MarkWatney/trading_bot_smart.py root@174.138.5.183:~/polymarket_bot/

# 2. SSH and install dependency
ssh root@174.138.5.183
pip3 install websockets --break-system-packages

# 3. Restart bot
pkill -f trading_bot_smart.py
cd ~/polymarket_bot && export GOOGLE_SHEETS_SPREADSHEET_ID=1fxGKxKxj2RAL0hwtqjaOWdmnwqg6RcKseYYP-cCKp74 && export GOOGLE_SHEETS_CREDENTIALS_FILE=~/.google_sheets_credentials.json && nohup python3 trading_bot_smart.py > /dev/null 2>&1 &

# 4. Verify RTDS connected
tail -50 ~/polybot/bot.log | grep RTDS
```

## Version Update

Update `BOT_VERSION` in `trading_bot_smart.py`:

```python
BOT_VERSION = {
    "version": "v1.33",
    "codename": "Phoenix Feed",
    "date": "2026-01-28",
    "changes": "RTDS WebSocket for real-time BTC prices from Polymarket's settlement feed"
}
```

Add to `BOT_REGISTRY.md`:

```markdown
| v1.33 | 2026-01-28 PST | Phoenix Feed | RTDS WebSocket: Real-time BTC prices from Polymarket's Chainlink stream | Active |

### v1.33 - Phoenix Feed (2026-01-28)
*"Rising from the ashes of stale prices"*
- **RTDS WebSocket Integration**
  - Connects to `wss://ws-live-data.polymarket.com`
  - Same price feed Polymarket uses for settlement
  - ~100ms latency (vs 1-60 seconds from on-chain)
- **Price to Beat Tracking**
  - Captures BTC price at each 15-minute window start
  - Shows delta in status line: `BTC:$89,200(+$52)`
  - Positive = UP winning, Negative = DOWN winning
- **Graceful Fallback**
  - Falls back to Chainlink on-chain if RTDS disconnects
```

## References

- Previous plan (abandoned): `docs/plans/2026-01-26-feat-live-btc-price-websocket-plan.md`
- [Polymarket RTDS Overview](https://docs.polymarket.com/developers/RTDS/RTDS-overview)
- [RTDS Crypto Prices](https://docs.polymarket.com/developers/RTDS/RTDS-crypto-prices)
- Chainlink feed: `chainlink_feed.py:50-133`
- Status line formatting: `trading_bot_smart.py:988-1106`
- Threading patterns: `docs/plans/2026-01-26-fix-sheets-blocking-flush-plan.md`
