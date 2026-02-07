#!/usr/bin/env python3
"""
POLYBOT - 99c SNIPER
====================
BTC 15-minute Up/Down markets on Polymarket.

STRATEGY: 99c Capture (single-side bet on near-certain winners)
- Places 99c bid when confidence >= 95%
- Confidence = ask_price - time_penalty
- OB-based early exit when order book reverses
- 60c hard stop (FOK market orders) as emergency exit
"""

# ===========================================
# BOT VERSION
# ===========================================
BOT_VERSION = {
    "version": "v1.57",
    "codename": "Crystal Eye",
    "date": "2026-02-06",
    "changes": "Exact fill tracking via Position API, floor FOK sell to 2dp, fix double PnL counting, fix capture_99c_exited on hard stop partial fail, cache OB analyze (was 3x/tick)."
}

import os
import sys
import signal
import time
import json
import math
import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from collections import deque

# Timezone for logging (Pacific Time)
PST = ZoneInfo("America/Los_Angeles")
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor

# Supabase logging (single source of truth - replaces Google Sheets)
try:
    from supabase_logger import (init_supabase_logger,
                                 buffer_tick as supabase_buffer_tick,
                                 maybe_flush_ticks as supabase_maybe_flush_ticks,
                                 flush_ticks as supabase_flush_ticks,
                                 buffer_activity as supabase_buffer_activity,
                                 flush_activities as supabase_flush_activities,
                                 log_event as supabase_log_event)
    SUPABASE_LOGGER_AVAILABLE = True
except ImportError:
    SUPABASE_LOGGER_AVAILABLE = False
    init_supabase_logger = lambda: False
    supabase_buffer_tick = lambda *args, **kwargs: None
    supabase_maybe_flush_ticks = lambda ttl=None: False
    supabase_flush_ticks = lambda: False
    supabase_buffer_activity = lambda *args, **kwargs: None
    supabase_flush_activities = lambda: False
    supabase_log_event = lambda *args, **kwargs: False

# Unified logging functions
def buffer_tick(*args, **kwargs):
    supabase_buffer_tick(*args, **kwargs)

def maybe_flush_ticks(ttl: float = None):
    """Flush ticks if enough time has passed.

    Args:
        ttl: Time to close (seconds). If < 60, skip flush to protect critical trading period.
    """
    supabase_maybe_flush_ticks(ttl)
    if ttl is None or ttl >= 60:
        supabase_flush_activities()

def flush_ticks():
    supabase_flush_ticks()
    supabase_flush_activities()

def log_event(event_type: str, window_id: str = "", **kwargs):
    """Log a trading event to Supabase."""
    return supabase_log_event(event_type, window_id=window_id, **kwargs)

# Setup file logging (tee to console and file)
import sys
class TeeLogger:
    def __init__(self, filename):
        self.terminal = sys.stdout
        self.log = open(filename, "a", buffering=1)  # Line buffered
    def write(self, message):
        self.terminal.write(message)
        self.log.write(message)
    def flush(self):
        self.terminal.flush()
        self.log.flush()

LOG_FILE = os.path.expanduser("~/polybot/bot.log")
sys.stdout = TeeLogger(LOG_FILE)
sys.stderr = TeeLogger(LOG_FILE)
print(f"\n{'='*60}")
print(f"POLYBOT {BOT_VERSION['codename']} ({BOT_VERSION['version']}) starting...")
print(f"Changes: {BOT_VERSION['changes']}")
print(f"Started: {datetime.now(PST).strftime('%Y-%m-%d %H:%M:%S PST')}")
print(f"Logging to: {LOG_FILE}")
print(f"{'='*60}\n")

# Load credentials
load_dotenv(os.path.expanduser("~/.env"))
PRIVATE_KEY = os.getenv("PRIVATE_KEY")

# Chainlink price feed (same source as Polymarket settlement)
try:
    from chainlink_feed import ChainlinkPriceFeed, get_btc_price_with_age
    chainlink_feed = ChainlinkPriceFeed()
    CHAINLINK_AVAILABLE = chainlink_feed.is_connected()
    if CHAINLINK_AVAILABLE:
        print(f"Chainlink feed connected: {chainlink_feed.rpc_url}")
    else:
        print("WARNING: Chainlink feed not connected - using Coinbase fallback")
except ImportError:
    CHAINLINK_AVAILABLE = False
    chainlink_feed = None
    print("WARNING: chainlink_feed.py not found - using Coinbase fallback")

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

# Order book imbalance analyzer
try:
    from orderbook_analyzer import OrderBookAnalyzer
    orderbook_analyzer = OrderBookAnalyzer(history_size=60, imbalance_threshold=0.3)
    ORDERBOOK_ANALYZER_AVAILABLE = True
    print("Order book analyzer: ENABLED")
except ImportError:
    ORDERBOOK_ANALYZER_AVAILABLE = False
    orderbook_analyzer = None
    print("WARNING: orderbook_analyzer.py not found - imbalance signals disabled")

WALLET_ADDRESS = os.getenv("WALLET_ADDRESS")

# Signal handler for clean exit
def signal_handler(sig, frame):
    print("\n\nCtrl+C pressed. Exiting...")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

# ============================================================================
# CONSTANTS
# ============================================================================

# Price constraints
MIN_PRICE = 0.01
TICK = 0.01

# ===========================================
# FAILSAFE PRICE LIMITS - NEVER VIOLATE THESE
# ===========================================
FAILSAFE_MAX_BUY_PRICE = 0.85     # NEVER buy above 85c
FAILSAFE_MIN_BUY_PRICE = 0.05     # NEVER buy below 5c (garbage)
FAILSAFE_MAX_SHARES = 50          # NEVER buy more than 50 shares at once
FAILSAFE_MAX_ORDER_COST = 10.00   # NEVER place order costing more than $10

# Timing
CLOSE_GUARD_SECONDS = 10
HARD_FLATTEN_SECONDS = 10         # Guaranteed flat by 10s pre-close

# ===========================================
# ORDER BOOK IMBALANCE SETTINGS
# ===========================================
USE_ORDERBOOK_SIGNALS = True       # Enable order book imbalance analysis
ORDERBOOK_MIN_SIGNAL_STRENGTH = "MODERATE"  # Minimum: WEAK, MODERATE, STRONG
ORDERBOOK_REQUIRE_TREND = False    # Require sustained trend confirmation
ORDERBOOK_LOG_ALWAYS = True        # Always show imbalance in status log
# ===========================================
# BUG FIXES - ORDER & POSITION HANDLING
# ===========================================
# ===========================================
# 99c EARLY EXIT (OB-BASED) - Cut losses early
# ===========================================
OB_EARLY_EXIT_ENABLED = True       # Enable/disable early exit feature
OB_EARLY_EXIT_THRESHOLD = -0.30    # Exit when sellers > 30% (OB imbalance < -0.30)

# ===========================================
# 60¢ HARD STOP - FOK Market Orders (v1.34)
# ===========================================
# Guaranteed emergency exit using Fill-or-Kill market orders.
# Triggers on BEST BID (not ask) to ensure real liquidity exists.
# Will sell at any price to avoid riding to $0.
HARD_STOP_ENABLED = True           # Enable 60¢ hard stop
HARD_STOP_TRIGGER = 0.60           # Exit when best bid <= 60¢
HARD_STOP_FLOOR = 0.01             # Effectively no floor (1¢ minimum)
HARD_STOP_USE_FOK = True           # Use Fill-or-Kill market orders

# ===========================================
# 99c BID CAPTURE STRATEGY (CONFIDENCE-BASED)
# ===========================================
CAPTURE_99C_ENABLED = True         # Enable/disable 99c capture strategy
CAPTURE_99C_MAX_SPEND = 6.00       # Max $6 per window on this strategy (6 shares @ 99c)
CAPTURE_99C_BID_PRICE = 0.99       # Place bid at 99c
CAPTURE_99C_MIN_TIME = 10          # Need at least 10 seconds to settle order
CAPTURE_99C_MIN_CONFIDENCE = 0.95  # Only bet when 95%+ confident
CAPTURE_99C_MAX_ASK = 1.01         # Allow placing bids even when ask is at 99-100c (if doesn't fill, no harm)

# Time penalties: (max_time_remaining, penalty)
# Confidence = ask_price - time_penalty
# Less time = less penalty = higher confidence
CAPTURE_99C_TIME_PENALTIES = [
    (60,   0.00),   # <1 min: no penalty (locked in)
    (120,  0.03),   # 1-2 min: -3%
    (300,  0.08),   # 2-5 min: -8%
    (9999, 0.15),   # 5+ min: -15% (very uncertain)
]

# Velocity tracking for danger score
VELOCITY_WINDOW_SECONDS = 5  # Rolling window for BTC price velocity

# 99c Entry Filters (v1.24 - based on tick data analysis)
# These filters prevent entering on volatile/spiking markets that lead to losses
ENTRY_FILTER_ENABLED = True             # Enable smart entry filtering
ENTRY_FILTER_STABLE_TICKS = 3           # Last N ticks must all be >= 97c for "stable" entry
ENTRY_FILTER_STABLE_THRESHOLD = 0.97    # Price threshold for stability check
ENTRY_FILTER_MAX_JUMP = 0.08            # Max allowed tick-to-tick jump in past 10 ticks
ENTRY_FILTER_MAX_OPP_RECENT = 0.15      # Skip if opposing side was > this in past 30 ticks
ENTRY_FILTER_HISTORY_SIZE = 30          # Number of ticks to keep for filtering

# Danger Scoring Configuration — NOW USED AS EXIT TRIGGER
DANGER_THRESHOLD = 1.5               # Exit triggers when danger >= this
DANGER_THRESHOLD_FINAL = 0.8         # Lower threshold in final 30 seconds
DANGER_WEIGHT_CONFIDENCE = 3.0       # Weight for confidence drop from peak
DANGER_WEIGHT_IMBALANCE = 0.4        # Weight for order book imbalance against us
DANGER_WEIGHT_VELOCITY = 2.0         # Weight for BTC price velocity against us
DANGER_WEIGHT_OPPONENT = 0.5         # Weight for opponent ask price strength
DANGER_WEIGHT_TIME = 0.3             # Weight for time decay in final 60s
DANGER_BTC_SAFE_MARGIN = 30          # If BTC is $30+ in our favor, suppress danger exit (thin books ≠ real danger)

# Bot identity
BOT_NAME = "MarkWatney"

# States
STATE_DONE = "DONE"

# ============================================================================
# GLOBAL STATE
# ============================================================================

trades_log = []
api_latencies = deque(maxlen=10)
# Rolling window for BTC price velocity (danger score calculation)
btc_price_history = deque(maxlen=VELOCITY_WINDOW_SECONDS)

# Rolling window for market price history (entry filter - v1.24)
# Stores (timestamp, up_ask, down_ask) tuples
market_price_history = deque(maxlen=ENTRY_FILTER_HISTORY_SIZE)
error_count = 0

