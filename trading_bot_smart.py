#!/usr/bin/env python3
"""
CHATGPT POLY BOT - SMART STRATEGY VERSION
==========================================
BTC 15-minute Up/Down markets with MULTI-TIMEFRAME SIGNAL ANALYSIS.

CRITICAL: Never allow unequal UP/DOWN shares at window resolution.
- If imbalanced, enter PAIRING_MODE immediately
- Block new arb quotes until flat
- Forced completion with escalating steps
- HARD_FLATTEN as last resort

SMART STRATEGY ADDITIONS:
- Multi-timeframe momentum (1m, 5m, 15m)
- Volatility filtering (skip choppy markets)
- Confidence scoring for trade quality
- Dynamic position sizing based on signal strength
"""

# ===========================================
# BOT VERSION
# ===========================================
BOT_VERSION = {
    "version": "v1.47",
    "codename": "Finish Line II",
    "date": "2026-02-16",
    "changes": "Hard stop lowered to 40c, OB exit disabled — only exit is hard stop at 40c"
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
# SMART STRATEGY IMPORTS
# ============================================================================
try:
    from strategy_signals import (
        get_tracker, get_signal, update_btc_price,
        get_momentum, get_volatility, get_position_size_multiplier,
        CONFIDENCE_MIN_TRADE, BTCPriceTracker
    )
    STRATEGY_SIGNALS_AVAILABLE = True
    print("Smart strategy signals: ENABLED")
except ImportError:
    STRATEGY_SIGNALS_AVAILABLE = False
    print("WARNING: strategy_signals.py not found - using basic strategy")

# ============================================================================
# CONSTANTS
# ============================================================================

# Price constraints
LOCK_MAX = 0.99
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
PAIR_DEADLINE_SECONDS = 90        # Start forced pairing 90s before close (was 60)
TAKER_AT_SECONDS = 20             # Allow taker completion at 20s
HARD_FLATTEN_SECONDS = 10         # Guaranteed flat by 10s pre-close

# ===========================================
# TIMING SAFEGUARDS - PREVENT RACE CONDITIONS
# ===========================================
ORDER_COOLDOWN_SECONDS = 3.0
PAIRING_LOOP_DELAY = 2.0
FILL_CHECK_WAIT = 5.0
POSITION_VERIFY_BEFORE_ORDER = True

# TTL behavior
TTL_1C_MS = 1000
TTL_2C_MS = 2500

# Loss cap (test mode)
MAX_WINDOW_LOSS_USD = 0.10

# Hedge deadline
HEDGE_DEADLINE_MS = 400

# Pinned skip
PINNED_ASK_LIMIT = 0.02

# ===========================================
# DIVERGENCE THRESHOLD - WAIT FOR CLEAR TREND
# ===========================================
# Require BOTH conditions:
# - Cheap side must be <= 42c (strong conviction)
# - Expensive side must be >= 58c (clear momentum)
DIVERGENCE_THRESHOLD = 0.42       # Cheap side must be <= 42c
MIN_EXPENSIVE_SIDE_PRICE = 0.58   # Expensive side must be >= 58c

# ===========================================
# LOSS MINIMIZATION
# ===========================================
MAX_PER_TRADE_LOSS = 0.03         # 3c max loss per share (3%)

# Sizing
MIN_SHARES = 5
MICRO_IMBALANCE_TOLERANCE = 0.5  # Accept up to 0.5 share difference as "balanced"

# ===========================================
# AGGRESSIVE COMPLETION
# ===========================================
AGGRESSIVE_COMPLETION_WAIT = 2.0
AGGRESSIVE_COMPLETION_ENABLED = True
USE_FAST_ORDER_CHECK = True
MOMENTUM_FIRST_ENABLED = True
ARB_ENABLED = False                # Disable ARB strategy, 99c sniper only

# ===========================================
# ROI HALT SETTINGS (v1.46)
# ===========================================
ROI_HALT_THRESHOLD = 0.45          # Halt all trading at 45% ROI
ROI_HALT_STATE_FILE = os.path.expanduser("~/polybot/trading_halt_state.json")

# ===========================================
# SMART STRATEGY SETTINGS (NEW)
# ===========================================
USE_SMART_SIGNALS = False          # Disabled - trade on divergence alone
SMART_SIGNAL_MIN_CONFIDENCE = 55   # Minimum confidence to trade (0-100)
SMART_SIGNAL_SKIP_CHOPPY = True    # Skip trading in choppy markets
USE_CONFIDENCE_SIZING = True       # Adjust position size by confidence

# ===========================================
# ORDER BOOK IMBALANCE SETTINGS
# ===========================================
USE_ORDERBOOK_SIGNALS = True       # Enable order book imbalance analysis
ORDERBOOK_MIN_SIGNAL_STRENGTH = "MODERATE"  # Minimum: WEAK, MODERATE, STRONG
ORDERBOOK_REQUIRE_TREND = False    # Require sustained trend confirmation
ORDERBOOK_LOG_ALWAYS = True        # Always show imbalance in status log
SMART_SIGNAL_LOG_ALWAYS = True     # Log signals even when not trading

# ===========================================
# BUG FIXES - ORDER & POSITION HANDLING
# ===========================================
ORDER_SETTLE_DELAY = 7.0           # Seconds to wait after order for blockchain settlement
ORDER_VERIFY_RETRIES = 3           # Retries when verifying order/position

# ===========================================
# HYBRID LOSS PROTECTION - HEDGE ESCALATION
# ===========================================
# Time-based tolerance for hedge price (profit target is 99c combined)
# profit_target = 99 - entry_price (gives 1c profit)
# max_hedge = profit_target + tolerance
HEDGE_ESCALATION = [
    (0,   0),    # 0-2 min: No tolerance, require 1c profit (99c combined)
    (120, 2),    # 2-5 min: Accept 1c loss (tolerance = 2c, so 101c combined)
    (300, 3),    # 5-8 min: Accept 2c loss (tolerance = 3c, so 102c combined)
    (480, 4),    # 8-10 min: Accept 3c loss (tolerance = 4c, so 103c combined)
    (600, 6),    # 10+ min: Accept 5c loss (tolerance = 6c, so 105c combined)
]
HEDGE_PRICE_CAP = 0.50             # Never hedge above 50c

# ===========================================
# BAIL MODE - EMERGENCY EXIT
# ===========================================
BAIL_UNHEDGED_TIMEOUT = 120        # Bail if unhedged >2 minutes
BAIL_TIME_REMAINING = 90           # Force bail at 90 seconds remaining (NO EXCEPTIONS)
BAIL_LOSS_THRESHOLD = 0.05         # Bail if position down >5%

# ===========================================
# EARLY BAIL - MINIMIZE EXPOSURE (v1.10)
# ===========================================
PAIR_WINDOW_SECONDS = 5            # If second leg doesn't fill in 5s, bail immediately
EARLY_HEDGE_TIMEOUT = 30           # (legacy) Try hedging for 30s before evaluating bail
EARLY_BAIL_MAX_LOSS = 0.05         # 5c max loss per share triggers early decision
EARLY_BAIL_CHECK_INTERVAL = 10     # (legacy) Re-check every 10s after EARLY_HEDGE_TIMEOUT
MARKET_REVERSAL_THRESHOLD = 0.10   # 10c move in 15s = consider immediate bail

# OB-BASED EARLY BAIL (v1.9) - Detect reversals via order book before price moves
OB_REVERSAL_THRESHOLD = -0.25      # If filled side OB imbalance < -25%, sellers dominating
OB_REVERSAL_PRICE_CONFIRM = 0.03   # Only need 3c price drop when OB confirms reversal

# ===========================================
# 99c EARLY EXIT (OB-BASED) - Cut losses early
# ===========================================
OB_EARLY_EXIT_ENABLED = False      # DISABLED: Only exit rule is hard stop at 40c
OB_EARLY_EXIT_THRESHOLD = -0.30    # Exit when sellers > 30% (OB imbalance < -0.30)

# ===========================================
# 99c PRICE STOP-LOSS - Hard price floor exit (LEGACY - DISABLED)
# ===========================================
PRICE_STOP_ENABLED = False         # DISABLED - replaced by HARD_STOP below
PRICE_STOP_TRIGGER = 0.80          # (legacy) Exit when our side's price <= 80c
PRICE_STOP_FLOOR = 0.50            # (legacy) Never sell below 50c

# ===========================================
# 60¢ HARD STOP - FOK Market Orders (v1.34)
# ===========================================
# Guaranteed emergency exit using Fill-or-Kill market orders.
# Triggers on BEST BID (not ask) to ensure real liquidity exists.
# Will sell at any price to avoid riding to $0.
HARD_STOP_ENABLED = True           # Enable 40¢ hard stop
HARD_STOP_TRIGGER = 0.40           # Exit when best bid <= 40¢
HARD_STOP_FLOOR = 0.01             # Effectively no floor (1¢ minimum)
HARD_STOP_USE_FOK = True           # Use Fill-or-Kill market orders

# ===========================================
# ENTRY RESTRICTIONS
# ===========================================
MIN_TIME_FOR_ENTRY = 300           # Never enter with <5 minutes (300s) remaining

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

# 99c Capture Hedge Protection
CAPTURE_99C_HEDGE_ENABLED = False       # DISABLED: Was triggering on end-of-window price death (50c/1c)
CAPTURE_99C_HEDGE_THRESHOLD = 0.85      # (Legacy) Hedge if confidence drops below 85%

# 99c Entry Filters (v1.24 - based on tick data analysis)
# These filters prevent entering on volatile/spiking markets that lead to losses
ENTRY_FILTER_ENABLED = True             # Enable smart entry filtering
ENTRY_FILTER_STABLE_TICKS = 3           # Last N ticks must all be >= 97c for "stable" entry
ENTRY_FILTER_STABLE_THRESHOLD = 0.97    # Price threshold for stability check
ENTRY_FILTER_MAX_JUMP = 0.08            # Max allowed tick-to-tick jump in past 10 ticks
ENTRY_FILTER_MAX_OPP_RECENT = 0.15      # Skip if opposing side was > this in past 30 ticks
ENTRY_FILTER_HISTORY_SIZE = 30          # Number of ticks to keep for filtering

# Danger Scoring Configuration (for smart hedge)
DANGER_THRESHOLD = 0.40              # Hedge triggers when danger >= this
DANGER_WEIGHT_CONFIDENCE = 3.0       # Weight for confidence drop from peak
DANGER_WEIGHT_IMBALANCE = 0.4        # Weight for order book imbalance against us
DANGER_WEIGHT_VELOCITY = 2.0         # Weight for BTC price velocity against us
DANGER_WEIGHT_OPPONENT = 0.5         # Weight for opponent ask price strength
DANGER_WEIGHT_TIME = 0.3             # Weight for time decay in final 60s

# Bot identity
BOT_NAME = "ChatGPT-Smart"

# States
STATE_QUOTING = "QUOTING"
STATE_PAIRING = "PAIRING_MODE"
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

# v1.46: Trading halt state (set in main(), checked in order functions)
trading_halted = False
capital_deployed = 0.0

# Session counters
session_counters = {
    "profit_pairs": 0,
    "loss_avoid_pairs": 0,
    "hard_flattens": 0,
    "smart_skips": 0,  # NEW: Track how many trades skipped by smart signals
    "smart_trades": 0,  # NEW: Track smart signal approved trades
}

last_pair_type = None

# ============================================================================
# REAL-TIME ACTIVITY LOG
# ============================================================================

BOT_ID = "CHATGPT-SMART"
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
        "avg_up_price_paid": 0.0,
        "avg_down_price_paid": 0.0,
        "open_up_order_ids": [],
        "open_down_order_ids": [],
        "realized_pnl_usd": 0.0,
        "state": STATE_QUOTING,
        "up_token": None,
        "last_order_time": 0,
        "down_token": None,
        "arb_order_time": None,
        "current_arb_orders": None,
        "pairing_attempts": 0,
        "telegram_notified": False,
        "arb_placed_this_window": False,
        "smart_signal_confidence": 0,  # NEW: Track confidence of winning signal
        "capture_99c_used": False,     # 99c capture: only once per window
        "capture_99c_order": None,     # 99c capture: order ID
        "capture_99c_side": None,      # 99c capture: UP or DOWN
        "capture_99c_shares": 0,       # 99c capture: shares ordered
        "capture_99c_filled_up": 0,    # 99c capture: filled UP shares (exclude from pairing)
        "capture_99c_filled_down": 0,  # 99c capture: filled DOWN shares (exclude from pairing)
        "capture_99c_fill_notified": False,  # 99c capture: have we shown fill notification
        "capture_99c_hedged": False,         # 99c capture: whether we've hedged this position
        "capture_99c_hedge_price": 0,        # 99c capture: price paid for hedge
        "capture_99c_exited": False,         # 99c capture: whether we've early-exited this position
        "ob_negative_ticks": 0,              # 99c early exit: consecutive negative OB ticks
        "started_mid_window": False,         # True if bot started mid-window (skip trading)
        "pairing_start_time": None,          # When PAIRING_MODE was entered
        "best_distance_seen": None,          # Best (lowest) distance from profit target (in cents)
        "pending_hedge_order_id": None,      # Track pending hedge order to prevent duplicates
        "pending_hedge_side": None,          # Which side the pending hedge is for (UP/DOWN)
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
    if abs(raw_imb) < MICRO_IMBALANCE_TOLERANCE:
        return 0
    return raw_imb

def get_arb_imbalance():
    """Calculate ARB imbalance (excludes 99c capture shares)"""
    if not window_state:
        return 0
    arb_up = window_state['filled_up_shares'] - window_state.get('capture_99c_filled_up', 0)
    arb_down = window_state['filled_down_shares'] - window_state.get('capture_99c_filled_down', 0)
    raw_imb = arb_up - arb_down
    if abs(raw_imb) < MICRO_IMBALANCE_TOLERANCE:
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

USDC_CONTRACT = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # USDC on Polygon

def get_portfolio_balance():
    """Get total portfolio balance: positions value + USDC cash."""
    positions_value = 0.0
    usdc_balance = 0.0

    # 1. Sum position values from Polymarket
    try:
        url = f"https://data-api.polymarket.com/positions?user={WALLET_ADDRESS.lower()}"
        resp = http_session.get(url, timeout=5)
        for pos in resp.json():
            positions_value += float(pos.get('currentValue', 0))
    except Exception as e:
        print(f"[BALANCE] Position query failed: {e}")

    # 2. Check USDC balance via ERC20 balanceOf
    try:
        addr_padded = WALLET_ADDRESS.lower().replace('0x', '').zfill(64)
        data = f"0x70a08231{addr_padded}"
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_call",
            "params": [{"to": USDC_CONTRACT, "data": data}, "latest"],
            "id": 1
        }
        response = requests.post(POLYGON_RPC, json=payload, timeout=5)
        result = response.json().get("result")
        if result:
            usdc_balance = int(result, 16) / 1e6  # USDC has 6 decimals
    except Exception as e:
        print(f"[BALANCE] USDC query failed: {e}")

    return positions_value, usdc_balance