# HTTP session
http_session = requests.Session()
http_session.headers.update({"User-Agent": "Mozilla/5.0"})

# CLOB client
clob_client = None

# Per-window state
window_state = None

# Session counters
session_counters = {
    "profit_pairs": 0,
    "loss_avoid_pairs": 0,
    "hard_flattens": 0,
}


# ============================================================================
# REAL-TIME ACTIVITY LOG
# ============================================================================

BOT_ID = "MARKWATNEY"
ACTIVITY_LOG_FILE = os.path.expanduser("~/activity_log.jsonl")

def log_activity(action, details=None):
    """Log activity to shared JSONL file + buffer for Supabase"""
    try:
        entry = {
            "timestamp": datetime.now().isoformat(),
            "bot": BOT_ID,
            "action": action,
            "details": details or {}
        }
        with open(ACTIVITY_LOG_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")
        # Also buffer for Supabase (non-blocking)
        window_id = window_state.get('window_id', '') if window_state else ''
        supabase_buffer_activity(action, window_id, details)
    except Exception as e:
        print(f"[{ts()}] LOG_ERROR: Failed to write activity log: {e}")

def reset_window_state(slug):
    """Initialize fresh state for a new window"""
    return {
        "window_id": slug,
        "market_id": None,
        "filled_up_shares": 0,
        "filled_down_shares": 0,
        "open_up_order_ids": [],
        "open_down_order_ids": [],
        "realized_pnl_usd": 0.0,
        "state": "active",
        "up_token": None,
        "last_order_time": 0,
        "down_token": None,
        "telegram_notified": False,
        "capture_99c_used": False,     # 99c capture: only once per window
        "capture_99c_order": None,     # 99c capture: order ID
        "capture_99c_side": None,      # 99c capture: UP or DOWN
        "capture_99c_shares": 0,       # 99c capture: shares ordered
        "capture_99c_filled_up": 0,    # 99c capture: filled UP shares
        "capture_99c_filled_down": 0,  # 99c capture: filled DOWN shares
        "capture_99c_fill_notified": False,  # 99c capture: have we shown fill notification
        "capture_99c_exited": False,         # 99c capture: whether we've early-exited this position
        "ob_negative_ticks": 0,              # 99c early exit: consecutive negative OB ticks
        "started_mid_window": False,         # True if bot started mid-window (skip trading)
        "danger_score": 0,                    # Current danger score (0.0-1.0)
        "capture_99c_peak_confidence": 0,     # Confidence at 99c fill time
    }

# 99c Sniper Daily Stats (rolling summary for Telegram notifications)
sniper_stats = {
    'wins': 0,
    'losses': 0,
    'total_pnl': 0.0,
    'trades': []  # List of {window_id, side, shares, won, pnl}
}

# Pending 99c resolutions to retry (when market hasn't settled yet)
pending_99c_resolutions = []  # List of {slug, side, shares, entry_price, timestamp}

def get_imbalance():
    """Calculate current imbalance (includes all shares)"""
    if not window_state:
        return 0
    raw_imb = window_state['filled_up_shares'] - window_state['filled_down_shares']
    if abs(raw_imb) < 0.5:
        return 0
    return raw_imb

# ============================================================================
# CLOB CLIENT SETUP
# ============================================================================

def init_clob_client(max_retries=3):
    global clob_client
    from py_clob_client.client import ClobClient

    for attempt in range(max_retries):
        try:
            clob_client = ClobClient(
                host="https://clob.polymarket.com",
                key=PRIVATE_KEY,
                chain_id=137,
                signature_type=2,
                funder=WALLET_ADDRESS
            )
            clob_client.set_api_creds(clob_client.create_or_derive_api_creds())
            return clob_client
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"Connection attempt {attempt + 1} failed, retrying in 3s...")
                time.sleep(3)
            else:
                raise e
    return clob_client

# ============================================================================
# TELEGRAM NOTIFICATIONS
# ============================================================================

TELEGRAM_CONFIG_FILE = os.path.expanduser('~/.telegram-bot.json')
telegram_config = None

def init_telegram():
    global telegram_config
    try:
        if os.path.exists(TELEGRAM_CONFIG_FILE):
            with open(TELEGRAM_CONFIG_FILE, 'r') as f:
                telegram_config = json.load(f)
            print(f"[Telegram] Bot connected")
            return True
    except Exception as e:
        print(f"[Telegram] Error: {e}")
    return False

def send_telegram(message):
    if not telegram_config:
        return
    try:
        url = f"https://api.telegram.org/bot{telegram_config['token']}/sendMessage"
        requests.post(url, data={
            "chat_id": telegram_config['chat_id'],
            "text": message,
            "parse_mode": "HTML"
        }, timeout=5)
    except:
        pass

# ============================================================================
# GAS/MATIC BALANCE MONITORING
# ============================================================================

POLYGON_RPC = os.getenv("POLYGON_RPC", "https://polygon-rpc.com")
EOA_ADDRESS = "0xa0bC1d8209B6601B0Ed99cA82a550f53FA3447F7"  # EOA that pays for gas
GAS_LOW_THRESHOLD = 0.1  # MATIC - about 4 days of gas at 47 trades/day
GAS_CRITICAL_THRESHOLD = 0.03  # MATIC - about 1 day of gas
_last_gas_alert_time = 0  # Track when we last sent an alert

def check_eoa_gas_balance():
    """Check EOA MATIC balance for gas. Returns balance in MATIC or None on error."""
    try:
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_getBalance",
            "params": [EOA_ADDRESS, "latest"],
            "id": 1
        }
        response = requests.post(POLYGON_RPC, json=payload, timeout=5)
        result = response.json().get("result")
        if result:
            balance_wei = int(result, 16)
            balance_matic = balance_wei / 1e18
            return balance_matic
    except Exception as e:
        print(f"[GAS] Failed to check balance: {e}")
    return None

def check_gas_and_alert():
    """Check gas balance and send Telegram alert if low. Called once per window."""
    global _last_gas_alert_time

    balance = check_eoa_gas_balance()
    if balance is None:
        return None

    # Calculate days remaining (0.0268 MATIC per redeem, 47 redeems/day)
    daily_cost = 47 * 0.0268
    days_remaining = balance / daily_cost if daily_cost > 0 else 999

    # Only alert once per hour to avoid spam
    now = time.time()
    alert_cooldown = 3600  # 1 hour

    if balance < GAS_CRITICAL_THRESHOLD:
        if now - _last_gas_alert_time > alert_cooldown:
            _last_gas_alert_time = now
            msg = f"""🚨🚨🚨 <b>CRITICAL: GAS EMPTY</b> 🚨🚨🚨

⛽ EOA Balance: <b>{balance:.4f} MATIC</b>
⏰ Remaining: <b>{days_remaining:.1f} days</b>

❌ AUTO-REDEEM WILL FAIL
💰 Send MATIC to:
<code>{EOA_ADDRESS}</code>

Or run: python3 send_matic.py"""
            send_telegram(msg)
            print(f"[GAS] 🚨 CRITICAL: {balance:.4f} MATIC ({days_remaining:.1f} days)")

    elif balance < GAS_LOW_THRESHOLD:
        if now - _last_gas_alert_time > alert_cooldown:
            _last_gas_alert_time = now
            msg = f"""⚠️ <b>LOW GAS WARNING</b> ⚠️

⛽ EOA Balance: <b>{balance:.4f} MATIC</b>
⏰ Remaining: ~<b>{days_remaining:.1f} days</b>

Send MATIC to:
<code>{EOA_ADDRESS}</code>"""
            send_telegram(msg)
            print(f"[GAS] ⚠️ LOW: {balance:.4f} MATIC ({days_remaining:.1f} days)")

    return balance

def notify_99c_fill(side, shares, confidence, ttc):
    """Telegram notification when 99c sniper order fills"""
    msg = f"""🎯 <b>99c SNIPER FILLED</b>
Side: {side}
Shares: {shares:.0f} @ 99c
Confidence: {confidence:.0f}%
Time left: {ttc}s
Cost: ${shares * 0.99:.2f}
Potential profit: ${shares * 0.01:.2f}"""
    send_telegram(msg)

def notify_99c_resolution(side, shares, won, pnl):
    """Telegram notification when 99c sniper window resolves"""
    global sniper_stats

    # Update stats
    if won:
        sniper_stats['wins'] += 1
        emoji = "✅"
        result = "WIN"
    else:
        sniper_stats['losses'] += 1
        emoji = "❌"
        result = "LOSS"

    sniper_stats['total_pnl'] += pnl
    sniper_stats['trades'].append({
        'side': side,
        'shares': shares,
        'won': won,
        'pnl': pnl
    })

    # Calculate stats
    total_trades = sniper_stats['wins'] + sniper_stats['losses']
    win_rate = (sniper_stats['wins'] / total_trades * 100) if total_trades > 0 else 0
    avg_trade_value = 5.00  # $5 per trade
    pnl_pct = (sniper_stats['total_pnl'] / avg_trade_value) * 100  # ROI based on $5 avg trade

    msg = f"""{emoji} <b>99c SNIPER {result}</b>
Side: {side} ({shares:.0f} shares)
P&L: ${pnl:+.2f}

<b>📊 Daily Summary</b>
Wins: {sniper_stats['wins']} | Losses: {sniper_stats['losses']}
Win Rate: {win_rate:.0f}%
Total P&L: ${sniper_stats['total_pnl']:+.2f}
ROI: {pnl_pct:+.1f}% (of $5 avg)"""
    send_telegram(msg)

def check_99c_outcome(side, slug):
    """Check if our 99c bet won by querying Polymarket API for actual settlement"""
    if not slug:
        return None

    try:
        # Query gamma API for actual market outcome
        url = f"https://gamma-api.polymarket.com/events?slug={slug}"
        resp = requests.get(url, timeout=10)
        data = resp.json()

        if not data:
            return None

        # Get markets from the event
        markets = data[0].get('markets', [])
        if not markets:
            return None

        # Find the winning outcome
        winning_side = None
        for market in markets:
            # Check if market is resolved via outcomePrices
            # API returns: outcomes=["Up", "Down"], outcomePrices=["1", "0"]
            # Price of 1.0 means that outcome won
            outcomes_str = market.get('outcomes', '[]')
            prices_str = market.get('outcomePrices', '[]')

            if isinstance(outcomes_str, str):
                outcomes_list = json.loads(outcomes_str)
            else:
                outcomes_list = outcomes_str

            if isinstance(prices_str, str):
                prices_list = json.loads(prices_str)
            else:
                prices_list = prices_str

            # Find which outcome has price = 1.0 (the winner)
            if outcomes_list and prices_list and len(outcomes_list) == len(prices_list):
                for outcome, price in zip(outcomes_list, prices_list):
                    if float(price) > 0.9:  # Winner has price ~1.0
                        winning_side = outcome.upper()
                        break

            if winning_side:
                break

        if winning_side:
            # Compare our side with winning side
            return side.upper() == winning_side

        return None  # Market not resolved yet

    except Exception as e:
        print(f"[{ts()}] check_99c_outcome error: {e}")
        return None