_last_balance_date = None  # Track last snapshot date (EST)

def check_and_log_balance():
    """Log daily balance snapshot on first window after midnight EST."""
    global _last_balance_date
    est_now = datetime.now(ZoneInfo("America/New_York"))
    est_date = est_now.strftime("%Y-%m-%d")
    if _last_balance_date == est_date:
        return
    _last_balance_date = est_date
    pos_value, usdc_cash = get_portfolio_balance()
    total = pos_value + usdc_cash
    print(f"[{ts()}] 💰 Balance snapshot: ${total:.2f} (positions: ${pos_value:.2f}, USDC: ${usdc_cash:.2f})")
    log_event("BALANCE_SNAPSHOT",
              details=f"total={total:.2f}|positions={pos_value:.2f}|usdc={usdc_cash:.2f}|date={est_date}")

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

def notify_profit_pair(up_shares, avg_up, down_shares, avg_down):
    """PROFIT_PAIR notification"""
    global window_state
    if window_state.get('telegram_notified'):
        return
    window_state['telegram_notified'] = True

    pair_total = avg_up + avg_down
    edge = 1.00 - pair_total
    conf = window_state.get('smart_signal_confidence', 0)

    msg = f"""🟩 <b>PROFIT PAIR</b>
BTC 15m window
UP {up_shares} @ {avg_up:.2f}
DOWN {down_shares} @ {avg_down:.2f}
Total = {pair_total:.2f}
Edge = +{edge:.2f}
Signal confidence: {conf}%
Status: SAFE"""
    send_telegram(msg)

def notify_loss_avoid_pair(up_shares, avg_up, down_shares, avg_down):
    """LOSS_AVOID_PAIR notification"""
    global window_state
    if window_state.get('telegram_notified'):
        return
    window_state['telegram_notified'] = True

    pair_total = avg_up + avg_down
    edge = 1.00 - pair_total

    msg = f"""🟧 <b>LOSS-AVOID PAIR</b>
BTC 15m window
UP {up_shares} @ {avg_up:.2f}
DOWN {down_shares} @ {avg_down:.2f}
Total = {pair_total:.2f}
Giveback = {edge:.2f}
Status: RISK ELIMINATED"""
    send_telegram(msg)

def notify_hard_flatten(side, shares, pnl_impact):
    """HARD_FLATTEN notification"""
    global window_state
    if window_state.get('telegram_notified'):
        return
    window_state['telegram_notified'] = True

    msg = f"""🟥 <b>HARD FLATTEN</b>
BTC 15m window
Excess side: {side}
Shares closed: {shares}
Realized PnL impact: {pnl_impact:.2f}
Reason: Risk control"""
    send_telegram(msg)

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

def _send_pair_outcome_notification():
    """Send appropriate notification when pair completes"""
    global last_pair_type, session_counters

    if not window_state:
        return

    up_shares = window_state['filled_up_shares']
    down_shares = window_state['filled_down_shares']
    avg_up = window_state['avg_up_price_paid']
    avg_down = window_state['avg_down_price_paid']

    if up_shares == 0 or down_shares == 0:
        return

    # Skip if prices not recorded yet
    if avg_up == 0 or avg_down == 0:
        return

    pair_total = avg_up + avg_down
    min_shares = min(up_shares, down_shares)
    total_cost = (avg_up * min_shares) + (avg_down * min_shares)
    payout = min_shares * 1.00  # Winner pays $1
    profit = payout - total_cost
    profit_per_pair = 1.00 - pair_total

    if pair_total <= 1.00:
        last_pair_type = "PROFIT_PAIR"
        session_counters['profit_pairs'] += 1
        up_cost = avg_up * min_shares
        dn_cost = avg_down * min_shares
        print()
        print("╔════════════════════════════════════════════════════════════╗")
        print("║  💰💰💰 PROFIT LOCKED! 💰💰💰                               ║")
        print("╠════════════════════════════════════════════════════════════╣")
        print(f"║  UP: {min_shares:.0f} shares @ {avg_up*100:.0f}c  =  ${up_cost:.2f}".ljust(60) + "║")
        print(f"║  DN: {min_shares:.0f} shares @ {avg_down*100:.0f}c  =  ${dn_cost:.2f}".ljust(60) + "║")
        print("║  ────────────────────────────────────────                  ║")
        print(f"║  Total Cost: ${total_cost:.2f}  →  Payout: ${payout:.2f}".ljust(60) + "║")
        print(f"║  🎉 GUARANTEED PROFIT: ${profit:.2f} ({profit_per_pair*100:.0f}c per pair)".ljust(60) + "║")
        print("╚════════════════════════════════════════════════════════════╝")
        print()
        notify_profit_pair(up_shares, avg_up, down_shares, avg_down)
        log_event("PROFIT_PAIR", window_state.get('window_id', ''),
                        up_shares=min_shares, up_price=avg_up,
                        down_shares=min_shares, down_price=avg_down,
                        pnl=profit)
    else:
        last_pair_type = "LOSS_AVOID_PAIR"
        session_counters['loss_avoid_pairs'] += 1
        loss = total_cost - payout
        print()
        print("┌────────────────────────────────────────────────────────────┐")
        print("│  🟧 LOSS AVOIDED                                           │")
        print("├────────────────────────────────────────────────────────────┤")
        print(f"│  UP: {min_shares:.0f} @ {avg_up*100:.0f}c + DN: {min_shares:.0f} @ {avg_down*100:.0f}c = {pair_total*100:.0f}c".ljust(60) + "│")
        print(f"│  Cost: ${total_cost:.2f} → Payout: ${payout:.2f} | Loss capped: ${loss:.2f}".ljust(60) + "│")
        print("└────────────────────────────────────────────────────────────┘")
        print()
        notify_loss_avoid_pair(up_shares, avg_up, down_shares, avg_down)
        log_event("LOSS_AVOID", window_state.get('window_id', ''),
                        up_shares=min_shares, up_price=avg_up,
                        down_shares=min_shares, down_price=avg_down,
                        pnl=-loss)

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

def min_shares(price):
    """minShares(p) = ceil(max(5, 1/p))"""
    if price <= 0:
        return MIN_SHARES
    return math.ceil(max(MIN_SHARES, 1.0 / price))

def calc_q(bid_up, bid_down):
    """Calculate Q = max shares needed for both legs"""
    return max(min_shares(bid_up), min_shares(bid_down))

def floor_to_tick(price):
    """Floor price to nearest tick"""
    return round(int(price / TICK) * TICK, 2)

# ============================================================================
# LOGGING
# ============================================================================

def ts():
    return datetime.now(PST).strftime("%H:%M:%S")

def get_mode_display(ttc):
    imb = get_arb_imbalance()  # Use ARB imbalance (excludes 99c captures)
    if imb != 0 and ttc <= PAIR_DEADLINE_SECONDS:
        return "RISK"
    if last_pair_type == "PROFIT_PAIR":
        return "PROFIT"
    elif last_pair_type in ("LOSS_AVOID_PAIR", "HARD_FLATTEN"):
        return "SAFE"
    return "IDLE"

_last_log_time = 0
_last_skip_reason = ""

def log_state(ttc, books=None):
    """Log current state every second with prices and skip reason"""
    global _last_log_time, _last_skip_reason
    if not window_state:
        return

    # Throttle to 1 second
    now = time.time()
    if (now - _last_log_time < 1):
        return
    _last_log_time = now

    imb = get_arb_imbalance()  # Use ARB imbalance (excludes 99c captures)
    state = window_state['state']
    up_shares = window_state['filled_up_shares']
    down_shares = window_state['filled_down_shares']
    mode = get_mode_display(ttc)
    pnl = window_state['realized_pnl_usd']

    # Get current prices
    ask_up = ask_down = 0.50
    if books:
        if books.get('up_asks'):
            ask_up = float(books['up_asks'][0]['price'])
        if books.get('down_asks'):
            ask_down = float(books['down_asks'][0]['price'])

    # Determine status and reason
    if trading_halted:
        status = "HALTED"
        reason = f"ROI target reached ({ROI_HALT_THRESHOLD*100:.0f}%)"
    elif state == STATE_PAIRING:
        status = "PAIRING"
        reason = f"need {'DN' if imb > 0 else 'UP'}"
    elif up_shares > 0 or down_shares > 0:
        if imb == 0:
            # No ARB imbalance - either paired ARB or 99c capture
            if window_state.get('capture_99c_fill_notified'):
                status = "SNIPER"
                reason = f"{window_state.get('capture_99c_side', '?')} {int(up_shares + down_shares)} shares"
            else:
                status = "PAIRED"
                reason = ""
        else:
            status = "IMBAL"
            reason = ""
    else:
        status = "IDLE"
        # Figure out why we're idle
        cheap = min(ask_up, ask_down)
        expensive = max(ask_up, ask_down)
        if cheap > DIVERGENCE_THRESHOLD:
            reason = f"no diverge ({cheap*100:.0f}c>{DIVERGENCE_THRESHOLD*100:.0f}c)"
        elif expensive < MIN_EXPENSIVE_SIDE_PRICE:
            reason = f"weak ({expensive*100:.0f}c<{MIN_EXPENSIVE_SIDE_PRICE*100:.0f}c)"
        elif ttc <= PAIR_DEADLINE_SECONDS:
            reason = "too late"
        else:
            reason = _last_skip_reason if _last_skip_reason else "checking..."

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

    # Get order book imbalance
    ob_str = ""
    up_imb = None
    down_imb = None
    if ORDERBOOK_ANALYZER_AVAILABLE and orderbook_analyzer and books and ORDERBOOK_LOG_ALWAYS:
        ob_result = orderbook_analyzer.analyze(
            books.get('up_bids', []), books.get('up_asks', []),
            books.get('down_bids', []), books.get('down_asks', [])
        )
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
    if window_state.get('capture_99c_fill_notified') and not window_state.get('capture_99c_hedged'):
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
    if window_state.get('capture_99c_fill_notified') and not window_state.get('capture_99c_hedged'):
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

    # v1.46: Defense-in-depth - block ALL orders when trading is halted
    if trading_halted:
        print(f"[{ts()}] [HALT] BLOCKED: {side} @ {price*100:.0f}c x{size} - trading halted (ROI target reached)")
        return False, "HALTED: ROI target reached"

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

    # v1.46: Defense-in-depth - block ALL orders when trading is halted
    if trading_halted:
        print(f"[{ts()}] [HALT] BLOCKED: FOK SELL x{shares} - trading halted (ROI target reached)")
        return False, None, 0

    try:
        from py_clob_client.clob_types import MarketOrderArgs, OrderType
        from py_clob_client.order_builder.constants import SELL

        print(f"[{ts()}] HARD_STOP: Placing FOK market sell: {shares} shares")

        # Create market sell order
        sell_args = MarketOrderArgs(
            token_id=token_id,
            amount=shares,
            side=SELL
        )

        # Sign and post with FOK (Fill-or-Kill)
        signed_order = clob_client.create_market_order(sell_args)
        response = clob_client.post_order(signed_order, orderType=OrderType.FOK)

        # Parse response
        order_id = response.get("orderID", "unknown")
        status = response.get("status", "UNKNOWN")
        filled = float(response.get("filledAmount", 0))

        if status == "MATCHED" or filled > 0:
            print(f"[{ts()}] HARD_STOP: FOK filled {filled}/{shares} shares, order_id={order_id[:8]}...")
            log_activity("FOK_FILLED", {"order_id": order_id, "filled": filled, "requested": shares})
            return True, order_id, filled
        else:
            print(f"[{ts()}] HARD_STOP: FOK rejected, status={status}")
            log_activity("FOK_REJECTED", {"order_id": order_id, "status": status})
            return False, order_id, 0.0

    except Exception as e:
        print(f"[{ts()}] HARD_STOP_ERROR: FOK order failed: {e}")
        log_activity("FOK_ERROR", {"error": str(e)})
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

        # No bids = no trigger (can't sell into nothing)
        if not bids or len(bids) == 0:
            return False, 0.0

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