# ============================================================================
# DATA FETCHING
# ============================================================================

def get_current_slug():
    current = int(time.time())
    window_start = (current // 900) * 900
    return f"btc-updown-15m-{window_start}", window_start

def get_market_data(slug):
    try:
        start = time.time()
        url = f"https://gamma-api.polymarket.com/events?slug={slug}"
        resp = http_session.get(url, timeout=3)
        latency_ms = (time.time() - start) * 1000
        api_latencies.append(latency_ms)
        data = resp.json()
        return data[0] if data else None
    except Exception as e:
        print(f"[{ts()}] MARKET_DATA_ERROR: {e}")
        return None

def get_time_remaining(market):
    try:
        end_str = market.get('markets', [{}])[0].get('endDate', '')
        end_time = datetime.fromisoformat(end_str.replace('Z', '+00:00'))
        remaining = (end_time - datetime.now(timezone.utc)).total_seconds()
        if remaining < 0:
            return "ENDED", -1
        return f"{int(remaining)//60:02d}:{int(remaining)%60:02d}", remaining
    except Exception as e:
        print(f"[{ts()}] TIME_PARSE_ERROR: {e}")
        return "??:??", 0

def get_order_books(market):
    """Fetch full order books for UP and DOWN tokens"""
    try:
        clob_ids = market.get('markets', [{}])[0].get('clobTokenIds', '')
        clob_ids = clob_ids.replace('[', '').replace(']', '').replace('"', '')
        tokens = [t.strip() for t in clob_ids.split(',')]
        if len(tokens) < 2:
            return None

        def fetch_book(token_id):
            try:
                start = time.time()
                url = f"https://clob.polymarket.com/book?token_id={token_id}"
                resp = http_session.get(url, timeout=3)
                latency_ms = (time.time() - start) * 1000
                api_latencies.append(latency_ms)
                book = resp.json()
                return book.get('asks', []), book.get('bids', [])
            except Exception as e:
                print(f"[{ts()}] BOOK_FETCH_ERROR: {e}")
                return [], []

        with ThreadPoolExecutor(max_workers=2) as ex:
            up_future = ex.submit(fetch_book, tokens[0])
            down_future = ex.submit(fetch_book, tokens[1])

            up_asks, up_bids = up_future.result(timeout=3)
            down_asks, down_bids = down_future.result(timeout=3)

            return {
                'up_asks': sorted(up_asks, key=lambda x: float(x['price'])),
                'up_bids': sorted(up_bids, key=lambda x: float(x['price']), reverse=True),
                'down_asks': sorted(down_asks, key=lambda x: float(x['price'])),
                'down_bids': sorted(down_bids, key=lambda x: float(x['price']), reverse=True),
                'up_token': tokens[0],
                'down_token': tokens[1]
            }
    except Exception as e:
        print(f"[{ts()}] ORDER_BOOK_ERROR: {e}")
        return None

def get_btc_price_from_coinbase():
    """Fetch current BTC price from Coinbase for strategy signals"""
    try:
        resp = http_session.get(
            "https://api.coinbase.com/v2/prices/BTC-USD/spot",
            timeout=2
        )
        if resp.status_code == 200:
            data = resp.json()
            return float(data['data']['amount'])
    except Exception as e:
        print(f"[{ts()}] COINBASE_PRICE_ERROR: {e}")
    return None

# ============================================================================
# SHARE SIZE CALCULATION
# ============================================================================

def floor_to_tick(price):
    """Floor price to nearest tick"""
    return round(int(price / TICK) * TICK, 2)

# ============================================================================
# LOGGING
# ============================================================================

def ts():
    return datetime.now(PST).strftime("%H:%M:%S")

_last_log_time = 0
_last_skip_reason = ""

def log_state(ttc, books=None, ob_result=None):
    """Log current state every second with prices and skip reason"""
    global _last_log_time, _last_skip_reason
    if not window_state:
        return

    # Throttle to 1 second
    now = time.time()
    if (now - _last_log_time < 1):
        return
    _last_log_time = now

    up_shares = window_state['filled_up_shares']
    down_shares = window_state['filled_down_shares']
    pnl = window_state['realized_pnl_usd']

    # Get current prices
    ask_up = ask_down = 0.50
    if books:
        if books.get('up_asks'):
            ask_up = float(books['up_asks'][0]['price'])
        if books.get('down_asks'):
            ask_down = float(books['down_asks'][0]['price'])

    # Determine status and reason
    if window_state.get('capture_99c_fill_notified'):
        if window_state.get('capture_99c_exited'):
            status = "EXITED"
            reason = window_state.get('capture_99c_exit_reason', '')
        else:
            status = "SNIPER"
            reason = f"{window_state.get('capture_99c_side', '?')} {int(up_shares + down_shares)} shares"
    elif up_shares > 0 or down_shares > 0:
        status = "FILLED"
        reason = ""
    else:
        status = "IDLE"
        reason = _last_skip_reason if _last_skip_reason else "watching..."

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

    # v1.24: Track market prices for entry filter
    market_price_history.append((time.time(), ask_up, ask_down))

    # Get order book imbalance (use cached result — analyze() called once per tick in main loop)
    ob_str = ""
    up_imb = None
    down_imb = None
    if ob_result and ORDERBOOK_LOG_ALWAYS:
        up_imb = ob_result['up_imbalance']
        down_imb = ob_result['down_imbalance']
        signal = ob_result['signal']
        strength = ob_result['strength']

        # Compact imbalance display
        sig_str = signal[:6] if signal else "-"
        str_str = f"({strength[0]})" if strength else ""
        ob_str = f" | OB:{up_imb:+.2f}/{down_imb:+.2f} {sig_str}{str_str}"

    # Build danger score indicator if applicable (LOG-03)
    danger_str = ""
    if window_state.get('capture_99c_fill_notified') and not window_state.get('capture_99c_exited'):
        ds = window_state.get('danger_score', 0)
        danger_str = f" | D:{ds:.2f}"

    # Calculate 99c confidence for the leading side (always show so we can track)
    conf_str = ""
    leading_ask = max(ask_up, ask_down)
    leading_side = "UP" if ask_up > ask_down else "DN"
    conf, _ = calculate_99c_confidence(leading_ask, ttc)
    threshold = CAPTURE_99C_MIN_CONFIDENCE * 100
    conf_str = f" | {leading_side}:{conf*100:.0f}%/{threshold:.0f}%"

    price_str = f"UP:{ask_up*100:2.0f}c DN:{ask_down*100:2.0f}c"
    print(f"[{ts()}] {status:7} | T-{ttc:3.0f}s | {btc_str}{price_str}{conf_str}{ob_str}{danger_str} | pos:{up_shares:.0f}/{down_shares:.0f} | {reason}")

    # Get danger score if holding 99c position (LOG-01)
    danger_for_log = None
    if window_state.get('capture_99c_fill_notified') and not window_state.get('capture_99c_exited'):
        danger_for_log = window_state.get('danger_score')

    # Buffer tick for Google Sheets (batched upload)
    buffer_tick(
        window_state.get('window_id', ''),
        ttc, status, ask_up, ask_down, up_shares, down_shares,
        btc_price=btc_price, up_imb=up_imb, down_imb=down_imb,
        danger_score=danger_for_log, reason=reason
    )
    # Pass TTL to skip flush during critical trading period (final 60 seconds)
    maybe_flush_ticks(ttc)

def log_order_event(order_id, side, action, price, qty, filled, avg_fill, reason=""):
    oid = order_id[:16] if order_id else "None"
    print(f"[{ts()}] ORDER_EVENT order_id={oid}... side={side} action={action} px={price:.2f} qty={qty} filled={filled} reason={reason}")

# ============================================================================
# ORDER MANAGEMENT
# ============================================================================

def place_limit_order(token_id, price, size, side="BUY", bypass_price_failsafe=False):
    """Place a post-only limit order with FAILSAFE checks"""

    if side == "BUY":
        if price > FAILSAFE_MAX_BUY_PRICE and not bypass_price_failsafe:
            print(f"[FAILSAFE] BLOCKED: BUY @ {price*100:.0f}c > {FAILSAFE_MAX_BUY_PRICE*100:.0f}c max")
            return False, "FAILSAFE: price too high"
        if price < FAILSAFE_MIN_BUY_PRICE:
            print(f"[FAILSAFE] BLOCKED: BUY @ {price*100:.0f}c < {FAILSAFE_MIN_BUY_PRICE*100:.0f}c min")
            return False, "FAILSAFE: price too low"

    if size > FAILSAFE_MAX_SHARES:
        print(f"[FAILSAFE] BLOCKED: {size} shares > {FAILSAFE_MAX_SHARES} max")
        return False, "FAILSAFE: too many shares"

    order_cost = price * size
    if order_cost > FAILSAFE_MAX_ORDER_COST:
        print(f"[FAILSAFE] BLOCKED: ${order_cost:.2f} > ${FAILSAFE_MAX_ORDER_COST:.2f} max")
        return False, "FAILSAFE: order cost too high"

    try:
        from py_clob_client.clob_types import OrderArgs
        from py_clob_client.order_builder.constants import BUY, SELL

        order_side = BUY if side == "BUY" else SELL

        result = clob_client.create_and_post_order(
            OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=order_side,
            )
        )
        order_id = result.get('orderID', str(result))
        log_activity("ORDER_PLACED", {"order_id": order_id, "side": side, "price": price, "size": size})
        return True, order_id
    except Exception as e:
        log_activity("ORDER_FAILED", {"side": side, "price": price, "size": size, "error": str(e)})
        return False, str(e)

# ============================================================================
# HARD STOP - FOK MARKET ORDERS (v1.34)
# ============================================================================