def execute_hard_stop(side: str, books: dict) -> tuple:
    """
    Execute emergency hard stop using FOK market orders.
    Keeps selling until position is completely flat.

    Args:
        side: "UP" or "DOWN" - which side we're liquidating
        books: Order book data

    Returns:
        tuple: (success, total_pnl)
    """
    global window_state

    # Query ACTUAL position from API (not tracked amount) to avoid "not enough balance" errors
    tracked_shares = window_state.get(f'capture_99c_filled_{side.lower()}', 0)
    api_pos = verify_position_from_api()

    if api_pos is not None:
        actual_shares = api_pos[0] if side == 'UP' else api_pos[1]
        if abs(actual_shares - tracked_shares) > 0.01 and actual_shares > 0:
            print(f"[{ts()}] HARD_STOP: Position mismatch - API={actual_shares:.4f} tracked={tracked_shares:.4f}, using API value")
        shares = actual_shares if actual_shares > 0 else tracked_shares
    else:
        print(f"[{ts()}] HARD_STOP: API unavailable, using tracked shares={tracked_shares}")
        shares = tracked_shares

    if shares <= 0:
        print(f"[{ts()}] HARD_STOP: No shares to sell for {side}")
        return False, 0.0

    token = window_state.get(f'{side.lower()}_token')
    entry_price = window_state.get('capture_99c_fill_price', 0.99)

    remaining_shares = shares
    total_pnl = 0.0
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
            # Refresh order books
            if window_state.get('cached_market'):
                books = get_order_books(window_state['cached_market'])
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
        else:
            print(f"[{ts()}] HARD_STOP: FOK rejected, trying again (attempt {attempts})")
            time.sleep(0.5)

    if remaining_shares > 0:
        print(f"[{ts()}] HARD_STOP_ERROR: Failed to fully liquidate! {remaining_shares:.0f} shares stuck")
        # Still update state with partial exit
        window_state[f'capture_99c_filled_{side.lower()}'] = remaining_shares
        return False, total_pnl

    # Full liquidation successful
    print()
    print("=" * 50)
    print(f"{'='*15} HARD STOP COMPLETE {'='*15}")
    print(f"Total P&L: ${total_pnl:.2f}")
    print("=" * 50)

    # Log to Sheets
    log_event("HARD_STOP_EXIT", window_state.get('window_id', ''),
                    side=side, shares=shares, price=best_bid,
                    pnl=total_pnl, reason="hard_stop_60c",
                    details=f"trigger={HARD_STOP_TRIGGER*100:.0f}c")

    # Telegram notification
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

    # Update state
    window_state['capture_99c_exited'] = True
    window_state['capture_99c_exit_reason'] = 'hard_stop_60c'
    window_state[f'capture_99c_filled_{side.lower()}'] = 0

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

def get_verified_fill_price(slug, side, fallback_price):
    """Query Polymarket /trades API for actual execution price (not limit order price)."""
    try:
        resp = http_session.get(
            "https://data-api.polymarket.com/trades",
            params={"user": WALLET_ADDRESS, "limit": 20, "side": "BUY"},
            timeout=5
        )
        resp.raise_for_status()
        trades = resp.json()
        if not isinstance(trades, list):
            return fallback_price
        for t in trades:
            if t.get("slug") == slug and t.get("outcome", "").upper() == side:
                verified = float(t.get("price", 0))
                if 0 < verified <= 1.0:
                    if abs(verified - fallback_price) > 0.001:
                        print(f"[{ts()}] PRICE_VERIFY: {slug} {side} order={fallback_price:.2f} actual={verified:.2f}")
                    return verified
    except Exception as e:
        print(f"[{ts()}] PRICE_VERIFY_ERROR: {e}")
    return fallback_price

def check_both_orders_fast(up_order_id, down_order_id):
    with ThreadPoolExecutor(max_workers=2) as ex:
        up_future = ex.submit(get_order_status, up_order_id)
        down_future = ex.submit(get_order_status, down_order_id)
        up_status = up_future.result(timeout=5)
        down_status = down_future.result(timeout=5)
    return up_status, down_status

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
# BUG FIX: VERIFIED POSITION WITH RETRY
# ============================================================================

def get_verified_position():
    """Get position with retry and validation - Bug Fix #2"""
    for attempt in range(ORDER_VERIFY_RETRIES):
        api_pos = verify_position_from_api()
        if api_pos is not None:
            return api_pos
        time.sleep(1.0)
    print(f"[{ts()}] POSITION_VERIFY_FAILED after {ORDER_VERIFY_RETRIES} attempts")
    return None

def get_verified_fills():
    """
    DUAL-SOURCE VERIFICATION: Check BOTH order status AND position API.
    Uses max() across all sources - fills can only increase, never decrease.
    """
    global window_state

    # Source 1: Check order status for pending arb orders
    order_up_filled = 0
    order_down_filled = 0

    if window_state.get('current_arb_orders'):
        arb = window_state['current_arb_orders']
        if arb.get('up_id'):
            up_status = get_order_status(arb['up_id'])
            order_up_filled = up_status.get('filled', 0)
        if arb.get('down_id'):
            down_status = get_order_status(arb['down_id'])
            order_down_filled = down_status.get('filled', 0)

    # Source 2: Position API
    api_up, api_down = 0, 0
    pos = verify_position_from_api()
    if pos:
        api_up, api_down = pos

    # Source 3: Local tracking
    local_up = window_state.get('filled_up_shares', 0)
    local_down = window_state.get('filled_down_shares', 0)

    # Use MAXIMUM from all sources - fills can only increase
    verified_up = max(order_up_filled, api_up, local_up)
    verified_down = max(order_down_filled, api_down, local_down)

    # Log if sources disagree (for debugging)
    sources_match = (order_up_filled == api_up == local_up) and (order_down_filled == api_down == local_down)
    if not sources_match and (order_up_filled > 0 or order_down_filled > 0 or api_up > 0 or api_down > 0):
        print(f"[{ts()}] DUAL_VERIFY: order=({order_up_filled:.1f}/{order_down_filled:.1f}) api=({api_up:.1f}/{api_down:.1f}) local=({local_up:.1f}/{local_down:.1f}) -> ({verified_up:.1f}/{verified_down:.1f})")

    return verified_up, verified_down

def wait_and_sync_position():
    """Wait for order to settle and sync position - Bug Fix #3"""
    global window_state
    print(f"[{ts()}] Waiting {ORDER_SETTLE_DELAY}s for settlement...")
    time.sleep(ORDER_SETTLE_DELAY)

    pos = get_verified_position()
    if pos:
        old_up = window_state['filled_up_shares']
        old_down = window_state['filled_down_shares']
        window_state['filled_up_shares'] = pos[0]
        window_state['filled_down_shares'] = pos[1]
        if pos[0] != old_up or pos[1] != old_down:
            print(f"[{ts()}] POSITION_SYNCED: UP={old_up}->{pos[0]} DN={old_down}->{pos[1]}")
        else:
            print(f"[{ts()}] Position unchanged: UP={pos[0]} DN={pos[1]}")

    window_state['last_order_time'] = time.time()
    save_trades()

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
    # v1.46: Defense-in-depth - block ALL orders when trading is halted
    if trading_halted:
        print(f"[{ts()}] [HALT] BLOCKED: {side} @ {price*100:.0f}c x{size} - trading halted (ROI target reached)")
        return False, None, "HALTED"

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

# ============================================================================
# HYBRID LOSS PROTECTION: HEDGE ESCALATION
# ============================================================================

def calculate_hedge_price(fill_price, seconds_since_fill):
    """Calculate max acceptable hedge price based on escalating tolerance.

    Returns:
        tuple: (max_hedge_price, tolerance_cents)
        - max_hedge_price: Max price we're willing to pay for hedge (decimal, e.g. 0.42)
        - tolerance_cents: Current tolerance in cents (e.g. 2 for 2c tolerance)
    """
    # Profit target: 99c combined (1c profit)
    profit_target_price = 0.99 - fill_price  # e.g., 0.99 - 0.57 = 0.42 (42c)

    # Find current tolerance based on time elapsed
    tolerance_cents = 0
    for threshold_secs, tol_cents in HEDGE_ESCALATION:
        if seconds_since_fill >= threshold_secs:
            tolerance_cents = tol_cents  # e.g. 2 for 2c tolerance

    # Convert tolerance to decimal
    tolerance = tolerance_cents / 100.0

    # Max hedge = profit target + tolerance
    max_hedge = profit_target_price + tolerance

    # Cap at 50c max
    return min(max_hedge, HEDGE_PRICE_CAP), tolerance_cents

def should_trigger_bail(fill_price, current_ask, seconds_since_fill, ttc, is_hedged):
    """Check if bail mode should trigger"""
    # If already hedged (paired), no need to bail
    if is_hedged:
        return False, None

    # Trigger 1: Unhedged too long AND hedge would exceed cap
    hedge_needed, _ = calculate_hedge_price(fill_price, seconds_since_fill)
    if seconds_since_fill > BAIL_UNHEDGED_TIMEOUT and hedge_needed >= HEDGE_PRICE_CAP:
        return True, "UNHEDGED_TIMEOUT_HIGH_HEDGE"

    # Trigger 2: <90 seconds until close AND still unhedged
    if ttc <= BAIL_TIME_REMAINING:
        return True, "TIME_CRITICAL"

    # Trigger 3: Position down >5% (current ask much higher than fill = bad)
    if fill_price > 0:
        # If we bought at 40c and now ask is 50c, we'd have to pay 10c more = 25% loss
        potential_loss_pct = (current_ask - fill_price) / fill_price
        if potential_loss_pct > BAIL_LOSS_THRESHOLD:
            return True, f"LOSS_EXCEEDED_{int(BAIL_LOSS_THRESHOLD*100)}PCT"

    return False, None

def execute_bail(side, shares, token, books):
    """Cancel orders and sell at market - Emergency Exit"""
    global window_state, session_counters

    print()
    print("🚨" * 25)
    print(f"🚨 BAIL MODE TRIGGERED")
    print(f"🚨 Selling {shares} {side} shares at market")
    print("🚨" * 25)

    # Cancel all pending orders first
    cancel_all_orders()

    # Get best bid for immediate exit
    if side == "UP":
        bids = books.get('up_bids', [])
    else:
        bids = books.get('down_bids', [])

    if not bids:
        print(f"[{ts()}] BAIL_FAILED: No bids available for {side}")
        return False

    best_bid = float(bids[0]['price'])
    print(f"[{ts()}] BAIL_SELL: {shares} {side} @ {best_bid*100:.0f}c")

    # Place market sell order
    success, order_id = place_limit_order(token, best_bid, shares, "SELL")

    if success:
        time.sleep(1.0)
        status = get_order_status(order_id)
        filled = status.get('filled', 0)
        print(f"[{ts()}] BAIL_RESULT: Sold {filled}/{shares} shares")

        # Update position
        if side == "UP":
            window_state['filled_up_shares'] -= filled
        else:
            window_state['filled_down_shares'] -= filled

        session_counters['hard_flattens'] += 1
        save_trades()
        return True

    print(f"[{ts()}] BAIL_ORDER_FAILED")
    return False


def execute_99c_early_exit(side: str, trigger_value: float, books: dict, reason: str = "ob_reversal") -> bool:
    """Exit 99c position early due to OB reversal.

    Triggered when:
    - OB imbalance is negative for 3 consecutive ticks (reason="ob_reversal")

    NOTE: Price-based exits are now handled by execute_hard_stop() using FOK orders.
    This function is only used for OB-based early exits and uses limit orders.

    Args:
        side: "UP" or "DOWN" - which side we bet on
        trigger_value: OB imbalance value
        books: Order book data
        reason: "ob_reversal" (price_stop is deprecated, use hard_stop)

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
            success, pnl = execute_hard_stop(side, books)
            return success

    shares = window_state.get(f'capture_99c_filled_{side.lower()}', 0)
    if shares <= 0:
        print(f"[{ts()}] EARLY_EXIT: No shares to sell for {side}")
        return False

    # Get best bid, but enforce minimum exit price
    bids = books.get(f'{side.lower()}_bids', [])
    if not bids:
        print(f"[{ts()}] ABORT_EXIT: No bids available for {side}")
        return False

    best_bid = float(bids[0]['price'])

    # Use hard stop floor instead of legacy floor
    effective_floor = HARD_STOP_FLOOR if HARD_STOP_ENABLED else PRICE_STOP_FLOOR
    if best_bid < effective_floor:
        print(f"[{ts()}] ABORT_EXIT: Bid {best_bid*100:.0f}c below floor {effective_floor*100:.0f}c")
        return False

    exit_price = best_bid
    token = window_state.get(f'{side.lower()}_token')

    # Different emoji for price stop vs OB exit
    emoji = "🛑" if reason == "price_stop" else "🚨"
    label = "PRICE STOP" if reason == "price_stop" else "OB EXIT"

    print()
    print(f"{emoji}" * 20)
    print(f"{emoji} 99c {label} TRIGGERED")
    print(f"{emoji} Selling {shares:.0f} {side} shares @ {exit_price*100:.0f}c")
    if reason == "price_stop":
        print(f"{emoji} Price dropped to: {trigger_value*100:.0f}c")
    else:
        print(f"{emoji} OB Reading: {trigger_value:+.2f}")
    print(f"{emoji}" * 20)

    # Place sell order
    success, order_id = place_limit_order(token, exit_price, shares, "SELL")

    if not success:
        print(f"[{ts()}] {label}: Order failed to place")
        return False

    # CRITICAL: Wait for order confirmation
    time.sleep(1.0)
    status = get_order_status(order_id)
    filled = status.get('filled', 0)

    if filled < shares * 0.9:  # Require at least 90% filled
        print(f"[{ts()}] {label}: Only {filled:.1f}/{shares:.0f} filled, order may be partial")
        # Don't return False - partial exit is better than no exit

    # Calculate P&L
    entry_price = window_state.get('capture_99c_fill_price', 0.99)
    pnl = (exit_price - entry_price) * filled

    print(f"[{ts()}] {label}: Sold {filled:.0f} @ {exit_price*100:.0f}c (entry {entry_price*100:.0f}c)")
    print(f"[{ts()}] {label}: P&L = ${pnl:.2f}")

    # Log to Sheets - use different event type for price stop
    event_type = "99C_PRICE_STOP" if reason == "price_stop" else "99C_EARLY_EXIT"
    if reason == "price_stop":
        details = f"trigger={trigger_value*100:.0f}c"
    else:
        details = f"OB={trigger_value:.2f}"

    log_event(event_type, window_state.get('window_id', ''),
                    side=side, shares=filled, price=exit_price,
                    pnl=pnl, reason=reason, details=details)

    # Telegram notification
    if reason == "price_stop":
        msg = f"""🛑 <b>99c PRICE STOP</b>
Side: {side}
Shares: {filled:.0f}
Trigger: {trigger_value*100:.0f}c
Exit: {exit_price*100:.0f}c
Entry: {entry_price*100:.0f}c
P&L: ${pnl:.2f}
<i>Price floor triggered</i>"""
    else:
        msg = f"""🚨 <b>99c EARLY EXIT</b>
Side: {side}
Shares: {filled:.0f}
Exit Price: {exit_price*100:.0f}c
Entry Price: {entry_price*100:.0f}c
OB Reading: {trigger_value:+.2f}
P&L: ${pnl:.2f}
<i>Exited early to cut losses</i>"""
    send_telegram(msg)

    # Update state
    window_state['capture_99c_exited'] = True
    window_state['capture_99c_exit_reason'] = reason
    window_state[f'capture_99c_filled_{side.lower()}'] = 0

    return True


def bail_vs_hedge_decision(filled_side, filled_price, filled_shares, books):
    """
    Decide whether to HEDGE (buy other side) or BAIL (sell filled side).
    Used after EARLY_HEDGE_TIMEOUT to minimize exposure.

    Returns: ("HEDGE", price) or ("BAIL", price) or ("WAIT", None)
    """
    other_side = "DOWN" if filled_side == "UP" else "UP"

    # Get current market prices
    if filled_side == "UP":
        hedge_asks = books.get('down_asks', [])
        bail_bids = books.get('up_bids', [])
    else:
        hedge_asks = books.get('up_asks', [])
        bail_bids = books.get('down_bids', [])

    if not hedge_asks or not bail_bids:
        return ("WAIT", None)

    hedge_ask = float(hedge_asks[0]['price'])
    bail_bid = float(bail_bids[0]['price'])

    # Calculate losses
    # Target hedge price = 0.99 - filled_price (break-even for arb)
    target_hedge = 0.99 - filled_price
    hedge_loss = max(0, hedge_ask - target_hedge)  # How much worse than break-even
    bail_loss = max(0, filled_price - bail_bid)    # How much we lose by selling

    print(f"[{ts()}] BAIL_VS_HEDGE: filled {filled_side}@{filled_price*100:.0f}c | "
          f"hedge {other_side}@{hedge_ask*100:.0f}c (loss={hedge_loss*100:.1f}c) | "
          f"bail@{bail_bid*100:.0f}c (loss={bail_loss*100:.1f}c)")

    # Decision logic:
    # 1. If hedge is at/near target (<=2c loss), always hedge
    if hedge_loss <= 0.02:
        return ("HEDGE", hedge_ask)

    # 2. If bail is within tolerance AND cheaper than hedge, bail
    if bail_loss <= EARLY_BAIL_MAX_LOSS and bail_loss < hedge_loss:
        return ("BAIL", bail_bid)

    # 3. If hedge is within tolerance (even if more than bail), hedge
    if hedge_loss <= EARLY_BAIL_MAX_LOSS:
        return ("HEDGE", hedge_ask)

    # 4. Both too expensive - wait and let normal escalation handle it
    return ("WAIT", None)


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
    This is a single-side bet, not an arb.
    """
    global window_state, session_counters

    shares = int(CAPTURE_99C_MAX_SPEND / CAPTURE_99C_BID_PRICE)  # ~5 shares
    token = window_state['up_token'] if side == 'UP' else window_state['down_token']

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
        window_state['capture_99c_used'] = True
        window_state['capture_99c_order'] = order_id
        window_state['capture_99c_side'] = side
        window_state['capture_99c_shares'] = shares
        # Track captured shares to exclude from pairing logic
        if side == 'UP':
            window_state['capture_99c_filled_up'] = shares
        else:
            window_state['capture_99c_filled_down'] = shares
        print(f"🔭 99c CAPTURE: Order placed, watching for fill... (${shares * 0.01:.2f} potential profit)")
        print()
        log_event("CAPTURE_99C", window_state.get('window_id', ''),
                        side=side, price=CAPTURE_99C_BID_PRICE, shares=shares,
                        confidence=confidence, penalty=penalty, ttl=ttc)
        return True
    else:
        print(f"🎰 99c CAPTURE: ❌ Failed - {status}")
        print()
        return False


def check_99c_capture_hedge(books, ttc):
    """Monitor 99c capture position and hedge if danger score exceeds threshold."""
    global window_state

    # Guards
    if not CAPTURE_99C_HEDGE_ENABLED:
        return
    if not window_state.get('capture_99c_fill_notified'):
        return  # Not filled yet
    if window_state.get('capture_99c_hedged'):
        return  # Already hedged

    bet_side = window_state.get('capture_99c_side')
    if not bet_side:
        return

    # Get current ask for our bet side
    if bet_side == "UP":
        current_ask = float(books['up_asks'][0]['price']) if books.get('up_asks') else 0
        opposite_asks = books.get('down_asks', [])
        opposite_token = window_state['down_token']
        opposite_side = "DOWN"
    else:
        current_ask = float(books['down_asks'][0]['price']) if books.get('down_asks') else 0
        opposite_asks = books.get('up_asks', [])
        opposite_token = window_state['up_token']
        opposite_side = "UP"

    if not opposite_asks:
        return

    # Get danger score from window_state (calculated by main loop)
    danger_score = window_state.get('danger_score', 0)

    # Check if we should hedge based on danger score
    if danger_score >= DANGER_THRESHOLD:
        opposite_ask = float(opposite_asks[0]['price'])
        shares = window_state.get('capture_99c_shares', 0)

        if shares > 0 and opposite_ask < 0.50:  # Don't hedge if opposite too expensive
            print()
            print(f"┌─────────────── 99c HEDGE TRIGGERED ───────────────┐")
            print(f"│  Danger score: {danger_score:.2f} >= {DANGER_THRESHOLD:.2f} threshold".ljust(50) + "│")
            print(f"│  Bet: {bet_side} @ 99c".ljust(50) + "│")
            print(f"│  Hedging: {shares} {opposite_side} @ {opposite_ask*100:.0f}c".ljust(50) + "│")
            print(f"└───────────────────────────────────────────────────┘")

            # Place hedge order at market (take the ask)
            success, order_id, status = place_and_verify_order(opposite_token, opposite_ask, shares)
            if success:
                combined = 0.99 + opposite_ask
                loss_per_share = combined - 1.00
                total_loss = loss_per_share * shares

                window_state['capture_99c_hedged'] = True
                window_state['capture_99c_hedge_price'] = opposite_ask

                # Track hedge shares in filled counts
                if opposite_side == "UP":
                    window_state['filled_up_shares'] = max(window_state.get('filled_up_shares', 0), shares)
                    window_state['capture_99c_filled_up'] = max(window_state.get('capture_99c_filled_up', 0), shares)
                else:
                    window_state['filled_down_shares'] = max(window_state.get('filled_down_shares', 0), shares)
                    window_state['capture_99c_filled_down'] = max(window_state.get('capture_99c_filled_down', 0), shares)

                print(f"│  ✅ HEDGED: Combined {combined*100:.0f}c | Loss: ${abs(total_loss):.2f}".ljust(50) + "│")
                print(f"└───────────────────────────────────────────────────┘")
                print()

                # LOG-02: Log hedge event with full signal breakdown
                danger_result = window_state.get('danger_result', {})
                log_event("99C_HEDGE", window_state.get('window_id', ''),
                               bet_side=bet_side, hedge_side=opposite_side,
                               hedge_price=opposite_ask, combined=combined,
                               pnl=-abs(total_loss),  # Record loss in PnL column
                               danger_score=danger_score,
                               conf_drop=danger_result.get('confidence_drop', 0),
                               conf_wgt=danger_result.get('confidence_component', 0),
                               imb_raw=danger_result.get('imbalance', 0),
                               imb_wgt=danger_result.get('imbalance_component', 0),
                               vel_raw=danger_result.get('velocity', 0),
                               vel_wgt=danger_result.get('velocity_component', 0),
                               opp_raw=danger_result.get('opponent_ask', 0),
                               opp_wgt=danger_result.get('opponent_component', 0),
                               time_raw=danger_result.get('time_remaining', 0),
                               time_wgt=danger_result.get('time_component', 0))
            else:
                print(f"│  ❌ HEDGE FAILED: {status}".ljust(50) + "│")
                print(f"└───────────────────────────────────────────────────┘")
                print()


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
# ARB QUOTING WITH SMART SIGNALS
# ============================================================================