def place_fok_market_sell(token_id: str, shares: float) -> tuple:
    """
    Place a Fill-or-Kill market sell order for guaranteed execution.

    Args:
        token_id: The token to sell
        shares: Number of shares to sell

    Returns:
        tuple: (success, order_id, filled_shares)
    """
    global clob_client

    try:
        from py_clob_client.clob_types import MarketOrderArgs, OrderType
        from py_clob_client.order_builder.constants import SELL

        # Floor to 2 decimal places (CLOB size precision) to never sell more than we have
        safe_shares = math.floor(shares * 100) / 100
        if safe_shares != shares:
            print(f"[{ts()}] FOK_FLOOR: {shares} → {safe_shares} (floored to 2dp)")
        print(f"[{ts()}] HARD_STOP: Placing FOK market sell: {safe_shares} shares")

        # Create market sell order
        sell_args = MarketOrderArgs(
            token_id=token_id,
            amount=safe_shares,
            side=SELL
        )

        # Sign and post with FOK (Fill-or-Kill)
        signed_order = clob_client.create_market_order(sell_args)
        response = clob_client.post_order(signed_order, orderType=OrderType.FOK)

        # Parse response
        order_id = response.get("orderID", "unknown")
        status = response.get("status", "UNKNOWN")
        filled = float(response.get("filledAmount", 0))

        if status.upper() == "MATCHED" or filled > 0:
            print(f"[{ts()}] HARD_STOP: FOK filled {filled}/{shares} shares, order_id={order_id[:8]}...")
            log_activity("FOK_FILLED", {"order_id": order_id, "filled": filled, "requested": shares})
            return True, order_id, filled
        else:
            print(f"[{ts()}] HARD_STOP: FOK rejected, status={status}")
            log_activity("FOK_REJECTED", {"order_id": order_id, "status": status})
            return False, order_id, 0.0

    except Exception as e:
        error_msg = str(e).lower()
        print(f"[{ts()}] HARD_STOP_ERROR: FOK order failed: {e}")
        log_activity("FOK_ERROR", {"error": str(e)})
        # "not enough balance" means shares were already sold (prior fill succeeded)
        if "not enough balance" in error_msg:
            return True, "", 0.0  # Signal success so retry loop stops
        # "allowance" is a token approval issue — NOT the same as shares gone. Keep retrying.
        if "allowance" in error_msg:
            print(f"[{ts()}] HARD_STOP: Token allowance issue (not balance) — will retry")
            return False, "", 0.0
        return False, "", 0.0


def check_hard_stop_trigger(books: dict, side: str) -> tuple:
    """
    Check if hard stop should trigger based on best bid.

    Args:
        books: Order book data with up_bids/down_bids
        side: "UP" or "DOWN" - our position side

    Returns:
        tuple: (should_trigger, best_bid_price)
    """
    if not HARD_STOP_ENABLED:
        return False, 0.0

    try:
        # Get bids for our side
        bids_key = f'{side.lower()}_bids'
        bids = books.get(bids_key, [])

        # No bids = CRITICAL: market has zero liquidity, trigger emergency exit
        # The FOK retry loop in execute_hard_stop handles "no bids" with refresh + retry
        if not bids or len(bids) == 0:
            print(f"[{ts()}] HARD_STOP: NO BIDS for {side} — zero liquidity!")
            return True, 0.0

        best_bid = float(bids[0]['price'])
        best_bid_size = float(bids[0].get('size', 0))

        # Must have actual size, not just phantom price
        if best_bid_size <= 0:
            return False, 0.0

        # Trigger if best bid at or below threshold
        if best_bid <= HARD_STOP_TRIGGER:
            print(f"[{ts()}] HARD_STOP: TRIGGER CONDITION MET: best_bid={best_bid*100:.0f}c <= {HARD_STOP_TRIGGER*100:.0f}c")
            return True, best_bid

        return False, best_bid

    except Exception as e:
        print(f"[{ts()}] HARD_STOP_ERROR: Error checking trigger: {e}")
        return False, 0.0


def execute_hard_stop(side: str, books: dict, market_data=None) -> tuple:
    """
    Execute emergency hard stop using FOK market orders.
    Keeps selling until position is completely flat.

    Args:
        side: "UP" or "DOWN" - which side we're liquidating
        books: Order book data
        market_data: Market data for refreshing books (optional)

    Returns:
        tuple: (success, total_pnl)
    """
    global window_state

    # Get position: use tracked shares directly to avoid 5s API timeout blocking exits
    tracked_shares = window_state.get(f'capture_99c_filled_{side.lower()}', 0)
    shares = tracked_shares

    if shares <= 0:
        print(f"[{ts()}] HARD_STOP: No shares to sell for {side}")
        return False, 0.0

    token = window_state.get(f'{side.lower()}_token')
    entry_price = window_state.get('capture_99c_fill_price', 0.99)

    remaining_shares = shares
    total_pnl = 0.0
    best_bid = 0.0  # Initialize to avoid UnboundLocalError if no bids found
    attempts = 0
    max_attempts = 10  # Safety limit

    print()
    print("=" * 50)
    print(f"{'='*15} HARD STOP TRIGGERED {'='*15}")
    print(f"Side: {side}")
    print(f"Shares: {shares}")
    print(f"Entry Price: {entry_price*100:.0f}c")
    print("=" * 50)

    while remaining_shares > 0 and attempts < max_attempts:
        attempts += 1

        # Get current best bid
        bids_key = f'{side.lower()}_bids'
        bids = books.get(bids_key, [])

        if not bids or len(bids) == 0:
            print(f"[{ts()}] HARD_STOP: No bids available, waiting 1s (attempt {attempts})")
            time.sleep(1)
            # Refresh order books using passed market data
            if market_data:
                refreshed = get_order_books(market_data)
                if refreshed:
                    books = refreshed
            continue

        best_bid = float(bids[0]['price'])
        best_bid_size = float(bids[0].get('size', 0))

        if best_bid_size <= 0:
            print(f"[{ts()}] HARD_STOP: No bid size at {best_bid*100:.0f}c, waiting 1s")
            time.sleep(1)
            continue

        # Log if below floor (but still sell)
        if best_bid < HARD_STOP_FLOOR:
            print(f"[{ts()}] HARD_STOP: Best bid {best_bid*100:.0f}c below floor {HARD_STOP_FLOOR*100:.0f}c, selling anyway")

        # Place FOK market sell for remaining shares
        success, order_id, filled = place_fok_market_sell(token, remaining_shares)

        if success and filled > 0:
            # Calculate P&L for this fill
            fill_pnl = (best_bid - entry_price) * filled
            total_pnl += fill_pnl
            remaining_shares -= filled
            print(f"[{ts()}] HARD_STOP: Filled {filled:.0f} @ ~{best_bid*100:.0f}c, P&L: ${fill_pnl:.2f}, remaining: {remaining_shares:.0f}")
        elif success and filled == 0:
            # "not enough balance" — shares already sold by a prior fill
            print(f"[{ts()}] HARD_STOP: Shares already sold (balance exhausted), treating as complete")
            remaining_shares = 0
            break
        else:
            print(f"[{ts()}] HARD_STOP: FOK rejected, trying again (attempt {attempts})")
            time.sleep(0.5)
            # Refresh books before retry
            if market_data:
                refreshed = get_order_books(market_data)
                if refreshed:
                    books = refreshed

    if remaining_shares > 0:
        print(f"[{ts()}] HARD_STOP_ERROR: Failed to fully liquidate! {remaining_shares:.0f} shares stuck")
        # CRITICAL: Mark exited even on partial failure to prevent infinite re-triggers
        window_state['capture_99c_exited'] = True
        window_state['capture_99c_exit_reason'] = 'hard_stop_partial_fail'
        window_state[f'capture_99c_filled_{side.lower()}'] = remaining_shares
        window_state[f'filled_{side.lower()}_shares'] = remaining_shares
        window_state['realized_pnl_usd'] = window_state.get('realized_pnl_usd', 0.0) + total_pnl
        return False, total_pnl

    # Full liquidation successful
    print()
    print("=" * 50)
    print(f"{'='*15} HARD STOP COMPLETE {'='*15}")
    print(f"Total P&L: ${total_pnl:.2f}")
    print("=" * 50)

    # CRITICAL: Update state FIRST — before log_event/telegram which can throw
    # Zero BOTH tracking fields to prevent DUAL_VERIFY re-inflation
    window_state['capture_99c_exited'] = True
    window_state['capture_99c_exit_reason'] = 'hard_stop_60c'
    window_state[f'capture_99c_filled_{side.lower()}'] = 0
    window_state[f'filled_{side.lower()}_shares'] = 0
    window_state['realized_pnl_usd'] = window_state.get('realized_pnl_usd', 0.0) + total_pnl

    # Log to Sheets (non-critical — state already updated above)
    try:
        log_event("HARD_STOP_EXIT", window_state.get('window_id', ''),
                        side=side, shares=shares, price=best_bid,
                        pnl=total_pnl, reason="hard_stop_60c",
                        details=f"trigger={HARD_STOP_TRIGGER*100:.0f}c")
    except Exception as e:
        print(f"[{ts()}] HARD_STOP: log_event failed (non-critical): {e}")

    # Telegram notification (non-critical)
    msg = f"""{'='*20}
<b>HARD STOP EXIT</b>
{'='*20}
Side: {side}
Shares: {shares:.0f}
Exit: ~{best_bid*100:.0f}c
Entry: {entry_price*100:.0f}c
P&L: ${total_pnl:.2f}
<i>FOK market orders - guaranteed exit</i>"""
    send_telegram(msg)

    return True, total_pnl


def cancel_order(order_id):
    try:
        clob_client.cancel(order_id)
        return True
    except Exception as e:
        print(f"[{ts()}] CANCEL_ORDER_ERROR: {order_id[:8]}... - {e}")
        return False

def cancel_all_orders():
    try:
        clob_client.cancel_all()
        return True
    except Exception as e:
        print(f"[{ts()}] CANCEL_ALL_ERROR: {e}")
        return False

def get_order_status(order_id):
    try:
        order = clob_client.get_order(order_id)
        if order:
            size_matched = float(order.get('size_matched', 0))
            original_size = float(order.get('original_size', 0))
            return {
                'filled': size_matched,
                'original': original_size,
                'is_filled': size_matched > 0,
                'fully_filled': size_matched >= original_size,
                'price': float(order.get('price', 0)),
                'status': order.get('status', 'UNKNOWN')
            }
    except Exception as e:
        print(f"[{ts()}] ORDER_STATUS_ERROR: {order_id[:8]}... - {e}")
    return {'filled': 0, 'original': 0, 'is_filled': False, 'fully_filled': False, 'price': 0, 'status': 'ERROR'}

# ============================================================================
# POSITION VERIFICATION
# ============================================================================

def verify_position_from_api():
    """Verify actual position from API before placing orders"""
    try:
        url = f"https://data-api.polymarket.com/positions?user={WALLET_ADDRESS.lower()}"
        resp = requests.get(url, timeout=5)
        positions = resp.json()

        up_shares = 0
        down_shares = 0

        up_token = window_state.get('up_token')
        down_token = window_state.get('down_token')

        if not up_token or not down_token:
            return None

        for pos in positions:
            asset = pos.get('asset', '')
            size = float(pos.get('size', 0))
            if size > 0:
                if asset == up_token:
                    up_shares = size
                elif asset == down_token:
                    down_shares = size

        return up_shares, down_shares
    except Exception as e:
        print(f"[{ts()}] API_POSITION_ERROR: {e}")
        return None

# ============================================================================
# BUG FIX: ORDER DEDUPLICATION
# ============================================================================

def has_pending_order(side):
    """Check if there's already a pending order for this side - Bug Fix #4"""
    try:
        orders = clob_client.get_orders()
        target_token = window_state.get('up_token') if side == "UP" else window_state.get('down_token')
        if not target_token:
            return False, None

        for order in orders:
            if order.get('status') == 'LIVE':
                asset = order.get('asset_id', '')
                if asset == target_token:
                    return True, order.get('order_id', 'unknown')
    except Exception as e:
        print(f"[{ts()}] PENDING_ORDER_CHECK_ERROR: {e}")
    return False, None

# ============================================================================
# BUG FIX: ORDER VERIFICATION
# ============================================================================

def place_and_verify_order(token_id, price, size, side="BUY", bypass_price_failsafe=False):
    """Place order and verify it exists on exchange - Bug Fix #1"""
    # Check for duplicate first
    order_side = "UP" if token_id == window_state.get('up_token') else "DOWN"
    pending, pending_id = has_pending_order(order_side)
    if pending:
        print(f"[{ts()}] SKIP_DUPLICATE: Already have pending {order_side} order {pending_id}")
        return False, None, "DUPLICATE"

    # Place the order
    success, result = place_limit_order(token_id, price, size, side, bypass_price_failsafe)

    if not success:
        # result contains the error message
        error_msg = str(result) if result else "UNKNOWN_ERROR"
        if "FAILSAFE" in error_msg:
            return False, None, "FAILSAFE_BLOCKED"
        else:
            print(f"[{ts()}] ORDER_ERROR: {error_msg}")
            return False, None, f"API_ERROR: {error_msg[:50]}"

    order_id = result

    # Verify order exists on exchange
    time.sleep(0.5)
    status = get_order_status(order_id)

    if status['status'] == 'UNKNOWN' or status['status'] == 'ERROR':
        # Order may not have propagated - retry check
        time.sleep(1.0)
        status = get_order_status(order_id)

    if status['original'] > 0:
        return True, order_id, "PLACED"
    else:
        print(f"[{ts()}] ORDER_REJECTED: {order_id} - status={status['status']}")
        return False, order_id, "REJECTED"

def execute_99c_early_exit(side: str, trigger_value: float, books: dict, reason: str = "ob_reversal", market_data=None) -> bool:
    """Exit 99c position early due to OB reversal using FOK market orders.

    Triggered when:
    - OB imbalance is negative for 3 consecutive ticks (reason="ob_reversal")

    Uses FOK (Fill-or-Kill) market orders for guaranteed atomic execution.
    If FOK rejects, escalates immediately to execute_hard_stop() which has
    a 10-attempt retry loop with book refresh — guaranteed exit.

    Args:
        side: "UP" or "DOWN" - which side we bet on
        trigger_value: OB imbalance value
        books: Order book data
        reason: "ob_reversal" (price_stop is deprecated, use hard_stop)
        market_data: Market data for refreshing books in hard stop escalation

    Returns:
        bool: True if exit was successful (order filled)
    """
    global window_state

    # === HARD STOP FALLBACK (v1.34) ===
    # If hard stop conditions are met, escalate to FOK market orders
    if HARD_STOP_ENABLED:
        should_trigger, best_bid = check_hard_stop_trigger(books, side)
        if should_trigger:
            print(f"[{ts()}] EARLY_EXIT: Escalating to HARD STOP (best_bid={best_bid*100:.0f}c)")
            success, pnl = execute_hard_stop(side, books, market_data=market_data)
            return success

    # Get position: use tracked shares directly to avoid 5s API timeout blocking exits
    tracked_shares = window_state.get(f'capture_99c_filled_{side.lower()}', 0)
    shares = tracked_shares

    if shares <= 0:
        print(f"[{ts()}] EARLY_EXIT: No shares to sell for {side}")
        return False

    # Get best bid for P&L estimation
    bids = books.get(f'{side.lower()}_bids', [])
    if not bids:
        print(f"[{ts()}] NO_BIDS: Escalating to HARD STOP for {side}")
        success, pnl = execute_hard_stop(side, books, market_data=market_data)
        return success

    best_bid = float(bids[0]['price'])

    # Floor check: escalate to hard stop (which sells at any price) instead of aborting
    if best_bid < HARD_STOP_FLOOR:
        print(f"[{ts()}] OB EXIT: Bid {best_bid*100:.0f}c below floor, escalating to HARD STOP")
        success, pnl = execute_hard_stop(side, books, market_data=market_data)
        if not window_state.get('capture_99c_exited'):
            window_state['capture_99c_exited'] = True
            window_state['capture_99c_exit_reason'] = reason
        return success

    token = window_state.get(f'{side.lower()}_token')
    entry_price = window_state.get('capture_99c_fill_price', 0.99)

    print()
    print("🚨" * 20)
    print(f"🚨 99c OB EXIT TRIGGERED")
    print(f"🚨 Selling {shares:.0f} {side} shares (FOK market sell)")
    print(f"🚨 OB Reading: {trigger_value:+.2f}")
    print("🚨" * 20)

    # Place FOK market sell — atomic: fills completely or not at all
    success, order_id, filled = place_fok_market_sell(token, shares)

    # FOK rejected → escalate to hard stop (10-attempt retry loop, guaranteed exit)
    if not success:
        print(f"[{ts()}] OB EXIT: FOK rejected, escalating to HARD STOP")
        hs_success, hs_pnl = execute_hard_stop(side, books, market_data=market_data)
        if not window_state.get('capture_99c_exited'):
            window_state['capture_99c_exited'] = True
            window_state['capture_99c_exit_reason'] = reason
        # PnL already written by execute_hard_stop() — do NOT add again
        return hs_success

    # "not enough balance" → shares already sold by another exit
    if filled == 0:
        print(f"[{ts()}] OB EXIT: Shares already sold (balance exhausted)")
        window_state['capture_99c_exited'] = True
        window_state['capture_99c_exit_reason'] = reason
        window_state[f'capture_99c_filled_{side.lower()}'] = 0
        window_state[f'filled_{side.lower()}_shares'] = 0
        return True

    # Calculate P&L (use best_bid as approximate exit price)
    pnl = (best_bid - entry_price) * filled

    print(f"[{ts()}] OB EXIT: Sold {filled:.0f} @ ~{best_bid*100:.0f}c (entry {entry_price*100:.0f}c)")
    print(f"[{ts()}] OB EXIT: P&L = ${pnl:.2f}")

    # CRITICAL: Update state FIRST — before log_event/telegram which can throw
    window_state['capture_99c_exited'] = True
    window_state['capture_99c_exit_reason'] = reason
    window_state[f'capture_99c_filled_{side.lower()}'] = 0
    window_state[f'filled_{side.lower()}_shares'] = 0
    window_state['realized_pnl_usd'] = window_state.get('realized_pnl_usd', 0.0) + pnl

    # Log to Sheets (non-critical — state already updated above)
    try:
        log_event("99C_EARLY_EXIT", window_state.get('window_id', ''),
                        side=side, shares=filled, price=best_bid,
                        pnl=pnl, reason=reason, details=f"OB={trigger_value:.2f}")
    except Exception as e:
        print(f"[{ts()}] OB EXIT: log_event failed (non-critical): {e}")

    # Telegram notification (non-critical)
    try:
        msg = f"""🚨 <b>99c EARLY EXIT</b>
Side: {side}
Shares: {filled:.0f}
Exit Price: ~{best_bid*100:.0f}c
Entry Price: {entry_price*100:.0f}c
OB Reading: {trigger_value:+.2f}
P&L: ${pnl:.2f}
<i>FOK market sell — guaranteed exit</i>"""
        send_telegram(msg)
    except Exception as e:
        print(f"[{ts()}] OB EXIT: send_telegram failed (non-critical): {e}")

    return True


# ============================================================================
# 99c BID CAPTURE STRATEGY
# ============================================================================

def calculate_99c_confidence(ask_price, time_remaining):
    """
    Calculate confidence score for 99c capture.
    Confidence = ask_price - time_penalty

    Returns (confidence, time_penalty)
    """
    base_confidence = ask_price

    # Get time penalty (default to highest for safety)
    time_penalty = 0.15
    for max_time, penalty in CAPTURE_99C_TIME_PENALTIES:
        if time_remaining <= max_time:
            time_penalty = penalty
            break

    confidence = base_confidence - time_penalty
    return confidence, time_penalty


def check_99c_entry_filter(side: str) -> tuple[bool, str]:
    """
    Check if it's safe to enter a 99c position based on price history.

    Filters based on analysis of historical losses:
    1. STABLE: Last 3 ticks all >= 97c (sustained confidence, not spike)
    2. LOW VOLATILITY: No tick-to-tick jump > 8c in past 10 ticks
    3. OPPOSING LOW: Opposing side never > 15c in past 30 ticks

    Returns (safe_to_enter, reason)
    """
    global market_price_history

    if not ENTRY_FILTER_ENABLED:
        return True, "filter_disabled"

    if len(market_price_history) < 10:
        return True, "insufficient_history"

    # Extract price lists
    history = list(market_price_history)
    if side == "UP":
        our_prices = [h[1] for h in history]  # up_ask
        opp_prices = [h[2] for h in history]  # down_ask
    else:
        our_prices = [h[2] for h in history]  # down_ask
        opp_prices = [h[1] for h in history]  # up_ask

    # FILTER 1: Stability check - last N ticks all >= 97c
    stable_prices = our_prices[-ENTRY_FILTER_STABLE_TICKS:]
    is_stable = all(p >= ENTRY_FILTER_STABLE_THRESHOLD for p in stable_prices)

    # FILTER 2: Low volatility - max jump in past 10 ticks
    recent_prices = our_prices[-10:]
    max_jump = 0
    for i in range(1, len(recent_prices)):
        jump = abs(recent_prices[i] - recent_prices[i-1])
        if jump > max_jump:
            max_jump = jump
    is_low_volatility = max_jump <= ENTRY_FILTER_MAX_JUMP

    # FILTER 3: Opposing side was low recently
    max_opp_recent = max(opp_prices) if opp_prices else 0
    is_opp_low = max_opp_recent <= ENTRY_FILTER_MAX_OPP_RECENT

    # Entry is safe if: (stable at 97c+) OR (low volatility AND opposing low)
    # This matches the pattern from our analysis
    if is_stable:
        return True, "stable_at_97c"

    if is_low_volatility and is_opp_low:
        return True, "low_volatility"

    # Build rejection reason
    reasons = []
    if not is_stable:
        reasons.append(f"unstable({stable_prices[-1]*100:.0f}c)")
    if not is_low_volatility:
        reasons.append(f"volatile(jump={max_jump*100:.0f}c)")
    if not is_opp_low:
        reasons.append(f"opp_high({max_opp_recent*100:.0f}c)")

    return False, "|".join(reasons)