def check_and_place_arb(books, ttc):
    """
    Check for arb opportunity and place orders.
    NOW WITH SMART SIGNALS: Multi-timeframe momentum and volatility filtering.
    """
    global window_state, session_counters, _last_skip_reason

    # ONE ARB PER WINDOW guard
    if window_state.get('arb_placed_this_window'):
        return False

    # ENTRY TIME RESTRICTION - Never enter with <5 minutes remaining
    if ttc < MIN_TIME_FOR_ENTRY:
        _last_skip_reason = f"late entry ({ttc:.0f}s<{MIN_TIME_FOR_ENTRY}s)"
        return False

    # Position verification using improved function
    api_position = get_verified_position()
    if api_position:
        api_up, api_down = api_position
        local_up = window_state['filled_up_shares']
        local_down = window_state['filled_down_shares']
        if api_up != local_up or api_down != local_down:
            # Use MAX - fills can only increase, never decrease (API may be stale)
            new_up = max(local_up, api_up)
            new_down = max(local_down, api_down)
            print(f"🔄 PRE-ARB POSITION SYNC: local=({local_up},{local_down}) api=({api_up},{api_down}) -> ({new_up},{new_down})")
            window_state['filled_up_shares'] = new_up
            window_state['filled_down_shares'] = new_down
            save_trades()
            # Only return False if position actually changed (prevents infinite loop)
            if new_up != local_up or new_down != local_down:
                return False

    # Use ARB imbalance (excludes 99c capture shares) - don't pair for 99c captures
    imb = get_arb_imbalance()
    if imb != 0:
        print(f"[{ts()}] BLOCK_NEW_ARBS arb_imbalance={imb} -> entering PAIRING_MODE")
        window_state['state'] = STATE_PAIRING
        window_state['pairing_start_time'] = time.time()
        window_state['best_distance_seen'] = float('inf')
        log_event("PAIRING_ENTRY", window_state.get('window_id', ''), imbalance=imb, reason="imbalance_detected")
        return False

    if ttc <= PAIR_DEADLINE_SECONDS:
        return False

    up_asks = books['up_asks']
    down_asks = books['down_asks']

    if not up_asks or not down_asks:
        return False

    ask_up = float(up_asks[0]['price'])
    ask_down = float(down_asks[0]['price'])

    # Skip pinned
    if ask_up <= PINNED_ASK_LIMIT or ask_down <= PINNED_ASK_LIMIT:
        return False

    # ===========================================
    # STRONG DIVERGENCE CHECK
    # Requires: cheap side <= 42c AND expensive side >= 58c
    # ===========================================
    cheap_price = min(ask_up, ask_down)
    expensive_price = max(ask_up, ask_down)

    # Check 1: Cheap side must be cheap enough
    if cheap_price > DIVERGENCE_THRESHOLD:
        return False  # Both sides near 50/50

    # Check 2: Expensive side must show clear momentum (NEW)
    if expensive_price < MIN_EXPENSIVE_SIDE_PRICE:
        print(f"[{ts()}] SKIP_WEAK_DIVERGENCE: {cheap_price*100:.0f}c/{expensive_price*100:.0f}c - need {MIN_EXPENSIVE_SIDE_PRICE*100:.0f}c+ on expensive side")
        return False

    cheap_side = "UP" if ask_up <= DIVERGENCE_THRESHOLD else "DOWN"
    print(f"[{ts()}] STRONG_DIVERGENCE: {cheap_side} @ {cheap_price*100:.0f}c, other @ {expensive_price*100:.0f}c")

    # ===========================================
    # SMART SIGNAL CHECK (NEW)
    # ===========================================
    size_multiplier = 1.0

    if USE_SMART_SIGNALS and STRATEGY_SIGNALS_AVAILABLE:
        # Update BTC price
        btc_price = get_btc_price_from_coinbase()
        if btc_price:
            update_btc_price(btc_price)

        # Get smart signal
        signal = get_signal(ask_up, ask_down, ttc / 60.0)

        momentum = signal.momentum
        vol = signal.volatility
        print(f"[{ts()}] SIGNAL: {signal.direction} {signal.confidence}% | Mom: 1m={momentum.momentum_1m*100:+.1f}% 5m={momentum.momentum_5m*100:+.1f}% | {signal.reason}")

        # Store confidence for Telegram notification
        window_state['smart_signal_confidence'] = signal.confidence

        # Decision: Skip if signal says don't trade
        if not signal.should_trade:
            _last_skip_reason = f"no momentum ({signal.confidence}%)"
            session_counters['smart_skips'] += 1
            log_activity("SMART_SKIP", {
                "reason": signal.reason,
                "confidence": signal.confidence,
                "direction": signal.direction,
                "up_ask": ask_up,
                "down_ask": ask_down,
                "ttl": ttc
            })
            return False

        # Skip choppy markets
        if SMART_SIGNAL_SKIP_CHOPPY and signal.volatility.is_choppy:
            _last_skip_reason = "choppy market"
            session_counters['smart_skips'] += 1
            return False

        # Check minimum confidence
        if signal.confidence < SMART_SIGNAL_MIN_CONFIDENCE:
            _last_skip_reason = f"low conf ({signal.confidence}%<{SMART_SIGNAL_MIN_CONFIDENCE}%)"
            session_counters['smart_skips'] += 1
            return False

        # Get position size multiplier
        if USE_CONFIDENCE_SIZING:
            size_multiplier = get_position_size_multiplier(signal.confidence, signal.volatility)
            print(f"[{ts()}] ✅ SMART SIGNAL APPROVED | Size multiplier: {size_multiplier:.2f}x")
        else:
            print(f"[{ts()}] ✅ SMART SIGNAL APPROVED")

        session_counters['smart_trades'] += 1
        log_activity("SMART_TRADE_APPROVED", {
            "confidence": signal.confidence,
            "direction": signal.direction,
            "size_multiplier": size_multiplier,
            "up_ask": ask_up,
            "down_ask": ask_down,
            "ttl": ttc
        })

    # ===========================================
    # ORDER BOOK IMBALANCE CHECK
    # ===========================================
    if USE_ORDERBOOK_SIGNALS and ORDERBOOK_ANALYZER_AVAILABLE and orderbook_analyzer:
        ob_result = orderbook_analyzer.analyze(
            books.get('up_bids', []), books.get('up_asks', []),
            books.get('down_bids', []), books.get('down_asks', [])
        )

        signal_side = ob_result.get('signal')  # BUY_UP, BUY_DOWN, or None
        strength = ob_result.get('strength')   # STRONG, MODERATE, WEAK, or None
        trend = ob_result.get('trend')         # TREND_UP, TREND_DOWN, or None
        up_imb = ob_result.get('up_imbalance', 0)
        down_imb = ob_result.get('down_imbalance', 0)

        print(f"[{ts()}] ORDERBOOK: UP={up_imb:+.2f} DN={down_imb:+.2f} | Signal: {signal_side or '-'} | Strength: {strength or '-'} | Trend: {trend or '-'}")

        # Check if imbalance aligns with cheap side
        if signal_side:
            imbalance_direction = "UP" if signal_side == "BUY_UP" else "DOWN"
            price_direction = cheap_side

            if imbalance_direction == price_direction:
                print(f"[{ts()}] ✅ ORDERBOOK CONFIRMS: {signal_side} aligns with cheap side ({cheap_side})")
            else:
                print(f"[{ts()}] ⚠️  ORDERBOOK DIVERGES: {signal_side} vs price direction ({cheap_side})")
                # Optional: Could skip trade if orderbook disagrees
                # if ORDERBOOK_REQUIRE_ALIGNMENT:
                #     return False

        # Check minimum strength requirement
        strength_levels = {"WEAK": 1, "MODERATE": 2, "STRONG": 3}
        min_strength = strength_levels.get(ORDERBOOK_MIN_SIGNAL_STRENGTH, 2)
        current_strength = strength_levels.get(strength, 0)

        if ORDERBOOK_REQUIRE_TREND and trend is None:
            print(f"[{ts()}] ⚠️  NO TREND CONFIRMED (need {orderbook_analyzer.history_size // 6}+ consistent readings)")
        elif strength and current_strength >= min_strength:
            print(f"[{ts()}] ✅ ORDERBOOK STRENGTH OK: {strength} >= {ORDERBOOK_MIN_SIGNAL_STRENGTH}")

    # Compute bids
    bid_up = floor_to_tick(ask_up - TICK)
    bid_down = floor_to_tick(ask_down - TICK)

    if bid_up < MIN_PRICE or bid_up >= ask_up:
        return False
    if bid_down < MIN_PRICE or bid_down >= ask_down:
        return False

    total = bid_up + bid_down
    if total > LOCK_MAX:
        print(f"[{ts()}] NO_PAIR SUM_EXCEEDS_LOCK_MAX")
        return False

    locked_profit = 1.00 - total
    quote_ttl_ms = TTL_2C_MS if locked_profit >= 0.02 else TTL_1C_MS

    # Calculate Q with optional size multiplier
    base_q = calc_q(bid_up, bid_down)
    q = max(MIN_SHARES, min(FAILSAFE_MAX_SHARES, int(base_q * size_multiplier)))

    print()
    print("📝" * 25)
    print(f"📝 ARB OPPORTUNITY FOUND")
    print(f"📝 UP @ {bid_up*100:.0f}c + DOWN @ {bid_down*100:.0f}c = {total*100:.0f}c")
    print(f"📝 Locked Profit: {locked_profit*100:.0f}c per share | Shares: {q}")
    if size_multiplier != 1.0:
        print(f"📝 (Size adjusted from {base_q} by {size_multiplier:.2f}x)")
    print("📝" * 25)

    # MOMENTUM-FIRST STRATEGY
    if MOMENTUM_FIRST_ENABLED:
        if bid_up >= bid_down:
            first_side, first_token, first_bid = "UP", books['up_token'], bid_up
            second_side, second_token, second_bid = "DOWN", books['down_token'], bid_down
        else:
            first_side, first_token, first_bid = "DOWN", books['down_token'], bid_down
            second_side, second_token, second_bid = "UP", books['up_token'], bid_up

        print(f"🚀 MOMENTUM-FIRST: {first_side} has momentum ({first_bid*100:.0f}c > {second_bid*100:.0f}c)")

        # Place first side with verification (Bug Fix #1)
        success_first, order_first, status_first = place_and_verify_order(first_token, first_bid, q)
        if not success_first:
            print(f"📝 ORDER {first_side}: ❌ FAILED ({status_first})")
            return False

        print(f"📝 ORDER {first_side}: ✅ {q} shares @ {first_bid*100:.0f}c")
        window_state['first_order_time'] = time.time()  # Track when first leg was placed
        log_event("ARB_ORDER", window_state.get('window_id', ''), side=first_side, price=first_bid, shares=q,
                        locked_profit=locked_profit)

        # Wait for fill
        print(f"⏳ Waiting for {first_side} to fill...")
        first_filled = False
        first_fill_shares = 0
        for i in range(25):
            time.sleep(0.2)
            status = get_order_status(order_first)
            if status['filled'] >= q * 0.9:
                first_filled = True
                first_fill_shares = status['filled']
                print(f"✅ {first_side} FILLED: {first_fill_shares} shares")
                log_event("ARB_FILL", window_state.get('window_id', ''), side=first_side, shares=first_fill_shares, price=first_bid)
                window_state['arb_placed_this_window'] = True  # Prevent duplicate arb attempts after first fill
                break
            if i % 5 == 4:
                print(f"⏳ {first_side}: {status['filled']}/{q} filled...")

        if not first_filled:
            print(f"⚠️ {first_side} didn't fill - canceling")
            cancel_all_orders()
            return False

        # Place second side with verification (Bug Fix #1)
        print(f"🎯 {first_side} FILLED! Placing {second_side}...")
        success_second, order_second, status_second = place_and_verify_order(second_token, second_bid, q)

        if not success_second:
            print(f"📝 ORDER {second_side}: ❌ FAILED ({status_second})")
            # One side filled, need to track
            if first_side == "UP":
                window_state['filled_up_shares'] = first_fill_shares
                window_state['avg_up_price_paid'] = first_bid
            else:
                window_state['filled_down_shares'] = first_fill_shares
                window_state['avg_down_price_paid'] = first_bid

            # === FIX: Enter PAIRING_MODE to complete hedge ===
            print(f"⚠️ ONE-LEG FILL! Entering PAIRING_MODE to hedge {first_side}")
            window_state['state'] = STATE_PAIRING
            window_state['pairing_start_time'] = time.time()
            window_state['best_distance_seen'] = float('inf')
            window_state['arb_placed_this_window'] = True  # Prevent new arb attempts
            log_event("PAIRING_ENTRY", window_state.get('window_id', ''),
                           imbalance=first_fill_shares, reason="second_order_failed")
            return False  # Main loop will call run_pairing_mode()
        else:
            print(f"📝 ORDER {second_side}: ✅ {q} shares @ {second_bid*100:.0f}c")

            if first_side == "UP":
                window_state['filled_up_shares'] = first_fill_shares
                window_state['avg_up_price_paid'] = first_bid
                window_state['open_up_order_ids'].append(order_first)
                window_state['open_down_order_ids'].append(order_second)
            else:
                window_state['filled_down_shares'] = first_fill_shares
                window_state['avg_down_price_paid'] = first_bid
                window_state['open_down_order_ids'].append(order_first)
                window_state['open_up_order_ids'].append(order_second)

            success_up = True
            success_down = True
            order_up = order_first if first_side == "UP" else order_second
            order_down = order_first if first_side == "DOWN" else order_second

    else:
        # Simultaneous placement
        with ThreadPoolExecutor(max_workers=2) as ex:
            up_future = ex.submit(place_limit_order, books['up_token'], bid_up, q)
            down_future = ex.submit(place_limit_order, books['down_token'], bid_down, q)
            success_up, order_up = up_future.result(timeout=3)
            success_down, order_down = down_future.result(timeout=3)

        if success_up:
            window_state['open_up_order_ids'].append(order_up)
        if success_down:
            window_state['open_down_order_ids'].append(order_down)

    if success_up and success_down:
        window_state['current_arb_orders'] = {
            'up_id': order_up,
            'down_id': order_down,
            'bid_up': bid_up,
            'bid_down': bid_down,
            'q': q,
            'ttl_ms': quote_ttl_ms,
            'locked_profit': locked_profit
        }
        window_state['arb_order_time'] = time.time()
        window_state['arb_placed_this_window'] = True

        # AGGRESSIVE COMPLETION with position verification (Bug Fix #3)
        if AGGRESSIVE_COMPLETION_ENABLED:
            print(f"⏳ AGGRESSIVE COMPLETION: Waiting {ORDER_SETTLE_DELAY}s for settlement...")
            time.sleep(ORDER_SETTLE_DELAY)

            # DUAL-SOURCE VERIFICATION: Check both order status AND position API
            verified_up, verified_down = get_verified_fills()
            window_state['filled_up_shares'] = verified_up
            window_state['filled_down_shares'] = verified_down
            print(f"[{ts()}] POSITION_VERIFIED: UP={verified_up:.1f} DN={verified_down:.1f}")

            up_filled = window_state['filled_up_shares'] >= q * 0.9
            down_filled = window_state['filled_down_shares'] >= q * 0.9

            if up_filled and down_filled:
                print("💰 BOTH FILLED - PAIR COMPLETE!")
                window_state['avg_up_price_paid'] = bid_up
                window_state['avg_down_price_paid'] = bid_down
                notify_profit_pair(window_state['filled_up_shares'], bid_up,
                                   window_state['filled_down_shares'], bid_down)
                cancel_all_orders()
                window_state['current_arb_orders'] = None
                window_state['state'] = STATE_DONE
                save_trades()
                return True

        return True
    else:
        cancel_all_orders()
        return False

# ============================================================================
# MONITOR ARB ORDERS (simplified - keeping core logic)
# ============================================================================

def monitor_arb_orders(books):
    """Monitor pending arb orders for fills"""
    global window_state

    if not window_state['current_arb_orders']:
        return

    arb = window_state['current_arb_orders']
    elapsed_ms = (time.time() - window_state['arb_order_time']) * 1000

    up_status = get_order_status(arb['up_id'])
    down_status = get_order_status(arb['down_id'])

    up_filled = up_status['filled']
    down_filled = down_status['filled']

    if up_filled > window_state['filled_up_shares']:
        window_state['filled_up_shares'] = up_filled
        window_state['avg_up_price_paid'] = arb['bid_up']
        print(f"🔵 UP FILL! {up_filled} @ {arb['bid_up']*100:.0f}c")

    if down_filled > window_state['filled_down_shares']:
        window_state['filled_down_shares'] = down_filled
        window_state['avg_down_price_paid'] = arb['bid_down']
        print(f"🟠 DOWN FILL! {down_filled} @ {arb['bid_down']*100:.0f}c")

    if up_filled > 0 or down_filled > 0:
        save_trades()

    imb = get_imbalance()

    if up_status['fully_filled'] and down_status['fully_filled']:
        print("💰 BOTH FILLED - PAIRED!")
        notify_profit_pair(arb['q'], arb['bid_up'], arb['q'], arb['bid_down'])
        window_state['current_arb_orders'] = None
        window_state['state'] = STATE_DONE
        return

    if elapsed_ms > arb['ttl_ms'] and up_filled == 0 and down_filled == 0:
        print(f"[{ts()}] TTL_EXPIRED - cancelling")
        cancel_all_orders()
        window_state['current_arb_orders'] = None
        return

    if elapsed_ms > HEDGE_DEADLINE_MS and imb != 0:
        print(f"⚠️ ONE-LEG FILL! Entering PAIRING_MODE")
        cancel_all_orders()
        window_state['current_arb_orders'] = None
        window_state['state'] = STATE_PAIRING
        window_state['pairing_start_time'] = time.time()
        window_state['best_distance_seen'] = float('inf')