def check_99c_capture_opportunity(ask_up, ask_down, ttc):
    """
    Check if we should try to capture a 99c winner using confidence score.
    Returns {'side': 'UP'/'DOWN', 'ask': X, 'confidence': Y, 'penalty': Z} or None.

    Confidence = ask_price - time_penalty
    Only trigger when confidence >= CAPTURE_99C_MIN_CONFIDENCE (95%)
    """
    global window_state

    # Already used this window?
    if window_state.get('capture_99c_used'):
        return None

    # Need at least 10 seconds to settle order
    if ttc < CAPTURE_99C_MIN_TIME:
        return None

    # Check UP side
    conf_up, penalty_up = calculate_99c_confidence(ask_up, ttc)
    if conf_up >= CAPTURE_99C_MIN_CONFIDENCE:
        # Don't enter if ask is too high - filling at 99c would mean catching a reversal
        if ask_up >= CAPTURE_99C_MAX_ASK:
            return None
        # v1.24: Apply entry filter to avoid volatile/spiking entries
        safe, filter_reason = check_99c_entry_filter("UP")
        if not safe:
            print(f"[{ts()}] 99c ENTRY FILTER: Skipping UP (conf={conf_up*100:.0f}%) - {filter_reason}")
            return None
        return {'side': 'UP', 'ask': ask_up, 'confidence': conf_up, 'penalty': penalty_up, 'filter_reason': filter_reason}

    # Check DOWN side
    conf_down, penalty_down = calculate_99c_confidence(ask_down, ttc)
    if conf_down >= CAPTURE_99C_MIN_CONFIDENCE:
        # Don't enter if ask is too high - filling at 99c would mean catching a reversal
        if ask_down >= CAPTURE_99C_MAX_ASK:
            return None
        # v1.24: Apply entry filter to avoid volatile/spiking entries
        safe, filter_reason = check_99c_entry_filter("DOWN")
        if not safe:
            print(f"[{ts()}] 99c ENTRY FILTER: Skipping DOWN (conf={conf_down*100:.0f}%) - {filter_reason}")
            return None
        return {'side': 'DOWN', 'ask': ask_down, 'confidence': conf_down, 'penalty': penalty_down, 'filter_reason': filter_reason}

    return None


def execute_99c_capture(side, current_ask, confidence, penalty, ttc):
    """
    Place a $5 order at 99c for the likely winner.
    This is a single-side bet on near-certain winners.
    """
    global window_state

    shares = int(CAPTURE_99C_MAX_SPEND / CAPTURE_99C_BID_PRICE)  # ~5 shares
    token = window_state['up_token'] if side == 'UP' else window_state['down_token']

    # LOCK IMMEDIATELY: Prevent retry loops if API fails or is slow.
    # Better to miss one window than place 5 duplicate orders.
    window_state['capture_99c_used'] = True

    print()
    print(f"┌─────────────── 99c CAPTURE ───────────────┐")
    print(f"│  {side} @ {current_ask*100:.0f}c | T-{ttc:.0f}s | Confidence: {confidence*100:.0f}%".ljust(44) + "│")
    print(f"│  (base {current_ask*100:.0f}% - {penalty*100:.0f}% time penalty)".ljust(44) + "│")
    print(f"│  Bidding {shares} shares @ 99c = ${shares * CAPTURE_99C_BID_PRICE:.2f}".ljust(44) + "│")
    print(f"└────────────────────────────────────────────┘")

    # Bypass price failsafe - 99c capture is intentionally above 85c limit
    success, order_id, status = place_and_verify_order(
        token, CAPTURE_99C_BID_PRICE, shares, "BUY", bypass_price_failsafe=True
    )

    if success:
        window_state['capture_99c_order'] = order_id
        window_state['capture_99c_side'] = side
        window_state['capture_99c_shares'] = shares
        # NOTE: Do NOT set capture_99c_filled_up/down here — wait for fill detection.
        print(f"🔭 99c CAPTURE: Order placed, watching for fill... (${shares * 0.01:.2f} potential profit)")
        print()
        log_event("CAPTURE_99C", window_state.get('window_id', ''),
                        side=side, price=CAPTURE_99C_BID_PRICE, shares=shares,
                        confidence=confidence, penalty=penalty, ttl=ttc)
        return True
    else:
        print(f"🎰 99c CAPTURE: ❌ Failed - {status} (locked for this window)")
        print()
        return False


def get_price_velocity(btc_price_history: deque, bet_side: str) -> float:
    """
    Calculate BTC price velocity over the rolling window.
    Returns fractional price change against our position (positive = dangerous).

    For UP position: falling BTC = positive (dangerous)
    For DOWN position: rising BTC = positive (dangerous)
    """
    if len(btc_price_history) < 2:
        return 0.0

    oldest_ts, oldest_price = btc_price_history[0]
    newest_ts, newest_price = btc_price_history[-1]

    if oldest_price == 0:
        return 0.0

    # Fractional change: (new - old) / old
    price_change = (newest_price - oldest_price) / oldest_price

    # For UP: falling price is bad, so negate (falling = negative change -> positive danger)
    # For DOWN: rising price is bad, so keep as-is (rising = positive change -> positive danger)
    if bet_side == "UP":
        return -price_change
    else:
        return price_change


def calculate_danger_score(
    current_confidence: float,
    peak_confidence: float,
    our_imbalance: float,
    btc_price_history: deque,
    opponent_ask: float,
    time_remaining: float,
    bet_side: str
) -> dict:
    """
    Calculate danger score for 99c capture position.

    Formula:
    danger_score = (
        3.0 * (peak_confidence - current_confidence) +      # Confidence drop
        0.4 * max(-our_imbalance - 0.5, 0) +                # Order book selling pressure
        2.0 * max(price_velocity_against_us, 0) +           # BTC moving against us
        0.5 * max(opponent_ask - 0.20, 0) +                 # Opponent strength
        0.3 * max(1 - ttl/60, 0)                            # Time decay in final 60s
    )

    Returns dict with 'score' and individual signal components for logging.
    """
    # Signal 1: Confidence drop from peak (always >= 0)
    confidence_drop = max(peak_confidence - current_confidence, 0)
    conf_component = DANGER_WEIGHT_CONFIDENCE * confidence_drop

    # Signal 2: Order book imbalance against us
    # Negative imbalance = selling pressure. Only count if heavily negative (< -0.5)
    imb_signal = max(-our_imbalance - 0.5, 0)
    imb_component = DANGER_WEIGHT_IMBALANCE * imb_signal

    # Signal 3: BTC price velocity against our position
    velocity = get_price_velocity(btc_price_history, bet_side)
    velocity_component = DANGER_WEIGHT_VELOCITY * max(velocity, 0)

    # Signal 4: Opponent ask strength (only counts if > 20c)
    opp_signal = max(opponent_ask - 0.20, 0)
    opp_component = DANGER_WEIGHT_OPPONENT * opp_signal

    # Signal 5: Time decay in final 60 seconds (0 outside, ramps 0->1 as ttl goes 60->0)
    time_signal = max(1 - time_remaining / 60, 0) if time_remaining < 60 else 0
    time_component = DANGER_WEIGHT_TIME * time_signal

    # Total danger score (not capped - values > 1.0 mean "very dangerous")
    total = conf_component + imb_component + velocity_component + opp_component + time_component

    return {
        'score': total,
        'confidence_drop': confidence_drop,
        'confidence_component': conf_component,
        'imbalance': our_imbalance,
        'imbalance_component': imb_component,
        'velocity': velocity,
        'velocity_component': velocity_component,
        'opponent_ask': opponent_ask,
        'opponent_component': opp_component,
        'time_remaining': time_remaining,
        'time_component': time_component,
    }


# ============================================================================
# ============================================================================
# TRADE LOGGING
# ============================================================================

def save_trades():
    try:
        with open("trades_smart.json", "w") as f:
            json.dump(trades_log, f, indent=2, default=str)
    except Exception as e:
        print(f"[{ts()}] SAVE_TRADES_ERROR: {e}")

# ============================================================================
# MAIN BOT
# ============================================================================

def main():
    global window_state, trades_log, error_count, clob_client

    print("=" * 100)
    print(f"MARKWATNEY POLYBOT {BOT_VERSION['version']} '{BOT_VERSION['codename']}'")
    print("=" * 100)
    print()

    print("Initializing trading client...")
    try:
        init_clob_client()
        print(f"✅ Connected as: {clob_client.get_address()}")
    except Exception as e:
        print(f"❌ Failed to initialize: {e}")
        return

    print("STARTUP SAFETY: Cancelling any open orders...")
    try:
        cancel_all_orders()
        print("✅ All open orders cancelled")
    except Exception as e:
        print(f"⚠️ Cancel failed: {e}")

    print("Initializing Telegram...")
    init_telegram()

    print("Initializing Supabase logger...")
    if SUPABASE_LOGGER_AVAILABLE:
        if init_supabase_logger():
            print("  Supabase logger: ENABLED")
        else:
            print("  Supabase logger: DISABLED (connection failed)")
    else:
        print("  Supabase logger: DISABLED (module not found)")
    print()

    print("STRATEGY: 99c SNIPER")
    print(f"  - Confidence-based 99c capture on near-certain winners")
    print(f"  - Order book signals: {'ENABLED' if USE_ORDERBOOK_SIGNALS and ORDERBOOK_ANALYZER_AVAILABLE else 'DISABLED'}")
    if USE_ORDERBOOK_SIGNALS and ORDERBOOK_ANALYZER_AVAILABLE:
        print(f"  - Min signal strength: {ORDERBOOK_MIN_SIGNAL_STRENGTH}")
        print(f"  - Require trend: {ORDERBOOK_REQUIRE_TREND}")
    print()
    print("FAILSAFES:")
    print(f"  - Max buy price: {FAILSAFE_MAX_BUY_PRICE*100:.0f}c")
    print(f"  - Max shares: {FAILSAFE_MAX_SHARES}")
    print(f"  - Max order cost: ${FAILSAFE_MAX_ORDER_COST:.2f}")
    print()
    print("⚠️  CTRL+C TO STOP")
    print("=" * 100)
    print()

    last_slug = None
    session_stats = {"windows": 0, "paired": 0, "pnl": 0.0}
    cached_market = None
    last_good_books = None
    consecutive_book_failures = 0

    try:
        while True:
            cycle_start = time.time()

            try:
                slug, _ = get_current_slug()

                if slug != last_slug:
                    if window_state:
                        trades_log.append(window_state)
                        save_trades()
                        if window_state['filled_up_shares'] > 0 and window_state['filled_up_shares'] == window_state['filled_down_shares']:
                            session_stats['paired'] += 1
                        session_stats['pnl'] += window_state['realized_pnl_usd']

                        flush_ticks()  # Flush any remaining tick data

                        # 99c Sniper Resolution - wait for settlement BEFORE showing summary
                        sniper_result_line = ""
                        if window_state.get('capture_99c_fill_notified'):
                            sniper_side = window_state.get('capture_99c_side')
                            sniper_shares = window_state.get('capture_99c_filled_up', 0) + window_state.get('capture_99c_filled_down', 0)

                            # Query Polymarket API for actual settlement result
                            # Retry up to 6 times (30 seconds total) waiting for market resolution
                            sniper_won = None
                            sniper_pnl = 0
                            for retry in range(6):
                                try:
                                    sniper_won = check_99c_outcome(sniper_side, last_slug)
                                    if sniper_won is not None:
                                        sniper_pnl = sniper_shares * 0.01 if sniper_won else -sniper_shares * 0.99
                                        break
                                    else:
                                        if retry < 5:
                                            print(f"[{ts()}] 99c SNIPER: Market not resolved, retrying in 5s... ({retry+1}/6)")
                                            time.sleep(5)
                                        else:
                                            print(f"[{ts()}] 99c SNIPER RESULT: PENDING after 30s - will resolve on next window")
                                except Exception as e:
                                    print(f"[{ts()}] 99c SNIPER RESULT ERROR (retry {retry+1}): {e}")
                                    if retry < 5:
                                        time.sleep(5)
                                    sniper_won = None
                                    sniper_pnl = 0

                            # Build result line for banner
                            if sniper_won is not None:
                                # Only add sniper PnL if we held to settlement (no early exit)
                                # Early exits already counted in realized_pnl_usd
                                if not window_state.get('capture_99c_exited'):
                                    session_stats['pnl'] += sniper_pnl
                                emoji = "✅" if sniper_won else "❌"
                                exited_tag = " (EXITED EARLY)" if window_state.get('capture_99c_exited') else ""
                                sniper_result_line = f"│  {emoji} 99c {sniper_side}: {'WIN' if sniper_won else 'LOSS'} ${sniper_pnl:+.2f}{exited_tag}"
                                notify_99c_resolution(sniper_side, sniper_shares, sniper_won, sniper_pnl)

                                # Log outcome to Sheets/Supabase for dashboard tracking
                                entry_price = window_state.get('capture_99c_fill_price', 0.99)
                                event_type = "CAPTURE_99C_WIN" if sniper_won else "CAPTURE_99C_LOSS"
                                log_event(event_type, last_slug,
                                    side=sniper_side,
                                    shares=sniper_shares,
                                    price=entry_price,
                                    pnl=sniper_pnl,
                                    details=json.dumps({
                                        "outcome": "WIN" if sniper_won else "LOSS",
                                        "settlement_price": 1.00 if sniper_won else 0.00
                                    }))
                            else:
                                sniper_result_line = f"│  ⏳ 99c {sniper_side}: PENDING ({sniper_shares} shares)"
                                # Add to pending list to retry on next window
                                entry_price = window_state.get('capture_99c_fill_price', 0.99)
                                pending_99c_resolutions.append({
                                    'slug': last_slug,
                                    'side': sniper_side,
                                    'shares': sniper_shares,
                                    'entry_price': entry_price,
                                    'timestamp': time.time()
                                })

                        # Show WINDOW COMPLETE banner AFTER settlement
                        print()
                        print("┌─────────────────── WINDOW COMPLETE ───────────────────┐")
                        if sniper_result_line:
                            print(sniper_result_line.ljust(55) + "│")
                        print(f"│  💵 Session PnL: ${session_stats['pnl']:.2f}".ljust(55) + "│")
                        print("└────────────────────────────────────────────────────────┘")

                        # Check for claimable positions after window closes
                        try:
                            from auto_redeem import check_and_claim
                            claimable = check_and_claim()
                            if claimable:
                                total = sum(p['claimable_usdc'] for p in claimable)
                                print(f"[{ts()}] CLAIMABLE: ${total:.2f} - check polymarket.com to claim!")
                        except ImportError:
                            pass  # auto_redeem module not available
                        except Exception as e:
                            print(f"[{ts()}] REDEEM_CHECK_ERROR: {e}")

                    cancel_all_orders()
                    window_state = reset_window_state(slug)
                    market_price_history.clear()  # v1.24: Clear price history for entry filter
                    cached_market = None
                    last_good_books = None  # v1.52: Prevent stale books leaking across windows
                    consecutive_book_failures = 0
                    last_slug = slug
                    error_count = 0
                    session_stats['windows'] += 1

                    print()
                    print("=" * 100)
                    print(f"[{ts()}] NEW WINDOW: {slug}")
                    print(f"[{ts()}] Session: {session_stats['windows']} windows | {session_stats['paired']} paired | PnL: ${session_stats['pnl']:.2f}")

                    # Resolve any pending 99c outcomes from previous windows
                    if pending_99c_resolutions:
                        resolved = []
                        for pending in pending_99c_resolutions[:]:  # Iterate over copy
                            try:
                                result = check_99c_outcome(pending['side'], pending['slug'])
                                if result is not None:
                                    pnl = pending['shares'] * 0.01 if result else -pending['shares'] * 0.99
                                    event_type = "CAPTURE_99C_WIN" if result else "CAPTURE_99C_LOSS"
                                    log_event(event_type, pending['slug'],
                                        side=pending['side'],
                                        shares=pending['shares'],
                                        price=pending['entry_price'],
                                        pnl=pnl,
                                        details=json.dumps({
                                            "outcome": "WIN" if result else "LOSS",
                                            "settlement_price": 1.00 if result else 0.00,
                                            "delayed_resolution": True
                                        }))
                                    notify_99c_resolution(pending['side'], pending['shares'], result, pnl)
                                    print(f"[{ts()}] ✅ RESOLVED PENDING 99c: {pending['slug']} {'WIN' if result else 'LOSS'} ${pnl:+.2f}")
                                    resolved.append(pending)
                            except Exception as e:
                                print(f"[{ts()}] PENDING_RESOLVE_ERROR: {e}")
                        # Remove resolved items
                        for item in resolved:
                            pending_99c_resolutions.remove(item)
                        if pending_99c_resolutions:
                            print(f"[{ts()}] ⏳ Still pending: {len(pending_99c_resolutions)} unresolved 99c trades")

                    # Check gas balance and alert if low
                    gas_balance = check_gas_and_alert()
                    if gas_balance is not None:
                        days_left = gas_balance / (47 * 0.0268)
                        gas_status = "OK" if gas_balance >= GAS_LOW_THRESHOLD else ("LOW" if gas_balance >= GAS_CRITICAL_THRESHOLD else "CRITICAL")
                        print(f"[{ts()}] ⛽ Gas: {gas_balance:.4f} MATIC ({days_left:.1f} days) [{gas_status}]")

                    print("=" * 100)

                    # Log window start to Google Sheets
                    log_event("WINDOW_START", slug, session_windows=session_stats['windows'])

                if not cached_market:
                    cached_market = get_market_data(slug)
                if not cached_market:
                    time.sleep(0.5)
                    continue

                time_str, remaining_secs = get_time_remaining(cached_market)

                if remaining_secs < 0:
                    time.sleep(2)
                    continue

                # CLOSE_GUARD: Cancel pending buy orders but keep processing
                # (exits, tick logging, danger score all continue until T-0)
                close_guard_active = remaining_secs <= CLOSE_GUARD_SECONDS
                if close_guard_active and not window_state.get('_close_guard_fired'):
                    cancel_all_orders()
                    window_state['_close_guard_fired'] = True

                books = get_order_books(cached_market)
                if not books:
                    error_count += 1
                    consecutive_book_failures += 1
                    # If we have a position and API keeps failing, use last known books for exits
                    if (last_good_books and
                        window_state.get('capture_99c_fill_notified') and
                        not window_state.get('capture_99c_exited')):
                        books = last_good_books
                        print(f"[{ts()}] API_DOWN: Using stale books for exits (failure #{consecutive_book_failures})")
                        # Emergency: if API down for 5+ consecutive cycles, force hard stop
                        if consecutive_book_failures >= 5:
                            capture_side = window_state.get('capture_99c_side')
                            print(f"[{ts()}] API_DOWN_EMERGENCY: {consecutive_book_failures} failures, forcing exit")
                            execute_hard_stop(capture_side, books, market_data=cached_market)
                            if not window_state.get('capture_99c_exited'):
                                window_state['capture_99c_exited'] = True
                                window_state['capture_99c_exit_reason'] = 'api_down_emergency'
                    else:
                        time.sleep(0.5)
                        continue
                else:
                    consecutive_book_failures = 0
                    last_good_books = books

                window_state['up_token'] = books['up_token']
                window_state['down_token'] = books['down_token']

                # Startup position sync
                if window_state.get('startup_sync_done') is not True:
                    window_state['startup_sync_done'] = True

                    # Retry startup sync to avoid stale API cache
                    api_up, api_down = 0, 0
                    for attempt in range(3):
                        api_position = verify_position_from_api()
                        if api_position:
                            api_up, api_down = api_position
                            if api_up > 0 or api_down > 0:
                                # Position found - verify it's consistent
                                time.sleep(1)
                                api_position2 = verify_position_from_api()
                                if api_position2 and api_position == api_position2:
                                    break  # Consistent, trust it
                                print(f"[{ts()}] STARTUP_SYNC: Position changed, retrying... ({attempt+1}/3)")
                            else:
                                break  # No position, trust it
                        time.sleep(0.5)

                    if api_up > 0 or api_down > 0:
                        print(f"[{ts()}] STARTUP_SYNC: Found position UP={api_up} DOWN={api_down}")
                        window_state['filled_up_shares'] = api_up
                        window_state['filled_down_shares'] = api_down

                # Analyze order book ONCE per tick (reused by log_state, OB exit, danger score)
                ob_result_cached = None
                if ORDERBOOK_ANALYZER_AVAILABLE and orderbook_analyzer and books:
                    ob_result_cached = orderbook_analyzer.analyze(
                        books.get('up_bids', []), books.get('up_asks', []),
                        books.get('down_bids', []), books.get('down_asks', [])
                    )

                log_state(remaining_secs, books, ob_result=ob_result_cached)

                # 99c BID CAPTURE - core sniper strategy
                # Confidence-based 99c capture: only bet when confidence >= 95%
                # Skip new entries during CLOSE_GUARD (exits still active)
                if CAPTURE_99C_ENABLED and books and not window_state.get('capture_99c_used') and not close_guard_active:
                    ask_up = float(books['up_asks'][0]['price']) if books.get('up_asks') else 0.50
                    ask_down = float(books['down_asks'][0]['price']) if books.get('down_asks') else 0.50
                    capture = check_99c_capture_opportunity(ask_up, ask_down, remaining_secs)
                    if capture:
                        execute_99c_capture(
                            capture['side'],
                            capture['ask'],
                            capture['confidence'],
                            capture['penalty'],
                            remaining_secs
                        )

                # Check if 99c capture order filled
                if window_state.get('capture_99c_order') and not window_state.get('capture_99c_fill_notified'):
                    order_id = window_state['capture_99c_order']
                    status = get_order_status(order_id)
                    if status.get('filled', 0) > 0:
                        filled = status['filled']
                        side = window_state['capture_99c_side']
                        # Get actual fill price from order status (default to 0.99 if not available)
                        fill_price = status.get('price', 0.99)
                        if fill_price <= 0:
                            fill_price = 0.99  # Fallback if price not returned

                        # Query Position API for EXACT share balance (size_matched can lie - Issue #245)
                        actual_shares = filled  # Default to order API value
                        api_position = verify_position_from_api()
                        if api_position:
                            api_up, api_down = api_position
                            actual_shares = api_up if side == 'UP' else api_down
                            if abs(actual_shares - filled) > 0.001:
                                print(f"[{ts()}] FILL_PRECISION: order says {filled:.4f}, position API says {actual_shares:.4f} (delta={filled - actual_shares:.4f})")
                            if actual_shares <= 0:
                                # Position API hasn't caught up yet — trust order API for now
                                actual_shares = filled
                                print(f"[{ts()}] FILL_PRECISION: Position API returned 0, using order API value {filled:.4f}")

                        # Calculate actual P&L based on real fill price
                        actual_pnl = actual_shares * (1.00 - fill_price)
                        # Store fill price for later reference
                        window_state['capture_99c_fill_price'] = fill_price
                        print()
                        print(f"┌─────────────── 99c CAPTURE FILLED ───────────────┐")
                        print(f"│  ✅ {side}: {actual_shares:.2f} shares filled @ {fill_price*100:.0f}c".ljust(48) + "│")
                        print(f"│  💰 Expected profit: ${actual_pnl:.2f}".ljust(48) + "│")
                        print(f"└──────────────────────────────────────────────────┘")
                        print()
                        # Record peak confidence at fill time
                        if books.get('up_asks') and books.get('down_asks'):
                            current_ask = float(books['up_asks'][0]['price']) if side == "UP" else float(books['down_asks'][0]['price'])
                            peak_conf, _ = calculate_99c_confidence(current_ask, remaining_secs)
                            window_state['capture_99c_peak_confidence'] = peak_conf
                        window_state['capture_99c_fill_notified'] = True
                        # Update BOTH tracking fields with actual shares from Position API
                        window_state[f'capture_99c_filled_{side.lower()}'] = actual_shares
                        window_state[f'filled_{side.lower()}_shares'] = actual_shares
                        # Send Telegram notification
                        notify_99c_fill(side, actual_shares, peak_conf * 100 if peak_conf else 95, remaining_secs)
                        log_event("CAPTURE_FILL", slug, side=side, shares=actual_shares,
                                        price=fill_price, pnl=actual_pnl)

                # === 60¢ HARD STOP CHECK (v1.34, BTC safety added v1.52) ===
                # Exit immediately using FOK market orders if best bid <= 60¢
                # Uses best BID (not ask) to ensure real liquidity exists
                # BUT: suppress if BTC is safely on our side (thin books ≠ real danger)
                if (HARD_STOP_ENABLED and
                    window_state.get('capture_99c_fill_notified') and
                    not window_state.get('capture_99c_exited')):

                    capture_side = window_state.get('capture_99c_side')

                    # Check if hard stop should trigger (based on best bid)
                    should_trigger, best_bid = check_hard_stop_trigger(books, capture_side)

                    if should_trigger:
                        # BTC safety check — same logic as danger score
                        hs_btc_safe = False
                        if RTDS_AVAILABLE and rtds_feed and rtds_feed.is_connected():
                            hs_delta = rtds_feed.get_window_delta()
                            if hs_delta is not None:
                                if capture_side == "DOWN" and hs_delta <= -DANGER_BTC_SAFE_MARGIN:
                                    hs_btc_safe = True
                                elif capture_side == "UP" and hs_delta >= DANGER_BTC_SAFE_MARGIN:
                                    hs_btc_safe = True

                        if hs_btc_safe:
                            hs_delta = rtds_feed.get_window_delta()
                            print(f"[{ts()}] HARD STOP SUPPRESSED: bid={best_bid*100:.0f}c but BTC ${hs_delta:+.0f} safely on our side")
                        else:
                            print(f"[{ts()}] 🛑 HARD STOP: {capture_side} best bid at {best_bid*100:.0f}c <= {HARD_STOP_TRIGGER*100:.0f}c trigger")
                            execute_hard_stop(capture_side, books, market_data=cached_market)
                            if not window_state.get('capture_99c_exited'):
                                window_state['capture_99c_exited'] = True
                                window_state['capture_99c_exit_reason'] = 'hard_stop_60c'

                # === 99c OB-BASED EARLY EXIT CHECK ===
                # Exit early if OB shows sellers dominating for 3 consecutive ticks
                if (OB_EARLY_EXIT_ENABLED and
                    window_state.get('capture_99c_fill_notified') and
                    not window_state.get('capture_99c_exited')):

                    capture_side = window_state.get('capture_99c_side')

                    # Get OB imbalance for our side (use cached result from earlier this tick)
                    if ob_result_cached:
                        imb = ob_result_cached.get('up_imbalance', 0) if capture_side == "UP" else ob_result_cached.get('down_imbalance', 0)

                        # Track negative ticks with decay (decrement by 1 instead of reset to 0)
                        if imb < OB_EARLY_EXIT_THRESHOLD:
                            window_state['ob_negative_ticks'] = window_state.get('ob_negative_ticks', 0) + 1
                            print(f"[{ts()}] 99c OB WARNING: {capture_side} imb={imb:+.2f} ({window_state['ob_negative_ticks']}/3)")
                        else:
                            window_state['ob_negative_ticks'] = max(0, window_state.get('ob_negative_ticks', 0) - 1)

                        # Trigger exit if 3 consecutive negative ticks
                        if window_state['ob_negative_ticks'] >= 3:
                            print(f"[{ts()}] 🚨 99c OB EXIT TRIGGERED: {capture_side} imb={imb:+.2f}")
                            execute_99c_early_exit(capture_side, imb, books, reason="ob_reversal", market_data=cached_market)

                # Calculate danger score (used for logging/monitoring)
                # Skip if we already exited early
                if window_state.get('capture_99c_fill_notified') and not window_state.get('capture_99c_exited'):
                    # Calculate danger score from 5 signals
                    bet_side = window_state.get('capture_99c_side')

                    # Get current confidence (same calculation as original entry)
                    # GUARD: If no asks on our side, skip danger score entirely — empty book
                    # produces current_ask=0 which causes catastrophic false confidence drop
                    if bet_side == "UP":
                        our_asks = books.get('up_asks', [])
                    else:
                        our_asks = books.get('down_asks', [])

                    if not our_asks:
                        # No asks = MMs pulled quotes, NOT necessarily danger. Skip danger calc.
                        current_ask = 0
                        current_confidence = window_state.get('capture_99c_peak_confidence', 0.95)
                    else:
                        current_ask = float(our_asks[0]['price'])
                        current_confidence, _ = calculate_99c_confidence(current_ask, remaining_secs)

                    # Get order book imbalance for our side (use cached result from earlier this tick)
                    our_imbalance = 0.0
                    if ob_result_cached:
                        our_imbalance = ob_result_cached['up_imbalance'] if bet_side == "UP" else ob_result_cached['down_imbalance']

                    # Get opponent ask price
                    if bet_side == "UP":
                        opponent_asks = books.get('down_asks', [])
                    else:
                        opponent_asks = books.get('up_asks', [])
                    opponent_ask = float(opponent_asks[0]['price']) if opponent_asks else 0.50

                    # Calculate danger score
                    danger_result = calculate_danger_score(
                        current_confidence=current_confidence,
                        peak_confidence=window_state.get('capture_99c_peak_confidence', 0),
                        our_imbalance=our_imbalance,
                        btc_price_history=btc_price_history,
                        opponent_ask=opponent_ask,
                        time_remaining=remaining_secs,
                        bet_side=bet_side
                    )
                    window_state['danger_score'] = danger_result['score']
                    window_state['danger_result'] = danger_result  # Store full result for logging

                    # DANGER SCORE EXIT — use lower threshold in final 30 seconds
                    # BUT: suppress if BTC is comfortably on our side (thin books ≠ real danger)
                    # AND: skip entirely if RTDS is disconnected (can't verify BTC safety)
                    btc_safe = False
                    rtds_connected = RTDS_AVAILABLE and rtds_feed and rtds_feed.is_connected()
                    if rtds_connected:
                        delta = rtds_feed.get_window_delta()
                        if delta is not None:
                            # DOWN bet wins when BTC goes down (delta < 0), UP bet wins when BTC goes up (delta > 0)
                            if bet_side == "DOWN" and delta <= -DANGER_BTC_SAFE_MARGIN:
                                btc_safe = True
                            elif bet_side == "UP" and delta >= DANGER_BTC_SAFE_MARGIN:
                                btc_safe = True

                    active_threshold = DANGER_THRESHOLD_FINAL if remaining_secs <= 30 else DANGER_THRESHOLD
                    if not rtds_connected and danger_result['score'] >= active_threshold:
                        # RTDS disconnected — can't verify BTC safety, don't trust danger score
                        print(f"[{ts()}] DANGER SKIP: score={danger_result['score']:.2f} but RTDS disconnected, cannot verify BTC")
                        window_state['danger_ticks'] = 0
                    elif danger_result['score'] >= active_threshold and not btc_safe:
                        # Require 2 consecutive danger ticks to avoid false triggers
                        window_state['danger_ticks'] = window_state.get('danger_ticks', 0) + 1
                        if window_state['danger_ticks'] >= 2:
                            print(f"[{ts()}] DANGER EXIT: score={danger_result['score']:.2f} >= {active_threshold} (2 consecutive)")
                            capture_side = window_state.get('capture_99c_side')
                            execute_hard_stop(capture_side, books, market_data=cached_market)
                            # Always mark exited after hard stop attempt (prevents cascading re-triggers)
                            if not window_state.get('capture_99c_exited'):
                                window_state['capture_99c_exited'] = True
                                window_state['capture_99c_exit_reason'] = 'danger_score'
                    elif btc_safe and danger_result['score'] >= active_threshold:
                        # Log suppression so we can track it
                        delta = rtds_feed.get_window_delta()
                        print(f"[{ts()}] DANGER SUPPRESSED: score={danger_result['score']:.2f} but BTC ${delta:+.0f} safely on our side")
                        window_state['danger_ticks'] = 0
                    else:
                        window_state['danger_ticks'] = 0

                elapsed = time.time() - cycle_start
                time.sleep(max(0.1, 0.5 - elapsed))

            except Exception as e:
                error_count += 1
                print(f"[{ts()}] Error: {e}")
                import traceback
                traceback.print_exc()
                log_event("ERROR", slug if slug else "unknown",
                                error_type="MAIN_LOOP", message=str(e)[:200])
                time.sleep(0.5)

    except KeyboardInterrupt:
        print()
        print("🛑 STOPPING BOT...")
        try:
            cancel_all_orders()
            print("🛑 ✅ All orders cancelled")
        except:
            pass

        print()
        print(f"🛑 SESSION SUMMARY:")
        print(f"🛑 Windows: {session_stats['windows']}")
        print(f"🛑 Paired: {session_stats['paired']}")
        print(f"🛑 Total PnL: ${session_stats['pnl']:.2f}")
        print(f"🛑 PROFIT={session_counters['profit_pairs']} LOSS_AVOID={session_counters['loss_avoid_pairs']} FLATTEN={session_counters['hard_flattens']}")

        if window_state:
            trades_log.append(window_state)
        save_trades()
        print("Trades saved to trades_smart.json")

if __name__ == "__main__":
    main()