# ============================================================================
# PAIRING MODE (keeping existing logic)
# ============================================================================

def run_pairing_mode(books, ttc):
    """Execute forced completion to fix imbalance"""
    global window_state

    # ===========================================
    # BAIL MODE TRIGGER - 90 SECONDS (NO EXCEPTIONS)
    # ===========================================
    if ttc <= BAIL_TIME_REMAINING:
        imb = get_arb_imbalance()  # Exclude 99c capture shares
        if imb != 0:
            # Check bail conditions
            first_order_time = window_state.get('first_order_time', time.time())
            seconds_since_fill = time.time() - first_order_time
            fill_price = window_state.get('avg_up_price_paid', 0) if imb > 0 else window_state.get('avg_down_price_paid', 0)

            # Get current ask for the missing side
            if imb > 0:
                current_ask = float(books['down_asks'][0]['price']) if books.get('down_asks') else 1.0
                excess_side = "UP"
                excess_shares = imb
                excess_token = window_state['up_token']
            else:
                current_ask = float(books['up_asks'][0]['price']) if books.get('up_asks') else 1.0
                excess_side = "DOWN"
                excess_shares = abs(imb)
                excess_token = window_state['down_token']

            print(f"[{ts()}] 🚨 FORCE_BAIL: {ttc:.0f}s remaining, still imbalanced (imb={imb})")
            execute_bail(excess_side, excess_shares, excess_token, books)
            window_state['state'] = STATE_DONE
            return

    time_since_last = time.time() - window_state.get('last_order_time', 0)
    if time_since_last < ORDER_COOLDOWN_SECONDS:
        return

    # DUAL-SOURCE VERIFICATION before pairing
    if POSITION_VERIFY_BEFORE_ORDER:
        verified_up, verified_down = get_verified_fills()
        window_state['filled_up_shares'] = verified_up
        window_state['filled_down_shares'] = verified_down

    imb = get_arb_imbalance()  # Exclude 99c capture shares
    if imb == 0:
        print(f"[{ts()}] PAIRING_MODE EXIT - now flat (arb balanced)")
        window_state['state'] = STATE_QUOTING
        return

    if imb > 0:
        missing_side, missing_shares = "DOWN", imb
        missing_token = window_state['down_token']
        asks = books['down_asks']
        existing_price = window_state['avg_up_price_paid']
        filled_side = "UP"
        filled_token = window_state['up_token']
        filled_price = window_state['avg_up_price_paid']
    else:
        missing_side, missing_shares = "UP", abs(imb)
        missing_token = window_state['up_token']
        asks = books['up_asks']
        existing_price = window_state['avg_down_price_paid']
        filled_side = "DOWN"
        filled_token = window_state['down_token']
        filled_price = window_state['avg_down_price_paid']

    # ===========================================
    # TRACK MARKET STATE AT PAIRING ENTRY (for reversal detection)
    # ===========================================
    if 'pairing_entry_market' not in window_state:
        window_state['pairing_entry_market'] = {
            'up_ask': float(books['up_asks'][0]['price']) if books.get('up_asks') else 0.50,
            'down_ask': float(books['down_asks'][0]['price']) if books.get('down_asks') else 0.50,
            'up_bid': float(books['up_bids'][0]['price']) if books.get('up_bids') else 0.50,
            'down_bid': float(books['down_bids'][0]['price']) if books.get('down_bids') else 0.50,
            'time': time.time()
        }
        print(f"[{ts()}] PAIRING_ENTRY_MARKET: UP ask={window_state['pairing_entry_market']['up_ask']*100:.0f}c | "
              f"DOWN ask={window_state['pairing_entry_market']['down_ask']*100:.0f}c")

    # ===========================================
    # SCALE-UP LOGIC: Handle partial fills properly
    # If missing < 5, buy 5 on missing side + extra on filled side
    # ===========================================
    extra_needed = 0
    original_missing = missing_shares

    if missing_shares < MIN_SHARES:
        extra_needed = MIN_SHARES - missing_shares
        print(f"[{ts()}] ⚠️  SCALE-UP: missing_shares {missing_shares} < {MIN_SHARES} min")
        print(f"[{ts()}] ⚠️  SCALE-UP: Will buy {MIN_SHARES} {missing_side}, then {extra_needed} {filled_side} to re-balance")
        missing_shares = MIN_SHARES

    max_breakeven = floor_to_tick(1.00 - existing_price)

    print(f"[{ts()}] PAIRING_MODE: need {missing_shares} {missing_side} @ max {max_breakeven*100:.0f}c")

    cancel_all_orders()

    # RE-VERIFY position after cancel to prevent duplicate orders (Bug Fix: race condition)
    # The original order may have filled between our imbalance check and cancel
    time.sleep(1.0)  # Brief settle time for cancel/fills to propagate
    verified_up, verified_down = get_verified_fills()
    window_state['filled_up_shares'] = verified_up
    window_state['filled_down_shares'] = verified_down

    # Re-check imbalance with fresh data
    imb = get_arb_imbalance()
    if imb == 0:
        print(f"[{ts()}] PAIRING_MODE: Order filled during cancel - now balanced!")
        window_state['pending_hedge_order_id'] = None
        window_state['pending_hedge_side'] = None
        window_state['state'] = STATE_DONE
        _send_pair_outcome_notification()
        return

    if not asks:
        return

    best_ask = float(asks[0]['price'])

    # ===========================================
    # EARLY BAIL LOGIC v1.10 - 5-SECOND RULE
    # ===========================================
    if window_state.get('pairing_start_time'):
        time_in_pairing = time.time() - window_state['pairing_start_time']
        profit_target_price = 0.99 - existing_price
        current_distance = (best_ask - profit_target_price) * 100  # In cents
        best_ever = window_state.get('best_distance_seen', float('inf'))

        # Update best_distance_seen
        if current_distance < best_ever:
            window_state['best_distance_seen'] = current_distance
            best_ever = current_distance

        # --- 5-SECOND RULE (v1.10) ---
        # Most successful pairs happen within 5 seconds. After that, bail immediately.
        if time_in_pairing >= PAIR_WINDOW_SECONDS and not window_state.get('five_sec_bail_triggered'):
            window_state['five_sec_bail_triggered'] = True  # Only try once

            # Get best available bail price
            if filled_side == "UP":
                bail_bids = books.get('up_bids', [])
            else:
                bail_bids = books.get('down_bids', [])

            if bail_bids:
                bail_price = float(bail_bids[0]['price'])
                bail_loss = (filled_price - bail_price) * 100  # Loss in cents

                print(f"⏱️ 5-SEC RULE: {time_in_pairing:.1f}s elapsed, second leg didn't fill")
                print(f"🛑 IMMEDIATE_BAIL: Selling {missing_shares} {filled_side} @ {bail_price*100:.0f}c (loss: {bail_loss:.0f}c)")

                execute_bail(filled_side, missing_shares, filled_token, books)
                # Calculate P&L: (entry - exit) * shares (negative = loss)
                bail_pnl = -((filled_price - bail_price) * missing_shares)
                log_event("EARLY_BAIL", window_state.get('window_id', ''),
                                side=filled_side, shares=missing_shares, price=bail_price,
                                pnl=bail_pnl,
                                reason="5_second_rule", time_in_pairing=time_in_pairing)
                window_state['state'] = STATE_DONE
                return

        # --- MARKET REVERSAL DETECTION (within 5-second window) ---
        # Can trigger early bail BEFORE the 5-second rule if market moves against us
        entry_market = window_state.get('pairing_entry_market', {})
        time_since_entry = time.time() - entry_market.get('time', time.time())

        if time_since_entry <= PAIR_WINDOW_SECONDS and entry_market:
            # Calculate market move against our position
            if filled_side == "UP":
                entry_bid = entry_market.get('up_bid', 0.50)
                current_bid = float(books['up_bids'][0]['price']) if books.get('up_bids') else 0.50
            else:
                entry_bid = entry_market.get('down_bid', 0.50)
                current_bid = float(books['down_bids'][0]['price']) if books.get('down_bids') else 0.50

            market_move = entry_bid - current_bid  # Positive = market moved against us

            if market_move >= MARKET_REVERSAL_THRESHOLD:
                print(f"⚡ MARKET_REVERSAL: {market_move*100:.0f}c move against {filled_side} in {time_since_entry:.0f}s!")
                # Immediately evaluate bail vs hedge
                decision, price = bail_vs_hedge_decision(filled_side, filled_price, missing_shares, books)
                if decision == "BAIL":
                    print(f"🛑 REVERSAL_BAIL: Selling {filled_side} @ {price*100:.0f}c")
                    execute_bail(filled_side, missing_shares, filled_token, books)
                    # Calculate P&L: (entry - exit) * shares (negative = loss)
                    bail_pnl = -((filled_price - price) * missing_shares)
                    log_event("EARLY_BAIL", window_state.get('window_id', ''),
                                    side=filled_side, shares=missing_shares, price=price,
                                    pnl=bail_pnl,
                                    reason="market_reversal")
                    window_state['state'] = STATE_DONE
                    return

            # --- OB-BASED REVERSAL DETECTION (v1.9) ---
            # If OB shows heavy selling on our filled side + small price drop, bail early
            if ORDERBOOK_ANALYZER_AVAILABLE and orderbook_analyzer:
                ob_result = orderbook_analyzer.analyze(
                    books.get('up_bids', []), books.get('up_asks', []),
                    books.get('down_bids', []), books.get('down_asks', [])
                )
                filled_side_imb = ob_result['up_imbalance'] if filled_side == "UP" else ob_result['down_imbalance']

                # OB shows selling pressure + small price drop = bail immediately
                if filled_side_imb < OB_REVERSAL_THRESHOLD and market_move >= OB_REVERSAL_PRICE_CONFIRM:
                    print(f"📊 OB_REVERSAL: {filled_side} imbalance={filled_side_imb:+.2f} (<{OB_REVERSAL_THRESHOLD}) + {market_move*100:.0f}c drop")
                    decision, price = bail_vs_hedge_decision(filled_side, filled_price, missing_shares, books)
                    if decision == "BAIL":
                        print(f"🛑 OB_BAIL: Selling {filled_side} @ {price*100:.0f}c (OB confirmed reversal)")
                        execute_bail(filled_side, missing_shares, filled_token, books)
                        # Calculate P&L: (entry - exit) * shares (negative = loss)
                        bail_pnl = -((filled_price - price) * missing_shares)
                        log_event("EARLY_BAIL", window_state.get('window_id', ''),
                                        side=filled_side, shares=missing_shares, price=price,
                                        pnl=bail_pnl,
                                        reason="ob_reversal", ob_imbalance=filled_side_imb)
                        window_state['state'] = STATE_DONE
                        return

        # --- EARLY BAIL EVALUATION (after 30s timeout) ---
        if time_in_pairing >= EARLY_HEDGE_TIMEOUT and time_in_pairing < 120:
            # Check every EARLY_BAIL_CHECK_INTERVAL seconds
            last_check = window_state.get('last_bail_check_time', 0)
            if time.time() - last_check >= EARLY_BAIL_CHECK_INTERVAL:
                window_state['last_bail_check_time'] = time.time()

                decision, price = bail_vs_hedge_decision(filled_side, filled_price, missing_shares, books)

                if decision == "BAIL":
                    print(f"🛑 EARLY_BAIL: Selling {filled_side} @ {price*100:.0f}c (cheaper than hedging)")
                    execute_bail(filled_side, missing_shares, filled_token, books)
                    # Calculate P&L: (entry - exit) * shares (negative = loss)
                    bail_pnl = -((filled_price - price) * missing_shares)
                    log_event("EARLY_BAIL", window_state.get('window_id', ''),
                                    side=filled_side, shares=missing_shares, price=price,
                                    pnl=bail_pnl,
                                    reason="bail_cheaper_than_hedge")
                    window_state['state'] = STATE_DONE
                    return

                elif decision == "HEDGE" and price:
                    # Take the hedge at loss - within tolerance
                    print(f"📈 EARLY_HEDGE: Taking {missing_side} @ {price*100:.0f}c (within {EARLY_BAIL_MAX_LOSS*100:.0f}c loss limit)")
                    success, order_id, status_msg = place_and_verify_order(missing_token, price, missing_shares)
                    if success:
                        wait_and_sync_position()
                        imb_check = get_imbalance()
                        if imb_check == 0:
                            log_event("EARLY_HEDGE", window_state.get('window_id', ''),
                                            side=missing_side, shares=missing_shares, price=price,
                                            reason="hedge_within_tolerance")
                            window_state['pending_hedge_order_id'] = None
                            window_state['pending_hedge_side'] = None
                            _send_pair_outcome_notification()
                            window_state['state'] = STATE_DONE
                            return
                        cancel_order(order_id)

        # --- FALLBACK: 5+ min stuck with no hope ---
        if time_in_pairing > 300 and current_distance > 20 and best_ever > 8:
            print(f"[{ts()}] LATE_BAIL: {time_in_pairing:.0f}s elapsed, distance={current_distance:.0f}c, best_ever={best_ever:.0f}c")
            execute_bail(filled_side, missing_shares, filled_token, books)
            window_state['state'] = STATE_DONE
            return

    # Step 1: Post-only with hedge escalation
    if ttc > TAKER_AT_SECONDS:
        # Calculate hedge price based on escalation schedule
        first_order_time = window_state.get('first_order_time', time.time())
        seconds_since_fill = time.time() - first_order_time
        max_hedge, tolerance_cents = calculate_hedge_price(existing_price, seconds_since_fill)

        # Calculate profit target for logging
        profit_target = 0.99 - existing_price

        # Track best distance seen (for early bail logic)
        current_distance = (best_ask - profit_target) * 100  # In cents
        if window_state.get('best_distance_seen') is None or current_distance < window_state.get('best_distance_seen', float('inf')):
            window_state['best_distance_seen'] = current_distance

        best_distance = window_state.get('best_distance_seen', float('inf'))

        # Use the better of: breakeven price, escalated hedge, or just below best ask
        target = min(
            floor_to_tick(1.00 - existing_price - TICK),  # Breakeven
            floor_to_tick(max_hedge),                     # Max hedge with tolerance
            floor_to_tick(best_ask - TICK)                # Just below ask
        )

        print(f"[{ts()}] HEDGE_CALC: entry={existing_price*100:.0f}c target={profit_target*100:.0f}c tolerance={tolerance_cents:.0f}c max_hedge={max_hedge*100:.0f}c elapsed={seconds_since_fill:.0f}s best_seen={best_distance:.0f}c")

        # CHECK: Was previous hedge order filled? (Cancel race condition fix)
        if window_state.get('pending_hedge_order_id'):
            prev_order_id = window_state['pending_hedge_order_id']
            order_status = get_order_status(prev_order_id)

            if order_status and order_status.get('is_filled'):
                # Previous order was filled even though we tried to cancel!
                print(f"[{ts()}] HEDGE_ALREADY_FILLED: Previous order was matched despite cancel")
                window_state['pending_hedge_order_id'] = None
                window_state['pending_hedge_side'] = None
                # Re-sync position and return - don't place duplicate
                wait_and_sync_position()
                return

        if target >= MIN_PRICE and target < best_ask and target <= HEDGE_PRICE_CAP:
            # Use place_and_verify_order for deduplication (Bug Fix #4)
            success, order_id, status_msg = place_and_verify_order(missing_token, target, missing_shares)
            if success:
                # Track this order ID to prevent duplicates (Cancel race condition fix)
                window_state['pending_hedge_order_id'] = order_id
                window_state['pending_hedge_side'] = missing_side

                # Use wait_and_sync_position for proper settlement (Bug Fix #3)
                wait_and_sync_position()

                # Check if we're now flat
                imb = get_imbalance()
                if imb == 0:
                    # Clear tracking - position is balanced
                    window_state['pending_hedge_order_id'] = None
                    window_state['pending_hedge_side'] = None

                    # SCALE-UP: Buy extra on filled side to re-balance
                    if extra_needed > 0:
                        print(f"[{ts()}] ⚠️  SCALE-UP: Buying {extra_needed} extra {filled_side} @ {filled_price*100:.0f}c")
                        success2, order_id2, _ = place_and_verify_order(filled_token, filled_price, extra_needed)
                        if success2:
                            wait_and_sync_position()
                            print(f"[{ts()}] ✅ SCALE-UP COMPLETE: Now flat with larger position")

                    _send_pair_outcome_notification()
                    window_state['state'] = STATE_DONE
                    return
                cancel_order(order_id)
        elif target > HEDGE_PRICE_CAP:
            print(f"[{ts()}] HEDGE_BLOCKED: target {target*100:.0f}c > {HEDGE_PRICE_CAP*100:.0f}c cap")

    imb = get_imbalance()
    if imb == 0:
        window_state['pending_hedge_order_id'] = None
        window_state['pending_hedge_side'] = None
        _send_pair_outcome_notification()
        window_state['state'] = STATE_DONE
        return

    # Step 2: Taker with loss protection
    if ttc <= TAKER_AT_SECONDS and ttc > HARD_FLATTEN_SECONDS:
        # Re-check imbalance with fresh data
        imb = get_imbalance()
        missing_shares = abs(imb)
        if missing_shares >= MIN_SHARES:
            # Loss check: Don't take if loss would exceed MAX_PER_TRADE_LOSS
            potential_total = existing_price + best_ask
            per_share_loss = potential_total - 1.00

            if per_share_loss > MAX_PER_TRADE_LOSS:
                print(f"[{ts()}] TAKER_BLOCKED_MAX_LOSS: {per_share_loss*100:.0f}c per share > {MAX_PER_TRADE_LOSS*100:.0f}c limit")
                print(f"[{ts()}] Would pay {existing_price*100:.0f}c + {best_ask*100:.0f}c = {potential_total*100:.0f}c (loss: {per_share_loss*100:.0f}c)")
                # Don't take - wait for better price or hard flatten
            else:
                # Use place_and_verify_order (Bug Fix #1, #4)
                success, order_id, status_msg = place_and_verify_order(missing_token, best_ask, missing_shares)
                if success:
                    # Use proper settlement delay (Bug Fix #3)
                    wait_and_sync_position()
                    cancel_order(order_id)

    imb = get_imbalance()
    if imb == 0:
        window_state['pending_hedge_order_id'] = None
        window_state['pending_hedge_side'] = None
        _send_pair_outcome_notification()
        window_state['state'] = STATE_DONE
        return

    # Step 3: Hard flatten - use execute_bail for consistent handling
    imb = get_imbalance()
    if ttc <= HARD_FLATTEN_SECONDS and imb != 0:
        print(f"[{ts()}] HARD_FLATTEN triggered (T-{ttc:.0f}s, imb={imb})")

        if imb > 0:
            excess_side, excess_shares = "UP", imb
            excess_token = window_state['up_token']
        else:
            excess_side, excess_shares = "DOWN", abs(imb)
            excess_token = window_state['down_token']

        # Use execute_bail for consistent sell handling
        execute_bail(excess_side, excess_shares, excess_token, books)
        window_state['state'] = STATE_DONE
        log_event("HARD_FLATTEN", window_state.get('window_id', ''),
                        side=excess_side, shares=excess_shares, ttl=ttc)

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
# ROI HALT STATE PERSISTENCE (v1.46)
# ============================================================================

def save_halt_state(pnl, capital_deployed, roi):
    """Save trading halt state to disk (atomic write)."""
    try:
        state = {
            "halted": True,
            "halted_at": datetime.now(timezone.utc).isoformat(),
            "pnl": pnl,
            "capital_deployed": capital_deployed,
            "roi": roi
        }
        tmp_path = ROI_HALT_STATE_FILE + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp_path, ROI_HALT_STATE_FILE)
        print(f"[{ts()}] HALT_STATE: Saved to {ROI_HALT_STATE_FILE}")
    except Exception as e:
        print(f"[{ts()}] HALT_STATE_SAVE_ERROR: {e}")

def load_halt_state():
    """Load trading halt state from disk. Returns True if halted."""
    try:
        if os.path.exists(ROI_HALT_STATE_FILE):
            with open(ROI_HALT_STATE_FILE, "r") as f:
                state = json.load(f)
            if state.get("halted"):
                print(f"[{ts()}] HALT_STATE: Loaded - halted at ROI={state.get('roi', 0)*100:.1f}% "
                      f"(PnL=${state.get('pnl', 0):.2f}, capital=${state.get('capital_deployed', 0):.2f})")
                return True
        return False
    except (json.JSONDecodeError, IOError) as e:
        print(f"[{ts()}] HALT_STATE_LOAD_ERROR: {e} - defaulting to HALTED for safety")
        return True  # Corrupted file = assume halted (safe default)

# ============================================================================
# MAIN BOT
# ============================================================================

def main():
    global window_state, trades_log, error_count, clob_client
    global trading_halted, capital_deployed

    # v1.46: Trading halt state
    trading_halted = load_halt_state()
    capital_deployed = 0.0

    print("=" * 100)
    print(f"CHATGPT POLY BOT - SMART STRATEGY VERSION")
    print("=" * 100)
    print()

    if trading_halted:
        print("*** TRADING HALTED - ROI target previously reached ***")
        print("*** Bot will monitor markets but NOT place any orders ***")
        print(f"*** Resets at midnight EST (cron deletes {ROI_HALT_STATE_FILE}) ***")
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

    print("STRATEGY: HARD HEDGE INVARIANT + SMART SIGNALS")
    print(f"  - NEVER allow unequal UP/DOWN at window close")
    print(f"  - Smart signals: {'ENABLED' if USE_SMART_SIGNALS and STRATEGY_SIGNALS_AVAILABLE else 'DISABLED'}")
    print(f"  - Min confidence: {SMART_SIGNAL_MIN_CONFIDENCE}%")
    print(f"  - Skip choppy: {SMART_SIGNAL_SKIP_CHOPPY}")
    print(f"  - Confidence sizing: {USE_CONFIDENCE_SIZING}")
    print(f"  - Order book signals: {'ENABLED' if USE_ORDERBOOK_SIGNALS and ORDERBOOK_ANALYZER_AVAILABLE else 'DISABLED'}")
    if USE_ORDERBOOK_SIGNALS and ORDERBOOK_ANALYZER_AVAILABLE:
        print(f"  - Min signal strength: {ORDERBOOK_MIN_SIGNAL_STRENGTH}")
        print(f"  - Require trend: {ORDERBOOK_REQUIRE_TREND}")
    print()
    print("FAILSAFES:")
    print(f"  - Max buy price: {FAILSAFE_MAX_BUY_PRICE*100:.0f}c")
    print(f"  - Max shares: {FAILSAFE_MAX_SHARES}")
    print(f"  - Max order cost: ${FAILSAFE_MAX_ORDER_COST:.2f}")
    print(f"  - ROI halt: {ROI_HALT_THRESHOLD*100:.0f}% ({'HALTED' if trading_halted else 'active'})")
    print()
    print("⚠️  CTRL+C TO STOP")
    print("=" * 100)
    print()

    last_slug = None
    session_stats = {"windows": 0, "paired": 0, "pnl": 0.0}
    cached_market = None

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
                        # Prettier window summary
                        total_trades = session_counters['smart_trades'] + session_counters['smart_skips']
                        hit_rate = (session_counters['smart_trades'] / total_trades * 100) if total_trades > 0 else 0
                        print()
                        print("┌─────────────────── WINDOW COMPLETE ───────────────────┐")
                        print(f"│  ✅ Profits: {session_counters['profit_pairs']}    🟧 Loss-Avoid: {session_counters['loss_avoid_pairs']}    🔴 Flatten: {session_counters['hard_flattens']}".ljust(55) + "│")
                        print(f"│  📊 Smart Trades: {session_counters['smart_trades']}/{total_trades} ({hit_rate:.0f}% hit rate)".ljust(55) + "│")
                        print(f"│  💵 Session PnL: ${session_stats['pnl']:.2f}".ljust(55) + "│")
                        print("└────────────────────────────────────────────────────────┘")

                        # v1.46: Check ROI and halt trading if threshold reached
                        if not trading_halted and capital_deployed > 0:
                            current_roi = session_stats['pnl'] / capital_deployed
                            print(f"[{ts()}] ROI CHECK: PnL=${session_stats['pnl']:.2f} / Capital=${capital_deployed:.2f} = {current_roi*100:.1f}%")
                            if current_roi >= ROI_HALT_THRESHOLD:
                                trading_halted = True
                                save_halt_state(session_stats['pnl'], capital_deployed, current_roi)
                                print()
                                print("┌──────────────────────────────────────────────────────────┐")
                                print(f"│  TRADING HALTED - ROI target reached: {current_roi*100:.1f}% >= {ROI_HALT_THRESHOLD*100:.0f}%".ljust(57) + "│")
                                print(f"│  PnL: ${session_stats['pnl']:.2f} on ${capital_deployed:.2f} deployed".ljust(57) + "│")
                                print(f"│  Bot will idle until midnight EST reset".ljust(57) + "│")
                                print("└──────────────────────────────────────────────────────────┘")
                                print()
                                try:
                                    send_telegram(f"TRADING HALTED\\nROI: {current_roi*100:.1f}% (target: {ROI_HALT_THRESHOLD*100:.0f}%)\\nPnL: ${session_stats['pnl']:.2f} on ${capital_deployed:.2f}\\nBot idle until midnight EST")
                                except:
                                    pass
                                log_event("TRADING_HALTED", window_state.get('window_id', ''),
                                    pnl=session_stats['pnl'], capital=capital_deployed, roi=current_roi)

                        # 99c Sniper Resolution Notification
                        if window_state.get('capture_99c_fill_notified'):
                            sniper_side = window_state.get('capture_99c_side')
                            sniper_shares = window_state.get('capture_99c_filled_up', 0) + window_state.get('capture_99c_filled_down', 0)

                            if window_state.get('capture_99c_hedged'):
                                # Hedged: loss = (0.99 + hedge_price) - 1.00 per share
                                hedge_price = window_state.get('capture_99c_hedge_price', 0)
                                sniper_pnl = -(0.99 + hedge_price - 1.00) * sniper_shares
                                sniper_won = False
                                print(f"[{ts()}] 99c SNIPER RESULT: HEDGED (loss avoided) P&L=${sniper_pnl:.2f}")
                            else:
                                # Query Polymarket API for actual settlement result
                                # Retry up to 6 times (30 seconds total) waiting for market resolution
                                sniper_won = None
                                for retry in range(6):
                                    try:
                                        sniper_won = check_99c_outcome(sniper_side, last_slug)
                                        if sniper_won is not None:
                                            sniper_pnl = sniper_shares * 0.01 if sniper_won else -sniper_shares * 0.99
                                            print(f"[{ts()}] 99c SNIPER RESULT: {'WIN' if sniper_won else 'LOSS'} P&L=${sniper_pnl:.2f}")
                                            break
                                        else:
                                            if retry < 5:
                                                print(f"[{ts()}] 99c SNIPER: Market not resolved, retrying in 5s... ({retry+1}/6)")
                                                time.sleep(5)
                                            else:
                                                print(f"[{ts()}] 99c SNIPER RESULT: PENDING after 30s - will resolve on next window")
                                                sniper_pnl = 0
                                    except Exception as e:
                                        print(f"[{ts()}] 99c SNIPER RESULT ERROR (retry {retry+1}): {e}")
                                        if retry < 5:
                                            time.sleep(5)
                                        sniper_won = None
                                        sniper_pnl = 0

                            # Only send notification if we know the result
                            if sniper_won is not None:
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
                                        "settlement_price": 1.00 if sniper_won else 0.00,
                                        "hedged": window_state.get('capture_99c_hedged', False)
                                    }))
                            else:
                                # Add to pending list to retry on next window
                                entry_price = window_state.get('capture_99c_fill_price', 0.99)
                                pending_99c_resolutions.append({
                                    'slug': last_slug,
                                    'side': sniper_side,
                                    'shares': sniper_shares,
                                    'entry_price': entry_price,
                                    'timestamp': time.time()
                                })
                                print(f"[{ts()}] 99c SNIPER: Added to pending queue for later resolution")

                        flush_ticks()  # Flush any remaining tick data

                        # Check for claimable positions after window closes
                        try:
                            from auto_redeem import check_and_claim
                            claimable = check_and_claim()
                            if claimable:
                                total = sum(p['claimable_usdc'] for p in claimable)
                                print(f"[{ts()}] 💰 CLAIMABLE: ${total:.2f} - check polymarket.com to claim!")
                        except ImportError:
                            pass  # auto_redeem module not available
                        except Exception as e:
                            print(f"[{ts()}] REDEEM_CHECK_ERROR: {e}")

                    cancel_all_orders()
                    window_state = reset_window_state(slug)
                    market_price_history.clear()  # v1.24: Clear price history for entry filter
                    cached_market = None
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
                                            "hedged": False,
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

                    # Daily balance snapshot (once per EST day)
                    check_and_log_balance()

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

                if remaining_secs <= CLOSE_GUARD_SECONDS:
                    cancel_all_orders()
                    time.sleep(0.5)
                    continue

                books = get_order_books(cached_market)
                if not books:
                    error_count += 1
                    time.sleep(0.5)
                    continue

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
                        if api_up != api_down:
                            window_state['state'] = STATE_PAIRING
                            window_state['pairing_start_time'] = time.time()
                            window_state['best_distance_seen'] = float('inf')

                log_state(remaining_secs, books)

                # PERIODIC ORDER HEALTH CHECK - detect fills from order status
                if window_state.get('current_arb_orders'):
                    arb = window_state['current_arb_orders']
                    if arb.get('up_id'):
                        up_status = get_order_status(arb['up_id'])
                        if up_status.get('filled', 0) > window_state['filled_up_shares']:
                            print(f"[{ts()}] ORDER_FILL_DETECTED: UP {up_status['filled']:.1f} shares")
                            window_state['filled_up_shares'] = up_status['filled']
                            window_state['avg_up_price_paid'] = arb.get('bid_up', 0)
                    if arb.get('down_id'):
                        down_status = get_order_status(arb['down_id'])
                        if down_status.get('filled', 0) > window_state['filled_down_shares']:
                            print(f"[{ts()}] ORDER_FILL_DETECTED: DOWN {down_status['filled']:.1f} shares")
                            window_state['filled_down_shares'] = down_status['filled']
                            window_state['avg_down_price_paid'] = arb.get('bid_down', 0)

                # 99c BID CAPTURE - runs independently of arb strategy
                # Confidence-based 99c capture: only bet when confidence >= 95%
                # v1.46: Skip when trading halted
                if CAPTURE_99C_ENABLED and books and not window_state.get('capture_99c_used') and not trading_halted:
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
                        # Get actual execution price from Polymarket API (not limit order price)
                        order_price = status.get('price', 0.99) or 0.99
                        fill_price = get_verified_fill_price(slug, side, order_price)
                        # Calculate actual P&L based on real fill price
                        actual_pnl = filled * (1.00 - fill_price)
                        # Store fill price for later reference
                        window_state['capture_99c_fill_price'] = fill_price
                        # v1.46: Track capital deployed for ROI calculation
                        capital_deployed += filled * fill_price
                        print()
                        print(f"┌─────────────── 99c CAPTURE FILLED ───────────────┐")
                        print(f"│  ✅ {side}: {filled:.0f} shares filled @ {fill_price*100:.0f}c".ljust(48) + "│")
                        print(f"│  💰 Expected profit: ${actual_pnl:.2f}".ljust(48) + "│")
                        print(f"└──────────────────────────────────────────────────┘")
                        print()
                        # Record peak confidence at fill time
                        if books.get('up_asks') and books.get('down_asks'):
                            current_ask = float(books['up_asks'][0]['price']) if side == "UP" else float(books['down_asks'][0]['price'])
                            peak_conf, _ = calculate_99c_confidence(current_ask, remaining_secs)
                            window_state['capture_99c_peak_confidence'] = peak_conf
                        window_state['capture_99c_fill_notified'] = True
                        # Update tracked shares with ACTUAL filled amount (not requested)
                        if side == 'UP':
                            window_state['capture_99c_filled_up'] = filled
                        else:
                            window_state['capture_99c_filled_down'] = filled
                        # Send Telegram notification
                        notify_99c_fill(side, filled, peak_conf * 100 if peak_conf else 95, remaining_secs)
                        log_event("CAPTURE_FILL", slug, side=side, shares=filled,
                                        price=fill_price, pnl=actual_pnl)

                # === 60¢ HARD STOP CHECK (v1.34) ===
                # Exit immediately using FOK market orders if best bid <= 60¢
                # Uses best BID (not ask) to ensure real liquidity exists
                if (HARD_STOP_ENABLED and
                    window_state.get('capture_99c_fill_notified') and
                    not window_state.get('capture_99c_exited') and
                    not window_state.get('capture_99c_hedged') and
                    remaining_secs > 15):

                    capture_side = window_state.get('capture_99c_side')

                    # Check if hard stop should trigger (based on best bid)
                    should_trigger, best_bid = check_hard_stop_trigger(books, capture_side)

                    if should_trigger:
                        print(f"[{ts()}] 🛑 HARD STOP: {capture_side} best bid at {best_bid*100:.0f}c <= {HARD_STOP_TRIGGER*100:.0f}c trigger")
                        execute_hard_stop(capture_side, books)

                # === 99c OB-BASED EARLY EXIT CHECK ===
                # Exit early if OB shows sellers dominating for 3 consecutive ticks
                if (OB_EARLY_EXIT_ENABLED and
                    window_state.get('capture_99c_fill_notified') and
                    not window_state.get('capture_99c_exited') and
                    not window_state.get('capture_99c_hedged') and
                    remaining_secs > 15):

                    capture_side = window_state.get('capture_99c_side')

                    # Get OB imbalance for our side
                    if ORDERBOOK_ANALYZER_AVAILABLE and books:
                        ob_result = orderbook_analyzer.analyze(
                            books.get('up_bids', []), books.get('up_asks', []),
                            books.get('down_bids', []), books.get('down_asks', [])
                        )
                        imb = ob_result.get('up_imbalance', 0) if capture_side == "UP" else ob_result.get('down_imbalance', 0)

                        # Track consecutive negative ticks
                        if imb < OB_EARLY_EXIT_THRESHOLD:
                            window_state['ob_negative_ticks'] = window_state.get('ob_negative_ticks', 0) + 1
                            print(f"[{ts()}] 99c OB WARNING: {capture_side} imb={imb:+.2f} ({window_state['ob_negative_ticks']}/3)")
                        else:
                            window_state['ob_negative_ticks'] = 0

                        # Trigger exit if 3 consecutive negative ticks
                        if window_state['ob_negative_ticks'] >= 3:
                            print(f"[{ts()}] 🚨 99c OB EXIT TRIGGERED: {capture_side} imb={imb:+.2f}")
                            execute_99c_early_exit(capture_side, imb, books, reason="ob_reversal")

                # Check if 99c capture needs hedging (confidence dropped)
                # Skip if we already exited early
                if window_state.get('capture_99c_fill_notified') and not window_state.get('capture_99c_hedged') and not window_state.get('capture_99c_exited'):
                    # Calculate danger score from 5 signals
                    bet_side = window_state.get('capture_99c_side')

                    # Get current confidence (same calculation as original entry)
                    if bet_side == "UP":
                        current_ask = float(books['up_asks'][0]['price']) if books.get('up_asks') else 0
                    else:
                        current_ask = float(books['down_asks'][0]['price']) if books.get('down_asks') else 0
                    current_confidence, _ = calculate_99c_confidence(current_ask, remaining_secs)

                    # Get order book imbalance for our side
                    our_imbalance = 0.0
                    if ORDERBOOK_ANALYZER_AVAILABLE:
                        ob_result = orderbook_analyzer.analyze(
                            books.get('up_bids', []), books.get('up_asks', []),
                            books.get('down_bids', []), books.get('down_asks', [])
                        )
                        our_imbalance = ob_result['up_imbalance'] if bet_side == "UP" else ob_result['down_imbalance']

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

                    # Existing hedge check (will use danger_score in Phase 3)
                    check_99c_capture_hedge(books, remaining_secs)

                # State machine
                if window_state['state'] == STATE_DONE:
                    pass
                elif window_state['state'] == STATE_PAIRING:
                    run_pairing_mode(books, remaining_secs)
                elif window_state['state'] == STATE_QUOTING:
                    # DUAL-SOURCE VERIFICATION: Check both order status AND position API
                    verified_up, verified_down = get_verified_fills()
                    window_state['filled_up_shares'] = verified_up
                    window_state['filled_down_shares'] = verified_down

                    # Calculate ARB imbalance (exclude 99c capture shares)
                    arb_up = window_state['filled_up_shares'] - window_state.get('capture_99c_filled_up', 0)
                    arb_down = window_state['filled_down_shares'] - window_state.get('capture_99c_filled_down', 0)
                    arb_imbalance = arb_up - arb_down

                    if abs(arb_imbalance) > MICRO_IMBALANCE_TOLERANCE:
                        # Don't trust single read - verify imbalance persists
                        print(f"[{ts()}] Potential imbalance detected ({arb_up}/{arb_down}), verifying...")
                        time.sleep(2)  # Wait for API to catch up

                        # Re-sync from API
                        api_position = verify_position_from_api()
                        if api_position:
                            api_up, api_down = api_position
                            window_state['filled_up_shares'] = api_up
                            window_state['filled_down_shares'] = api_down

                            # Recalculate imbalance
                            arb_up = api_up - window_state.get('capture_99c_filled_up', 0)
                            arb_down = api_down - window_state.get('capture_99c_filled_down', 0)
                            arb_imbalance = arb_up - arb_down

                        if abs(arb_imbalance) > MICRO_IMBALANCE_TOLERANCE:
                            print(f"🔴 ARB IMBALANCE CONFIRMED! ({arb_up}/{arb_down}) Entering PAIRING_MODE")
                            window_state['state'] = STATE_PAIRING
                            window_state['pairing_start_time'] = time.time()
                            window_state['best_distance_seen'] = float('inf')
                            cancel_all_orders()
                            run_pairing_mode(books, remaining_secs)
                        else:
                            print(f"[{ts()}] Imbalance resolved after re-sync ({arb_up}/{arb_down})")
                    elif remaining_secs <= PAIR_DEADLINE_SECONDS:
                        window_state['state'] = STATE_DONE
                    else:
                        if window_state['current_arb_orders']:
                            monitor_arb_orders(books)
                        else:
                            if ARB_ENABLED:
                                check_and_place_arb(books, remaining_secs)

                elapsed = time.time() - cycle_start
                if window_state['state'] == STATE_PAIRING:
                    time.sleep(max(0.5, PAIRING_LOOP_DELAY - elapsed))
                else:
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
        print(f"🛑 SMART_SKIPS={session_counters['smart_skips']} SMART_TRADES={session_counters['smart_trades']}")

        if window_state:
            trades_log.append(window_state)
        save_trades()
        print("Trades saved to trades_smart.json")

if __name__ == "__main__":
    main()
