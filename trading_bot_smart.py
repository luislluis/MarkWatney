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
    "version": "v1.60",
    "codename": "Night Watch",
    "date": "2026-02-27",
    "changes": "Fix end-of-window blackout: remove T-15s exit gates; background 99c resolution; safety exit at T-10s<80c; FINAL_SECONDS logging; 5x monitoring in last 15s"
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
                                 log_event as supabase_log_event,
                                 get_daily_roi as supabase_get_daily_roi)
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
    supabase_get_daily_roi = lambda *args, **kwargs: None

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
FAILSAFE_MAX_SHARES = 999999      # No practical limit (portfolio-sized trades)
FAILSAFE_MAX_ORDER_COST = 999999  # No practical limit (portfolio-sized trades)

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
ROI_HALT_THRESHOLD = 0.60          # Halt all trading at 60% ROI
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
HARD_STOP_ENABLED = True           # Enable 45¢ hard stop
HARD_STOP_TRIGGER = 0.45           # Exit when best bid <= 45¢
HARD_STOP_FLOOR = 0.01             # Effectively no floor (1¢ minimum)
HARD_STOP_USE_FOK = True           # Use Fill-or-Kill market orders
HARD_STOP_CONSECUTIVE_REQUIRED = 2 # Require 2 consecutive ticks below trigger before firing
WINDOW_END_SAFETY_PRICE = 0.80     # Safety exit below 80c in final 10 seconds (don't wait for 45c)

# ===========================================
# ENTRY RESTRICTIONS
# ===========================================
MIN_TIME_FOR_ENTRY = 300           # Never enter with <5 minutes (300s) remaining

# ===========================================
# 99c BID CAPTURE STRATEGY (CONFIDENCE-BASED)
# ===========================================
CAPTURE_99C_ENABLED = True         # Enable/disable 99c capture strategy
CAPTURE_99C_MAX_SPEND = 6.00       # Fallback max spend if portfolio balance unavailable
TRADE_SIZE_PCT = 0.42              # 42% of portfolio per trade (used only when FIXED_TRADE_SHARES is 0)
FIXED_TRADE_SHARES = 25            # Fixed trade size in shares. Set >0 to override portfolio-based sizing.
CAPTURE_99C_BID_PRICE = 0.95       # Place bid at 95c
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

# ===========================================
# DANGER EXIT — Confidence + Opponent Ask Gate (v1.56)
# ===========================================
# Exit when danger score is high AND opponent ask confirms real uncertainty.
# Opponent ask > 15c = market disagrees with our bet. Combined with high danger = GET OUT.
# On wins, opponent ask is always ≤8c. 15c threshold provides 7c margin.
DANGER_EXIT_ENABLED = False
DANGER_EXIT_THRESHOLD = 0.40          # Exit when danger score >= this
DANGER_EXIT_OPPONENT_ASK_MIN = 0.15   # Only exit if opponent ask > 15c (real uncertainty)
DANGER_EXIT_CONSECUTIVE_REQUIRED = 2  # Require 2 consecutive ticks (prevent single-tick noise)

# ===========================================
# INSTANT PROFIT LOCK (v1.58)
# ===========================================
# On 99c capture fill, immediately place sell limit at 99c to lock in profit.
# If market turns (bid < 60c), cancel sell and let hard stop handle exit.
PROFIT_LOCK_ENABLED = True
PROFIT_LOCK_SELL_PRICE = 0.99         # Sell limit price (99c)
PROFIT_LOCK_CANCEL_THRESHOLD = 0.60   # Cancel sell if best bid drops below 60c

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

# v1.49: Dynamic trade sizing — cached at window start
cached_portfolio_total = 0.0
# v1.54: Lock trade size for the entire day (set once at first window after midnight EST)
# v1.56: Persisted to file so restarts don't reset the lock
DAILY_TRADE_SIZE_FILE = os.path.expanduser("~/.polybot_daily_trade_size.json")

def _load_daily_trade_shares() -> int:
    """Load today's locked trade size from file. Returns 0 if stale/missing."""
    try:
        with open(DAILY_TRADE_SIZE_FILE, 'r') as f:
            data = json.load(f)
        est_now = datetime.now(ZoneInfo("America/New_York"))
        if data.get('date') == est_now.strftime("%Y-%m-%d"):
            shares = int(data['shares'])
            print(f"[STARTUP] Restored daily trade size from file: {shares} shares (locked {data['date']})")
            return shares
    except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError):
        pass
    return 0

def _save_daily_trade_shares(shares: int):
    """Persist today's locked trade size to file."""
    est_now = datetime.now(ZoneInfo("America/New_York"))
    data = {"date": est_now.strftime("%Y-%m-%d"), "shares": shares}
    try:
        with open(DAILY_TRADE_SIZE_FILE, 'w') as f:
            json.dump(data, f)
    except Exception as e:
        print(f"WARNING: Failed to save daily trade size: {e}")

daily_trade_shares = _load_daily_trade_shares()

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
        "hard_stop_consecutive_ticks": 0,    # hard stop: consecutive ticks below trigger
        "started_mid_window": False,         # True if bot started mid-window (skip trading)
        "pairing_start_time": None,          # When PAIRING_MODE was entered
        "best_distance_seen": None,          # Best (lowest) distance from profit target (in cents)
        "pending_hedge_order_id": None,      # Track pending hedge order to prevent duplicates
        "pending_hedge_side": None,          # Which side the pending hedge is for (UP/DOWN)
        "danger_score": 0,                    # Current danger score (0.0-1.0)
        "danger_exit_ticks": 0,              # Danger exit: consecutive ticks above threshold
        "capture_99c_peak_confidence": 0,     # Confidence at 99c fill time
        "profit_lock_order_id": None,        # Profit lock: sell order ID
        "profit_lock_cancelled": False,      # Profit lock: whether sell was cancelled due to price drop
        "profit_lock_filled": False,         # Profit lock: whether sell order filled
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
        tuple: (success, order_id, filled_shares, is_balance_error)
    """
    global clob_client

    # v1.46: Defense-in-depth - block ALL orders when trading is halted
    if trading_halted:
        print(f"[{ts()}] [HALT] BLOCKED: FOK SELL x{shares} - trading halted (ROI target reached)")
        return False, None, 0, False

    try:
        from py_clob_client.clob_types import MarketOrderArgs, OrderType
        from py_clob_client.order_builder.constants import SELL

        print(f"[{ts()}] HARD_STOP: Placing FOK market sell: {shares:.1f} shares")

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
            return True, order_id, filled, False
        else:
            print(f"[{ts()}] HARD_STOP: FOK rejected, status={status}")
            log_activity("FOK_REJECTED", {"order_id": order_id, "status": status})
            return False, order_id, 0.0, False

    except Exception as e:
        error_str = str(e).lower()
        is_balance_error = 'not enough balance' in error_str or 'allowance' in error_str
        print(f"[{ts()}] HARD_STOP_ERROR: FOK order failed: {e}" + (" [BALANCE ERROR]" if is_balance_error else ""))
        log_activity("FOK_ERROR", {"error": str(e), "balance_error": is_balance_error})
        return False, "", 0.0, is_balance_error


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
    max_attempts = 15  # Safety limit (more attempts since we chunk now)
    balance_errors = 0  # Track consecutive balance errors

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

        # v1.55: Chunk FOK sells to order book depth (don't try to sell more than book can absorb)
        total_bid_depth = sum(float(b.get('size', 0)) for b in bids)
        chunk_size = min(remaining_shares, max(total_bid_depth * 0.9, 1))  # 90% of depth, min 1 share
        print(f"[{ts()}] HARD_STOP: Book depth={total_bid_depth:.0f}, selling chunk={chunk_size:.1f} of {remaining_shares:.0f} remaining")

        # Place FOK market sell for chunk (not full position)
        success, order_id, filled, is_balance_error = place_fok_market_sell(token, chunk_size)

        if success and filled > 0:
            # Calculate P&L for this fill
            fill_pnl = (best_bid - entry_price) * filled
            total_pnl += fill_pnl
            remaining_shares -= filled
            balance_errors = 0  # Reset on success

            print(f"[{ts()}] HARD_STOP: Filled {filled:.0f} @ ~{best_bid*100:.0f}c, P&L: ${fill_pnl:.2f}, remaining: {remaining_shares:.0f}")
        elif is_balance_error:
            balance_errors += 1
            print(f"[{ts()}] HARD_STOP: Balance error #{balance_errors} - shares may already be sold")
            if balance_errors >= 3:
                print(f"[{ts()}] HARD_STOP: 3 consecutive balance errors - shares already sold, stopping")
                remaining_shares = 0  # Assume sold
                break
            # Halve the chunk and retry
            remaining_shares = remaining_shares / 2
            if remaining_shares < 1:
                print(f"[{ts()}] HARD_STOP: Chunk too small after halving, stopping")
                remaining_shares = 0
                break
            time.sleep(0.5)
        else:
            print(f"[{ts()}] HARD_STOP: FOK rejected, refreshing book (attempt {attempts})")
            time.sleep(0.5)
            # Refresh order books for next attempt
            if window_state.get('cached_market'):
                books = get_order_books(window_state['cached_market'])

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


# ============================================================================
# ORDER-BOOK-AWARE CHUNKED EXIT (v1.59)
# ============================================================================

HARD_STOP_MAX_SHARES = 10  # If more than this many shares remain after chunked exit, run a second chunked pass instead of FOK

# Recovery tracking: list of (exit_timestamp, exit_price, side, token, market) tuples
# Background thread checks prices at +1m, +5m, +15m after each exit
_recovery_checks_pending = []

def _log_ob_snapshot(side: str, bids_sorted: list):
    """Log full order book state before exit for post-hoc analysis."""
    print(f"[{ts()}] OB_SNAPSHOT ({side}) — {len(bids_sorted)} bid levels:")
    total_depth = 0
    for level in bids_sorted:
        price = float(level['price'])
        size = float(level['size'])
        total_depth += size
        print(f"[{ts()}]   {price*100:6.1f}c | {size:>10.1f} shares | cumulative: {total_depth:.0f}")
    print(f"[{ts()}] OB_SNAPSHOT TOTAL: {total_depth:.0f} shares across {len(bids_sorted)} levels")

def _schedule_recovery_check(exit_time: float, exit_price: float, side: str):
    """Schedule background recovery price checks at +1m, +5m, +15m after exit."""
    import threading

    def _check():
        delays = [(60, "+1min"), (300, "+5min"), (900, "+15min")]
        results = {}
        for delay_sec, label in delays:
            wait = exit_time + delay_sec - time.time()
            if wait > 0:
                time.sleep(wait)
            # Fetch current best bid for this side
            try:
                slug, _ = get_current_slug()
                market = get_market_data(slug)
                if market:
                    fresh_books = get_order_books(market)
                    if fresh_books:
                        bids = fresh_books.get(f'{side.lower()}_bids', [])
                        if bids:
                            price = float(bids[0]['price'])
                            results[label] = price
                        else:
                            results[label] = 0.0
                    else:
                        results[label] = 0.0
                else:
                    results[label] = 0.0
            except Exception:
                results[label] = 0.0

        p1 = results.get("+1min", 0)
        p5 = results.get("+5min", 0)
        p15 = results.get("+15min", 0)
        recovered = "YES" if max(p1, p5, p15) >= 0.80 else "NO"

        line = (f"RECOVERY CHECK: [{ts()}] | Exit price: {exit_price*100:.0f}c "
                f"| Price at +1min: {p1*100:.0f}c | Price at +5min: {p5*100:.0f}c "
                f"| Price at +15min: {p15*100:.0f}c | Would have recovered: {recovered}")
        print(f"[{ts()}] {line}")
        log_activity("RECOVERY_CHECK", {
            "exit_price": exit_price, "side": side,
            "price_1m": p1, "price_5m": p5, "price_15m": p15,
            "recovered": recovered
        })

    t = threading.Thread(target=_check, daemon=True)
    t.start()


def _walk_bids(side: str, token: str, entry_price: float, remaining: float, pass_label: str) -> tuple:
    """
    Walk the order book bid levels top-down, placing limit sells matched to
    each level's available size. Re-fetches the order book every 3 chunks.

    Args:
        side: "UP" or "DOWN"
        token: Token ID to sell
        entry_price: Entry price for P&L calculation
        remaining: Shares left to sell
        pass_label: "PASS_1" or "PASS_2" for log clarity

    Returns:
        tuple: (confirmed_filled, total_pnl, remaining, fill_log, orders_placed, pending_order_ids)
    """
    OB_EXIT_FILL_TIMEOUT = 3.0
    OB_EXIT_POLL_INTERVAL = 0.5
    OB_REFRESH_EVERY = 3  # Re-fetch order book every N chunks

    confirmed_filled = 0.0
    total_pnl = 0.0
    fill_log = []
    orders_placed = 0
    pending_order_ids = []
    chunks_since_refresh = 0

    # Fetch initial order book
    books = get_order_books(window_state['cached_market']) if window_state.get('cached_market') else None
    if books is None:
        return 0.0, 0.0, remaining, [], 0, []

    bids_key = f'{side.lower()}_bids'

    while remaining > 0:
        # Refresh order book every OB_REFRESH_EVERY chunks
        if chunks_since_refresh >= OB_REFRESH_EVERY and window_state.get('cached_market'):
            print(f"[{ts()}] OB REFRESH: [{pass_label}] Chunks placed so far: {orders_placed} "
                  f"| Confirmed fills so far: {confirmed_filled:.0f} shares "
                  f"| Remaining: {remaining:.0f} shares | Refreshing order book")
            log_activity("OB_REFRESH", {
                "pass": pass_label, "orders_placed": orders_placed,
                "confirmed_filled": confirmed_filled, "remaining": remaining
            })
            books = get_order_books(window_state['cached_market'])
            if books is None:
                print(f"[{ts()}] OB_EXIT [{pass_label}]: Refresh failed, stopping walk")
                break
            chunks_since_refresh = 0

        bids = books.get(bids_key, [])
        bids_sorted = sorted(bids, key=lambda x: float(x['price']), reverse=True)

        if not bids_sorted:
            print(f"[{ts()}] OB_EXIT [{pass_label}]: No bids remaining in book")
            break

        # Walk current snapshot
        made_progress = False
        for level in bids_sorted:
            if remaining <= 0:
                break

            bid_price = float(level['price'])
            bid_size = float(level['size'])

            if bid_size <= 0:
                continue

            chunk = min(bid_size, remaining)

            print(f"[{ts()}] OB_EXIT [{pass_label}]: Selling {chunk:.1f} @ {bid_price*100:.0f}c (bid depth: {bid_size:.0f})")

            success, result = place_limit_order(token, bid_price, chunk, side="SELL")

            if not success:
                print(f"[{ts()}] OB_EXIT [{pass_label}]:   -> ORDER FAILED: {result}")
                fill_log.append({"price": bid_price, "size": chunk, "order_id": None,
                                 "success": False, "filled": 0})
                continue

            orders_placed += 1
            chunks_since_refresh += 1
            order_id = result
            pending_order_ids.append(order_id)
            made_progress = True

            # Wait for fill confirmation with timeout
            filled_shares = 0.0
            fill_start = time.time()
            while time.time() - fill_start < OB_EXIT_FILL_TIMEOUT:
                status = get_order_status(order_id)
                filled_shares = status.get('filled', 0)
                if status.get('fully_filled'):
                    break
                time.sleep(OB_EXIT_POLL_INTERVAL)

            # Final status check
            if filled_shares <= 0:
                status = get_order_status(order_id)
                filled_shares = status.get('filled', 0)

            chunk_pnl = (bid_price - entry_price) * filled_shares if filled_shares > 0 else 0
            unfilled = chunk - filled_shares

            fill_log.append({
                "price": bid_price, "size": chunk, "order_id": order_id,
                "success": True, "filled": filled_shares, "unfilled": unfilled,
                "pnl": chunk_pnl,
            })

            if filled_shares > 0:
                confirmed_filled += filled_shares
                total_pnl += chunk_pnl
                remaining -= filled_shares
                print(f"[{ts()}] OB_EXIT [{pass_label}]:   -> FILLED: {filled_shares:.0f}/{chunk:.0f} @ {bid_price*100:.0f}c "
                      f"(P&L: ${chunk_pnl:+.2f}) [ID: {order_id[:8]}...]")
                log_activity("OB_EXIT_CHUNK", {
                    "pass": pass_label, "side": side, "price": bid_price,
                    "size": chunk, "filled": filled_shares,
                    "pnl": round(chunk_pnl, 4), "order_id": order_id,
                    "remaining": remaining
                })
            else:
                print(f"[{ts()}] OB_EXIT [{pass_label}]:   -> NOT FILLED in {OB_EXIT_FILL_TIMEOUT}s, cancelling [ID: {order_id[:8]}...]")

            # Cancel unfilled portion immediately
            if unfilled > 0:
                cancel_order(order_id)
                print(f"[{ts()}] OB_EXIT [{pass_label}]:   -> Cancelled {unfilled:.0f} unfilled @ {bid_price*100:.0f}c")

            # Check if we need an OB refresh mid-walk
            if chunks_since_refresh >= OB_REFRESH_EVERY and remaining > 0:
                break  # Break inner for-loop to trigger refresh in outer while-loop

        # If we walked the entire snapshot without placing any orders, stop
        if not made_progress:
            break

    # Safety sweep: cancel any remaining open orders
    for oid in pending_order_ids:
        try:
            status = get_order_status(oid)
            if not status.get('fully_filled') and status.get('status') not in ('CANCELLED', 'EXPIRED'):
                cancel_order(oid)
        except Exception:
            pass

    return confirmed_filled, total_pnl, remaining, fill_log, orders_placed, pending_order_ids


def execute_ob_exit(side: str, books: dict = None) -> tuple:
    """
    Sell position by walking the order book and placing limit sells
    matched to each bid level's available size.

    Flow:
      1. Fetch fresh order book, log snapshot
      2. First pass: walk bids top-down (refreshing every 3 chunks)
      3. If >HARD_STOP_MAX_SHARES remain: re-fetch book, run second chunked pass
      4. If <=HARD_STOP_MAX_SHARES remain: single FOK to clean up
      5. If FOK fails: log + alert (never leave orphaned positions silently)

    Falls back to execute_hard_stop (FOK) only for small remainders or total failure.

    Args:
        side: "UP" or "DOWN" - which side we're liquidating
        books: Order book data (will be fetched fresh regardless)

    Returns:
        tuple: (success, total_pnl, fill_log)
    """
    global window_state

    exit_start = time.time()

    # --- Determine shares to sell ---
    tracked_shares = window_state.get(f'capture_99c_filled_{side.lower()}', 0)
    api_pos = verify_position_from_api()

    if api_pos is not None:
        actual_shares = api_pos[0] if side == 'UP' else api_pos[1]
        if abs(actual_shares - tracked_shares) > 0.01 and actual_shares > 0:
            print(f"[{ts()}] OB_EXIT: Position mismatch - API={actual_shares:.4f} tracked={tracked_shares:.4f}, using API")
        shares = actual_shares if actual_shares > 0 else tracked_shares
    else:
        print(f"[{ts()}] OB_EXIT: API unavailable, using tracked shares={tracked_shares}")
        shares = tracked_shares

    if shares <= 0:
        print(f"[{ts()}] OB_EXIT: No shares to sell for {side}")
        return False, 0.0, []

    token = window_state.get(f'{side.lower()}_token')
    entry_price = window_state.get('capture_99c_fill_price', 0.99)

    # --- Fetch FRESH order book for initial snapshot log ---
    if window_state.get('cached_market'):
        books = get_order_books(window_state['cached_market'])

    if books is None:
        print(f"[{ts()}] OB_EXIT: Cannot fetch order book, falling back to hard stop")
        hs_success, hs_pnl = execute_hard_stop(side, {})
        return hs_success, hs_pnl, []

    print()
    print("=" * 55)
    print(f"  OB_EXIT: Selling {shares:.0f} {side} shares via order book walk")
    print(f"  Entry: {entry_price*100:.0f}c | Token: {token[:12]}...")
    print("=" * 55)

    # --- Order book snapshot before any orders ---
    bids_key = f'{side.lower()}_bids'
    bids_sorted = sorted(books.get(bids_key, []), key=lambda x: float(x['price']), reverse=True)
    _log_ob_snapshot(side, bids_sorted)

    if not bids_sorted:
        print(f"[{ts()}] OB_EXIT: No bids in book, falling back to hard stop")
        hs_success, hs_pnl = execute_hard_stop(side, books)
        return hs_success, hs_pnl, []

    # ========== FIRST PASS ==========
    print(f"[{ts()}] OB_EXIT: Starting PASS 1 — {shares:.0f} shares to sell")
    p1_filled, p1_pnl, remaining, p1_log, p1_orders, _ = _walk_bids(
        side, token, entry_price, shares, "PASS_1")

    total_pnl = p1_pnl
    all_fills = list(p1_log)
    total_orders = p1_orders
    confirmed_filled = p1_filled

    # ========== SECOND PASS (if >HARD_STOP_MAX_SHARES remain) ==========
    if remaining > HARD_STOP_MAX_SHARES:
        print()
        print(f"[{ts()}] SECOND PASS: Remaining shares: {remaining:.0f} "
              f"| Re-fetching order book for second chunked exit attempt")
        log_activity("SECOND_PASS", {
            "remaining": remaining, "first_pass_filled": p1_filled,
            "first_pass_orders": p1_orders
        })

        p2_filled, p2_pnl, remaining, p2_log, p2_orders, _ = _walk_bids(
            side, token, entry_price, remaining, "PASS_2")

        total_pnl += p2_pnl
        all_fills.extend(p2_log)
        total_orders += p2_orders
        confirmed_filled += p2_filled

    # ========== FOK CLEANUP (if <=HARD_STOP_MAX_SHARES remain) ==========
    if 0 < remaining <= HARD_STOP_MAX_SHARES:
        print(f"[{ts()}] OB_EXIT: {remaining:.0f} shares remain (<= {HARD_STOP_MAX_SHARES}), using FOK to clean up")
        window_state[f'capture_99c_filled_{side.lower()}'] = remaining
        hs_success, hs_pnl = execute_hard_stop(side, books)
        total_pnl += hs_pnl
        if hs_success:
            remaining = 0
    elif remaining > HARD_STOP_MAX_SHARES:
        # Both passes exhausted and still >HARD_STOP_MAX_SHARES remain — FOK as last resort
        print(f"[{ts()}] OB_EXIT: {remaining:.0f} shares STILL remain after 2 passes, FOK last resort")
        window_state[f'capture_99c_filled_{side.lower()}'] = remaining
        hs_success, hs_pnl = execute_hard_stop(side, books)
        total_pnl += hs_pnl
        if hs_success:
            remaining = 0

    # ========== AUTOMATED LAST RESORT — never end a window holding shares ==========
    if remaining > 0:
        print(f"[{ts()}] OB_EXIT: FOK failed, {remaining:.0f} shares stuck. Entering last resort sequence.")
        send_telegram(f"<b>OB EXIT: FOK FAILED</b>\nStuck: {remaining:.0f} {side}\nEntering last resort sequence...")

        # Step 1: Wait 5s, re-fetch book, one more chunked pass at any price
        print(f"[{ts()}] LAST RESORT: Waiting 5s then attempting emergency chunked exit...")
        time.sleep(5)
        lr_filled, lr_pnl, remaining, lr_log, lr_orders, _ = _walk_bids(
            side, token, entry_price, remaining, "LAST_RESORT_CHUNKED")
        total_pnl += lr_pnl
        all_fills.extend(lr_log)
        total_orders += lr_orders
        confirmed_filled += lr_filled
        if remaining <= 0:
            print(f"[{ts()}] LAST RESORT: Emergency chunked exit succeeded! All shares sold.")

        # Step 2: Retry FOK every 10s until flat
        LAST_RESORT_MAX_ATTEMPTS = 30  # 30 * 10s = 5 minutes max
        lr_attempt = 0
        while remaining > 0 and lr_attempt < LAST_RESORT_MAX_ATTEMPTS:
            lr_attempt += 1
            time.sleep(10)

            # Re-fetch book for current best bid
            lr_books = get_order_books(window_state['cached_market']) if window_state.get('cached_market') else None
            lr_bids = []
            best_bid_price = 0
            if lr_books:
                lr_bids = lr_books.get(f'{side.lower()}_bids', [])
                if lr_bids:
                    best_bid_price = float(sorted(lr_bids, key=lambda x: float(x['price']), reverse=True)[0]['price'])

            print(f"[{ts()}] LAST RESORT: Attempt {lr_attempt} | Shares remaining: {remaining:.0f} "
                  f"| Best bid: {best_bid_price*100:.0f}c")

            if not lr_bids or best_bid_price <= 0:
                result_msg = "failed (no bids)"
                print(f"[{ts()}] LAST RESORT: [{ts()}] | Attempt {lr_attempt} | Shares remaining: {remaining:.0f} "
                      f"| Best bid: 0c | Result: {result_msg}")
                log_activity("LAST_RESORT", {
                    "attempt": lr_attempt, "remaining": remaining,
                    "best_bid": 0, "result": result_msg
                })
                continue

            # Try FOK for remaining shares
            window_state[f'capture_99c_filled_{side.lower()}'] = remaining
            fok_success, fok_oid, fok_filled, fok_bal_err = place_fok_market_sell(token, remaining)

            if fok_success and fok_filled > 0:
                fok_pnl = (best_bid_price - entry_price) * fok_filled
                total_pnl += fok_pnl
                confirmed_filled += fok_filled
                remaining -= fok_filled
                result_msg = f"filled {fok_filled:.0f}"
            else:
                result_msg = "failed"
                if fok_bal_err:
                    # Balance error likely means shares already sold
                    remaining = remaining / 2
                    if remaining < 1:
                        remaining = 0
                    result_msg = f"balance error, halved to {remaining:.0f}"

            print(f"[{ts()}] LAST RESORT: [{ts()}] | Attempt {lr_attempt} | Shares remaining: {remaining:.0f} "
                  f"| Best bid: {best_bid_price*100:.0f}c | Result: {result_msg}")
            log_activity("LAST_RESORT", {
                "attempt": lr_attempt, "remaining": remaining,
                "best_bid": best_bid_price, "result": result_msg
            })

        if remaining > 0:
            print(f"[{ts()}] LAST RESORT EXHAUSTED: {remaining:.0f} shares could not be sold after {lr_attempt} attempts!")
            send_telegram(
                f"<b>LAST RESORT EXHAUSTED</b>\n"
                f"Side: {side}\n"
                f"Stuck shares: {remaining:.0f}\n"
                f"Attempts: {lr_attempt}\n"
                f"<i>All automated exits failed</i>")
        else:
            print(f"[{ts()}] LAST RESORT: Successfully exited all shares after {lr_attempt} attempt(s)")
            send_telegram(f"LAST RESORT: Exited all {side} shares after {lr_attempt} retry attempt(s)")

    # --- EXIT SUMMARY ---
    exit_elapsed_ms = (time.time() - exit_start) * 1000

    if all_fills:
        prices = [f["price"] for f in all_fills if f.get("filled", 0) > 0]
        sizes = [f["filled"] for f in all_fills if f.get("filled", 0) > 0]
        if prices:
            avg_fill = sum(p * s for p, s in zip(prices, sizes)) / sum(sizes)
            best_fill = max(prices)
            worst_fill = min(prices)
        else:
            avg_fill = best_fill = worst_fill = 0
    else:
        avg_fill = best_fill = worst_fill = 0

    summary = (f"EXIT SUMMARY: [{ts()}] | Shares: {shares:.0f} | Filled: {confirmed_filled:.0f} "
               f"| Avg fill price: {avg_fill*100:.0f}c "
               f"| Chunks: {total_orders} | Best fill: {best_fill*100:.0f}c | Worst fill: {worst_fill*100:.0f}c "
               f"| Total P&L: ${total_pnl:+.2f} | Time to complete: {exit_elapsed_ms:.0f}ms"
               f"| Unfilled: {remaining:.0f}")
    print()
    print(summary)
    print()
    log_activity("EXIT_SUMMARY", {
        "shares": shares, "confirmed_filled": confirmed_filled,
        "avg_fill": round(avg_fill, 4),
        "chunks": total_orders, "best_fill": best_fill, "worst_fill": worst_fill,
        "pnl": round(total_pnl, 4), "elapsed_ms": round(exit_elapsed_ms),
        "unfilled": remaining
    })

    log_event("OB_EXIT", window_state.get('window_id', ''),
              side=side, shares=shares, covered=confirmed_filled,
              orders=total_orders, pnl=round(total_pnl, 4),
              uncovered=remaining, avg_fill=round(avg_fill, 4),
              elapsed_ms=round(exit_elapsed_ms))

    # Telegram notification
    msg = f"""{'='*20}
<b>OB EXIT ({side})</b>
{'='*20}
Shares: {shares:.0f}
Confirmed filled: {confirmed_filled:.0f} across {total_orders} orders
Avg fill: {avg_fill*100:.0f}c | Best: {best_fill*100:.0f}c | Worst: {worst_fill*100:.0f}c
Entry: {entry_price*100:.0f}c
P&L: ${total_pnl:+.2f} ({exit_elapsed_ms:.0f}ms)"""
    if remaining > 0:
        msg += f"\n{remaining:.0f} shares STUCK — manual check needed"
    send_telegram(msg)

    # --- Recovery tracking ---
    exit_bid = float(bids_sorted[0]['price']) if bids_sorted else HARD_STOP_TRIGGER
    _schedule_recovery_check(time.time(), exit_bid, side)

    # --- Update state ---
    if remaining <= 0:
        window_state['capture_99c_exited'] = True
        window_state['capture_99c_exit_reason'] = 'ob_exit'
        window_state[f'capture_99c_filled_{side.lower()}'] = 0
        return True, total_pnl, all_fills
    else:
        window_state[f'capture_99c_filled_{side.lower()}'] = remaining
        return False, total_pnl, all_fills


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

    # === OB EXIT (v1.59) — walks order book, falls back to hard stop ===
    if HARD_STOP_ENABLED:
        should_trigger, best_bid = check_hard_stop_trigger(books, side)
        if should_trigger:
            print(f"[{ts()}] EARLY_EXIT: Triggering OB EXIT (best_bid={best_bid*100:.0f}c)")
            ob_success, ob_pnl, ob_fills = execute_ob_exit(side, books)
            if ob_success:
                return True
            # OB exit failed entirely — hard stop as full fallback
            print(f"[{ts()}] EARLY_EXIT: OB EXIT failed, falling back to hard stop")
            hs_success, hs_pnl = execute_hard_stop(side, books)
            return hs_success

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

    # Trade sizing: FIXED_TRADE_SHARES takes priority if set
    if FIXED_TRADE_SHARES > 0:
        shares = FIXED_TRADE_SHARES
    elif daily_trade_shares > 0:
        shares = daily_trade_shares
    elif cached_portfolio_total > 0:
        trade_budget = cached_portfolio_total * TRADE_SIZE_PCT
        shares = math.ceil(trade_budget / CAPTURE_99C_BID_PRICE)
    else:
        shares = int(CAPTURE_99C_MAX_SPEND / CAPTURE_99C_BID_PRICE)
    token = window_state['up_token'] if side == 'UP' else window_state['down_token']

    print()
    print(f"┌─────────────── 99c CAPTURE ───────────────┐")
    print(f"│  {side} @ {current_ask*100:.0f}c | T-{ttc:.0f}s | Confidence: {confidence*100:.0f}%".ljust(44) + "│")
    print(f"│  (base {current_ask*100:.0f}% - {penalty*100:.0f}% time penalty)".ljust(44) + "│")
    print(f"│  Bidding {shares} shares @ {CAPTURE_99C_BID_PRICE*100:.0f}c = ${shares * CAPTURE_99C_BID_PRICE:.2f}".ljust(44) + "│")
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
        window_state['capture_99c_token'] = token
        # NOTE: Do NOT set capture_99c_filled_up/down here.
        # Fill tracking happens in the main loop fill detection (line ~3830).
        # Setting it here causes get_arb_imbalance() to return a phantom
        # negative value, triggering false PAIRING_MODE entry.
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

def get_midnight_est_utc():
    """Get today's midnight EST as a UTC ISO string for Supabase queries."""
    EST = ZoneInfo("America/New_York")
    now_est = datetime.now(EST)
    midnight_est = now_est.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight_est.astimezone(timezone.utc).isoformat()

def get_roi_from_activity_api() -> dict:
    """
    Query Polymarket Activity API for today's ROI.
    Uses same data source and logic as dashboard — single source of truth.
    Returns dict with: total_pnl, avg_trade_cost, roi, capital_deployed, wins, losses, exits, pending, trades
    Returns None on error.
    """
    try:
        EST = ZoneInfo("America/New_York")
        now_est = datetime.now(EST)
        midnight_est = now_est.replace(hour=0, minute=0, second=0, microsecond=0)
        midnight_ts = midnight_est.timestamp()

        url = f"https://data-api.polymarket.com/activity?user={WALLET_ADDRESS}&limit=1000"
        resp = http_session.get(url, timeout=10)
        resp.raise_for_status()
        all_activity = resp.json()

        # Split into trades and redeems
        poly_trades = [a for a in all_activity if a.get('type') == 'TRADE']
        redeem_events = [a for a in all_activity if a.get('type') == 'REDEEM']
        redeemed = {r['slug'] for r in redeem_events if r.get('slug')}

        # Filter trades to today (midnight EST)
        today_trades = [t for t in poly_trades if t.get('timestamp', 0) >= midnight_ts]

        print(f"[{ts()}] ACTIVITY_API: {len(all_activity)} events, "
              f"{len(today_trades)} today, {len(redeemed)} redeemed slugs")

        if not today_trades:
            return {"total_pnl": 0, "avg_trade_cost": 0, "roi": 0,
                    "wins": 0, "losses": 0, "exits": 0, "pending": 0,
                    "trades": 0, "capital_deployed": 0}

        # Group by slug|outcome (same key as dashboard)
        grouped = {}
        for t in today_trades:
            key = f"{t.get('slug', '')}|{t.get('outcome', '')}"
            if key not in grouped:
                grouped[key] = {"buys": [], "sells": [], "slug": t.get('slug', '')}
            entry = {
                "size": float(t.get('size', 0) or 0),
                "price": float(t.get('price', 0) or 0),
                "timestamp": float(t.get('timestamp', 0) or 0),
            }
            if t.get('side') == 'SELL':
                grouped[key]["sells"].append(entry)
            else:
                grouped[key]["buys"].append(entry)

        # Process each group: FIFO match sells to buys, classify
        trades_out = []
        now_ts = time.time()

        for key, group in grouped.items():
            slug = group["slug"]
            if not group["buys"]:
                continue
            won = slug in redeemed

            group["buys"].sort(key=lambda x: x["timestamp"])
            group["sells"].sort(key=lambda x: x["timestamp"])

            # FIFO: match sells against buys (same as dashboard lines 972-985)
            sell_pool = [{"remaining": s["size"], "price": s["price"]} for s in group["sells"]]
            buy_exit = [{"exit_shares": 0.0, "exit_revenue": 0.0} for _ in group["buys"]]

            for sell in sell_pool:
                for i, buy in enumerate(group["buys"]):
                    if sell["remaining"] <= 0.001:
                        break
                    can_match = buy["size"] - buy_exit[i]["exit_shares"]
                    if can_match <= 0.001:
                        continue
                    matched = min(sell["remaining"], can_match)
                    buy_exit[i]["exit_shares"] += matched
                    buy_exit[i]["exit_revenue"] += matched * sell["price"]
                    sell["remaining"] -= matched

            # Classify each buy (same as dashboard lines 988-1053)
            for i, buy in enumerate(group["buys"]):
                info = buy_exit[i]
                cost = buy["size"] * buy["price"]
                exited_all = info["exit_shares"] >= buy["size"] - 0.02

                if exited_all:
                    pnl = info["exit_revenue"] - cost
                    trades_out.append({"status": "EXIT", "pnl": round(pnl, 2),
                                       "cost": round(cost, 2), "slug": slug})
                elif info["exit_shares"] > 0.001:
                    # Partial exit: split into EXIT portion + remainder
                    exit_cost = info["exit_shares"] * buy["price"]
                    exit_pnl = info["exit_revenue"] - exit_cost
                    trades_out.append({"status": "EXIT", "pnl": round(exit_pnl, 2),
                                       "cost": round(exit_cost, 2), "slug": slug})
                    remain = buy["size"] - info["exit_shares"]
                    remain_cost = remain * buy["price"]
                    age = now_ts - buy["timestamp"]
                    status = "WIN" if won else ("LOSS" if age > 1800 else "PENDING")
                    remain_pnl = (remain * (1 - buy["price"]) if status == "WIN"
                                  else (-remain_cost if status == "LOSS" else 0))
                    trades_out.append({"status": status, "pnl": round(remain_pnl, 2),
                                       "cost": round(remain_cost, 2), "slug": slug})
                else:
                    # No exit — resolve via redemption
                    age = now_ts - buy["timestamp"]
                    status = "WIN" if won else ("LOSS" if age > 1800 else "PENDING")
                    pnl = (buy["size"] * (1 - buy["price"]) if status == "WIN"
                           else (-cost if status == "LOSS" else 0))
                    trades_out.append({"status": status, "pnl": round(pnl, 2),
                                       "cost": round(cost, 2), "slug": slug})

        # Calculate ROI (dashboard formula: lines 1095-1106)
        total_pnl = sum(t["pnl"] for t in trades_out)
        total_cost = sum(t["cost"] for t in trades_out)
        window_ids = set(t["slug"] for t in trades_out)
        num_windows = len(window_ids)
        avg_trade_cost = total_cost / num_windows if num_windows > 0 else 0
        roi = total_pnl / avg_trade_cost if avg_trade_cost > 0 else 0

        wins = sum(1 for t in trades_out if t["status"] == "WIN")
        losses = sum(1 for t in trades_out if t["status"] == "LOSS")
        exits = sum(1 for t in trades_out if t["status"] == "EXIT")
        pending = sum(1 for t in trades_out if t["status"] == "PENDING")

        return {
            "total_pnl": total_pnl, "avg_trade_cost": avg_trade_cost,
            "roi": roi, "capital_deployed": total_cost,
            "wins": wins, "losses": losses, "exits": exits,
            "pending": pending, "trades": num_windows
        }
    except Exception as e:
        print(f"[{ts()}] ACTIVITY_API_ERROR: {e}")
        return None


def check_daily_roi():
    """
    Check cumulative daily ROI via Polymarket Activity API.
    Returns (should_halt, roi_data).
    """
    global trading_halted, capital_deployed
    try:
        roi_data = get_roi_from_activity_api()
        if roi_data is None:
            print(f"[{ts()}] ROI_CHECK: Activity API unavailable, skipping")
            return False, None

        daily_capital = roi_data['capital_deployed']
        daily_pnl = roi_data['total_pnl']
        daily_roi = roi_data['roi']
        avg_cost = roi_data.get('avg_trade_cost', 0)

        # Update session tracking with cumulative daily values
        capital_deployed = daily_capital

        print(f"[{ts()}] ROI CHECK: PnL=${daily_pnl:.2f} / AvgTrade=${avg_cost:.2f} "
              f"= {daily_roi*100:.1f}% | W:{roi_data['wins']} L:{roi_data['losses']} "
              f"E:{roi_data.get('exits', 0)} P:{roi_data.get('pending', 0)} "
              f"Windows:{roi_data['trades']} Capital=${daily_capital:.2f}")

        if daily_roi >= ROI_HALT_THRESHOLD and not trading_halted:
            trading_halted = True
            save_halt_state(daily_pnl, daily_capital, daily_roi)
            print()
            print("┌──────────────────────────────────────────────────────────┐")
            print(f"│  TRADING HALTED - Daily ROI: {daily_roi*100:.1f}% >= {ROI_HALT_THRESHOLD*100:.0f}%".ljust(57) + "│")
            print(f"│  PnL: ${daily_pnl:.2f} on ${daily_capital:.2f} deployed".ljust(57) + "│")
            print(f"│  W:{roi_data['wins']} L:{roi_data['losses']} E:{roi_data.get('exits',0)} ({roi_data['trades']} windows)".ljust(57) + "│")
            print(f"│  Bot idle until midnight EST reset".ljust(57) + "│")
            print("└──────────────────────────────────────────────────────────┘")
            print()
            try:
                send_telegram(f"TRADING HALTED\nDaily ROI: {daily_roi*100:.1f}% (target: {ROI_HALT_THRESHOLD*100:.0f}%)\nPnL: ${daily_pnl:.2f} on ${daily_capital:.2f}\nW:{roi_data['wins']} L:{roi_data['losses']} E:{roi_data.get('exits',0)}\nBot idle until midnight EST")
            except:
                pass
            log_event("TRADING_HALTED", "",
                pnl=daily_pnl, capital=daily_capital, roi=daily_roi)
            return True, roi_data

        return False, roi_data
    except Exception as e:
        print(f"[{ts()}] ROI_CHECK_ERROR: {e}")
        return False, None

# ============================================================================
# MAIN BOT
# ============================================================================

def main():
    global window_state, trades_log, error_count, clob_client
    global trading_halted, capital_deployed, cached_portfolio_total, daily_trade_shares

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

    # v1.46: Check daily ROI from Supabase at startup (overrides state file)
    # Always check daily ROI at startup (self-corrects stale halt files)
    print("  Checking daily ROI from Activity API...")
    halted, roi_data = check_daily_roi()
    if halted:
        print(f"  *** HALTED at startup: daily ROI {roi_data['roi']*100:.1f}% >= {ROI_HALT_THRESHOLD*100:.0f}% ***")
    elif trading_halted and roi_data and roi_data['roi'] < ROI_HALT_THRESHOLD:
        # Stale halt file from previous day — self-correct
        print(f"  *** Clearing stale halt: daily ROI {roi_data['roi']*100:.1f}% < {ROI_HALT_THRESHOLD*100:.0f}% ***")
        trading_halted = False
        try:
            os.remove(ROI_HALT_STATE_FILE)
        except:
            pass
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

                        # v1.46: Check daily cumulative ROI from Supabase
                        if not trading_halted:
                            check_daily_roi()

                        # 99c Sniper Resolution — runs in background to avoid blocking main loop
                        if window_state.get('capture_99c_fill_notified'):
                            # Snapshot all values before window_state gets reset
                            _bg_side = window_state.get('capture_99c_side')
                            _bg_shares = window_state.get('capture_99c_filled_up', 0) + window_state.get('capture_99c_filled_down', 0)
                            _bg_hedged = window_state.get('capture_99c_hedged', False)
                            _bg_hedge_price = window_state.get('capture_99c_hedge_price', 0)
                            _bg_entry_px = window_state.get('capture_99c_fill_price', CAPTURE_99C_BID_PRICE)
                            _bg_slug = last_slug

                            def _resolve_99c_outcome(_side=_bg_side, _shares=_bg_shares,
                                                     _hedged=_bg_hedged, _hedge_price=_bg_hedge_price,
                                                     _entry_px=_bg_entry_px, _slug=_bg_slug):
                                try:
                                    if _hedged:
                                        _pnl = -(_entry_px + _hedge_price - 1.00) * _shares
                                        _won = False
                                        print(f"[{ts()}] 99c SNIPER RESULT: HEDGED (loss avoided) P&L=${_pnl:.2f}")
                                    else:
                                        _won = None
                                        _pnl = 0
                                        for retry in range(6):
                                            try:
                                                _won = check_99c_outcome(_side, _slug)
                                                if _won is not None:
                                                    _pnl = _shares * (1.00 - _entry_px) if _won else -_shares * _entry_px
                                                    print(f"[{ts()}] 99c SNIPER RESULT: {'WIN' if _won else 'LOSS'} P&L=${_pnl:.2f}")
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
                                                _won = None
                                                _pnl = 0

                                    if _won is not None:
                                        session_stats['pnl'] += _pnl
                                        notify_99c_resolution(_side, _shares, _won, _pnl)
                                        event_type = "CAPTURE_99C_WIN" if _won else "CAPTURE_99C_LOSS"
                                        log_event(event_type, _slug,
                                            side=_side, shares=_shares,
                                            price=_entry_px, pnl=_pnl,
                                            details=json.dumps({
                                                "outcome": "WIN" if _won else "LOSS",
                                                "settlement_price": 1.00 if _won else 0.00,
                                                "hedged": _hedged
                                            }))
                                    else:
                                        pending_99c_resolutions.append({
                                            'slug': _slug, 'side': _side,
                                            'shares': _shares, 'entry_price': _entry_px,
                                            'timestamp': time.time()
                                        })
                                        print(f"[{ts()}] 99c SNIPER: Added to pending queue for later resolution")

                                    # Also handle auto_redeem and tick flush in background
                                    flush_ticks()
                                    try:
                                        from auto_redeem import check_and_claim
                                        claimable = check_and_claim()
                                        if claimable:
                                            total = sum(p['claimable_usdc'] for p in claimable)
                                            print(f"[{ts()}] 💰 CLAIMABLE: ${total:.2f} - check polymarket.com to claim!")
                                    except ImportError:
                                        pass
                                    except Exception as e:
                                        print(f"[{ts()}] REDEEM_CHECK_ERROR: {e}")
                                except Exception as e:
                                    print(f"[{ts()}] BG_RESOLVE_ERROR: {e}")

                            import threading
                            threading.Thread(target=_resolve_99c_outcome, daemon=True).start()
                            print(f"[{ts()}] 99c resolution moved to background — main loop continues immediately")
                        else:
                            flush_ticks()
                            try:
                                from auto_redeem import check_and_claim
                                claimable = check_and_claim()
                                if claimable:
                                    total = sum(p['claimable_usdc'] for p in claimable)
                                    print(f"[{ts()}] 💰 CLAIMABLE: ${total:.2f} - check polymarket.com to claim!")
                            except ImportError:
                                pass
                            except Exception as e:
                                print(f"[{ts()}] REDEEM_CHECK_ERROR: {e}")

                    # Cancel any open profit lock sell order (v1.58)
                    if window_state and window_state.get('profit_lock_order_id') and not window_state.get('profit_lock_filled'):
                        cancel_order(window_state['profit_lock_order_id'])
                        print(f"[{ts()}] 🔒 PROFIT_LOCK: Cancelled open sell order at window end")

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

                    # Portfolio balance for dynamic trade sizing
                    _pos_val, _usdc_val = get_portfolio_balance()
                    cached_portfolio_total = _pos_val + _usdc_val
                    if cached_portfolio_total > 0:
                        print(f"[{ts()}] 💰 Balance snapshot: ${cached_portfolio_total:.2f} (positions: ${_pos_val:.2f}, USDC: ${_usdc_val:.2f})")
                        # Lock trade size once per day (first window only)
                        if daily_trade_shares == 0:
                            _trade_budget = cached_portfolio_total * TRADE_SIZE_PCT
                            daily_trade_shares = math.ceil(_trade_budget / CAPTURE_99C_BID_PRICE)
                            _save_daily_trade_shares(daily_trade_shares)
                            print(f"[{ts()}] 📐 DAILY trade size LOCKED: {daily_trade_shares} shares (${_trade_budget:.2f} = {TRADE_SIZE_PCT*100:.0f}% of ${cached_portfolio_total:.2f})")
                        else:
                            _current_budget = cached_portfolio_total * TRADE_SIZE_PCT
                            _current_shares = math.ceil(_current_budget / CAPTURE_99C_BID_PRICE)
                            print(f"[{ts()}] 📐 Trade size: {daily_trade_shares} shares (locked) | current would be {_current_shares}")

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
                    # Don't skip the loop when holding a 99c position — exit checks must keep running
                    holding_99c = (window_state.get('capture_99c_fill_notified') and
                                   not window_state.get('capture_99c_exited'))
                    if not holding_99c:
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

                        # === INSTANT PROFIT LOCK (v1.58, fixed v1.60) ===
                        # After fill, update balance allowance then place sell at 99c
                        if PROFIT_LOCK_ENABLED and not window_state.get('profit_lock_order_id'):
                            sell_token = window_state['up_token'] if side == "UP" else window_state['down_token']
                            buy_token = window_state.get('capture_99c_token', 'unknown')
                            sell_price = PROFIT_LOCK_SELL_PRICE
                            sell_shares = filled

                            print(f"[{ts()}] 🔒 PROFIT_LOCK: buy_token={buy_token[:12]}... sell_token={sell_token[:12]}... shares={sell_shares:.0f}")

                            # Step 1: Update balance allowance so CLOB knows we hold these tokens
                            try:
                                from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
                                clob_client.update_balance_allowance(
                                    BalanceAllowanceParams(
                                        asset_type=AssetType.CONDITIONAL,
                                        token_id=sell_token
                                    )
                                )
                                print(f"[{ts()}] 🔒 PROFIT_LOCK: Balance allowance updated for {side} token")
                            except Exception as e:
                                print(f"[{ts()}] 🔒 PROFIT_LOCK: update_balance_allowance error (non-fatal): {e}")

                            # Step 2: Wait for shares to settle — poll every 0.5s for up to 5s
                            pl_success = False
                            pl_result = None
                            for pl_attempt in range(10):  # 10 x 0.5s = 5s max
                                # Check balance before attempting sell
                                try:
                                    bal = clob_client.get_balance_allowance(
                                        BalanceAllowanceParams(
                                            asset_type=AssetType.CONDITIONAL,
                                            token_id=sell_token
                                        )
                                    )
                                    bal_amount = float(bal.get('balance', 0)) if bal else 0
                                    print(f"[{ts()}] 🔒 PROFIT_LOCK: Balance check #{pl_attempt+1}: {bal_amount:.1f} shares (need {sell_shares:.0f})")
                                except Exception as e:
                                    bal_amount = 0
                                    print(f"[{ts()}] 🔒 PROFIT_LOCK: Balance check error: {e}")

                                if bal_amount >= sell_shares:
                                    # Balance sufficient — place sell
                                    print(f"[{ts()}] 🔒 PROFIT_LOCK: Placing sell {sell_shares:.0f} {side} @ {sell_price*100:.0f}c")
                                    pl_success, pl_result = place_limit_order(
                                        sell_token, sell_price, sell_shares,
                                        side="SELL", bypass_price_failsafe=True
                                    )
                                    if pl_success:
                                        break
                                    else:
                                        print(f"[{ts()}] 🔒 PROFIT_LOCK: Sell failed (attempt {pl_attempt+1}): {pl_result}")
                                        # Re-update allowance and retry
                                        try:
                                            clob_client.update_balance_allowance(
                                                BalanceAllowanceParams(
                                                    asset_type=AssetType.CONDITIONAL,
                                                    token_id=sell_token
                                                )
                                            )
                                        except Exception:
                                            pass

                                time.sleep(0.5)

                            if pl_success:
                                window_state['profit_lock_order_id'] = pl_result
                                print(f"[{ts()}] PROFIT_LOCK_CONFIRMED: Sell order placed successfully | {sell_shares:.0f} shares @ {sell_price*100:.0f}c | Order ID: {pl_result[:8]}...")
                                log_activity("PROFIT_LOCK_PLACED", {
                                    "side": side, "shares": sell_shares,
                                    "sell_price": sell_price, "order_id": pl_result,
                                    "attempts": pl_attempt + 1
                                })
                            else:
                                print(f"[{ts()}] PROFIT_LOCK_ERROR: Failed after {pl_attempt+1} attempts. Last error: {pl_result}")
                                log_activity("PROFIT_LOCK_FAILED", {
                                    "side": side, "shares": sell_shares,
                                    "error": str(pl_result), "attempts": pl_attempt + 1
                                })

                # === 60¢ HARD STOP CHECK (v1.34) ===
                # Exit immediately using FOK market orders if best bid <= 60¢
                # Uses best BID (not ask) to ensure real liquidity exists
                if (HARD_STOP_ENABLED and
                    window_state.get('capture_99c_fill_notified') and
                    not window_state.get('capture_99c_exited') and
                    not window_state.get('capture_99c_hedged')):

                    capture_side = window_state.get('capture_99c_side')

                    # Check if hard stop should trigger (based on best bid, requires consecutive ticks)
                    should_trigger, best_bid = check_hard_stop_trigger(books, capture_side)

                    if should_trigger:
                        window_state['hard_stop_consecutive_ticks'] += 1
                        ticks_so_far = window_state['hard_stop_consecutive_ticks']
                        if ticks_so_far >= HARD_STOP_CONSECUTIVE_REQUIRED:
                            print(f"[{ts()}] 🛑 EXIT TRIGGER: {capture_side} best bid at {best_bid*100:.0f}c <= {HARD_STOP_TRIGGER*100:.0f}c for {ticks_so_far} consecutive ticks")
                            ob_success, ob_pnl, ob_fills = execute_ob_exit(capture_side, books)
                            if not ob_success:
                                print(f"[{ts()}] OB EXIT failed, falling back to hard stop")
                                execute_hard_stop(capture_side, books)
                        else:
                            print(f"[{ts()}] ⚠️ HARD_STOP WARNING: tick {ticks_so_far}/{HARD_STOP_CONSECUTIVE_REQUIRED}: best_bid={best_bid*100:.0f}c <= {HARD_STOP_TRIGGER*100:.0f}c")
                    else:
                        if window_state['hard_stop_consecutive_ticks'] > 0:
                            print(f"[{ts()}] HARD_STOP: Reset consecutive counter (bid recovered to {best_bid*100:.0f}c)")
                        window_state['hard_stop_consecutive_ticks'] = 0

                # === PROFIT LOCK MONITOR (v1.58) ===
                # Check if sell order filled, or cancel if best bid dropped below threshold
                if (PROFIT_LOCK_ENABLED and
                    window_state.get('profit_lock_order_id') and
                    not window_state.get('profit_lock_filled') and
                    not window_state.get('capture_99c_exited')):

                    capture_side = window_state.get('capture_99c_side')
                    lock_order_id = window_state['profit_lock_order_id']

                    # Check if sell order filled
                    sell_status = get_order_status(lock_order_id)
                    if sell_status and sell_status.get('filled', 0) > 0:
                        filled_shares = sell_status['filled']
                        print(f"[{ts()}] 🔒✅ PROFIT_LOCK FILLED: Sold {filled_shares:.0f} {capture_side} @ 99c")
                        window_state['profit_lock_filled'] = True
                        window_state['capture_99c_exited'] = True
                        entry_px = window_state.get('capture_99c_fill_price', CAPTURE_99C_BID_PRICE)
                        lock_pnl = filled_shares * (PROFIT_LOCK_SELL_PRICE - entry_px)
                        log_activity("PROFIT_LOCK_FILLED", {
                            "side": capture_side, "shares": filled_shares,
                            "price": PROFIT_LOCK_SELL_PRICE
                        })
                        log_event("PROFIT_LOCK_FILLED", slug,
                            side=capture_side, shares=filled_shares,
                            price=PROFIT_LOCK_SELL_PRICE, pnl=lock_pnl)
                    elif not window_state.get('profit_lock_cancelled'):
                        # Check if we need to cancel (best bid dropped below 60c)
                        if capture_side == "UP":
                            bids = books.get('up_bids', [])
                        else:
                            bids = books.get('down_bids', [])

                        best_bid = float(bids[0]['price']) if bids else 0
                        if best_bid < PROFIT_LOCK_CANCEL_THRESHOLD:
                            print(f"[{ts()}] 🔒❌ PROFIT_LOCK CANCEL: {capture_side} bid={best_bid*100:.0f}c < {PROFIT_LOCK_CANCEL_THRESHOLD*100:.0f}c")
                            cancel_order(lock_order_id)
                            window_state['profit_lock_cancelled'] = True
                            log_activity("PROFIT_LOCK_CANCELLED", {
                                "side": capture_side, "best_bid": best_bid,
                                "reason": "bid_below_threshold"
                            })

                # === 99c OB-BASED EARLY EXIT CHECK ===
                # Exit early if OB shows sellers dominating for 3 consecutive ticks
                if (OB_EARLY_EXIT_ENABLED and
                    window_state.get('capture_99c_fill_notified') and
                    not window_state.get('capture_99c_exited') and
                    not window_state.get('capture_99c_hedged')):

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

                    # === DANGER EXIT — Confidence + Opponent Ask Gate (v1.56) ===
                    # Exit when danger score is high AND opponent ask confirms real uncertainty.
                    # On wins, opponent ask ≤8c. On losses, opponent ask was 65c.
                    if (DANGER_EXIT_ENABLED and
                        not window_state.get('capture_99c_exited') and
                        not window_state.get('capture_99c_hedged')):

                        d_score = danger_result['score']
                        opp_ask = danger_result['opponent_ask']

                        if d_score >= DANGER_EXIT_THRESHOLD and opp_ask > DANGER_EXIT_OPPONENT_ASK_MIN:
                            window_state['danger_exit_ticks'] = window_state.get('danger_exit_ticks', 0) + 1
                            d_ticks = window_state['danger_exit_ticks']
                            if d_ticks >= DANGER_EXIT_CONSECUTIVE_REQUIRED:
                                print(f"[{ts()}] 🚨 DANGER_EXIT: score={d_score:.2f} opp_ask={opp_ask*100:.0f}c ({d_ticks} ticks)")
                                log_activity("DANGER_EXIT", {
                                    "danger_score": d_score,
                                    "opponent_ask": opp_ask,
                                    "ticks": d_ticks,
                                    "confidence_drop": danger_result['confidence_drop'],
                                    "velocity": danger_result['velocity'],
                                })
                                ob_success, ob_pnl, ob_fills = execute_ob_exit(bet_side, books)
                                if not ob_success:
                                    print(f"[{ts()}] OB EXIT failed on danger exit, falling back to hard stop")
                                    execute_hard_stop(bet_side, books)
                            else:
                                print(f"[{ts()}] ⚠️ DANGER_WARNING: tick {d_ticks}/{DANGER_EXIT_CONSECUTIVE_REQUIRED}: score={d_score:.2f} opp_ask={opp_ask*100:.0f}c")
                        else:
                            if window_state.get('danger_exit_ticks', 0) > 0:
                                print(f"[{ts()}] DANGER_EXIT: Reset (score={d_score:.2f} opp_ask={opp_ask*100:.0f}c)")
                            window_state['danger_exit_ticks'] = 0

                    # Existing hedge check (will use danger_score in Phase 3)
                    check_99c_capture_hedge(books, remaining_secs)

                # === WINDOW END SAFETY EXIT (v1.60) ===
                # In final 10 seconds, exit if price below 80c — don't wait for 45c hard stop
                if (window_state.get('capture_99c_fill_notified') and
                    not window_state.get('capture_99c_exited') and
                    remaining_secs <= 10):

                    capture_side = window_state.get('capture_99c_side')
                    if capture_side == "UP":
                        safety_bids = books.get('up_bids', [])
                    else:
                        safety_bids = books.get('down_bids', [])
                    safety_best_bid = float(safety_bids[0]['price']) if safety_bids else 0

                    if safety_best_bid < WINDOW_END_SAFETY_PRICE:
                        print(f"[{ts()}] 🚨 SAFETY_EXIT: T-{remaining_secs:.0f}s, {capture_side} best bid {safety_best_bid*100:.0f}c < {WINDOW_END_SAFETY_PRICE*100:.0f}c — exiting to protect position")
                        log_activity("SAFETY_EXIT", {
                            "side": capture_side, "best_bid": safety_best_bid,
                            "remaining_secs": remaining_secs,
                            "trigger": f"bid<{WINDOW_END_SAFETY_PRICE*100:.0f}c at T-{remaining_secs:.0f}s"
                        })
                        ob_success, ob_pnl, ob_fills = execute_ob_exit(capture_side, books)
                        if not ob_success:
                            print(f"[{ts()}] SAFETY_EXIT: OB exit failed, falling back to hard stop")
                            execute_hard_stop(capture_side, books)

                # === FINAL_SECONDS LOGGING (v1.60) ===
                # Log every tick in last 15 seconds when holding a position — proof of monitoring
                if (remaining_secs <= 15 and
                    window_state.get('capture_99c_fill_notified') and
                    not window_state.get('capture_99c_exited')):

                    capture_side = window_state.get('capture_99c_side')
                    up_bids = books.get('up_bids', [])
                    dn_bids = books.get('down_bids', [])
                    up_bid = float(up_bids[0]['price']) * 100 if up_bids else 0
                    dn_bid = float(dn_bids[0]['price']) * 100 if dn_bids else 0
                    d_score = window_state.get('danger_score', 0)
                    print(f"[{ts()}] FINAL_SECONDS: T-{remaining_secs:.0f}s | UP bid: {up_bid:.0f}c | DN bid: {dn_bid:.0f}c | danger: {d_score:.2f} | monitoring: ACTIVE")

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

                    if ARB_ENABLED and abs(arb_imbalance) > MICRO_IMBALANCE_TOLERANCE:
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
                elif (remaining_secs <= 15 and
                      window_state.get('capture_99c_fill_notified') and
                      not window_state.get('capture_99c_exited')):
                    # v1.60: 5 ticks/sec in final 15 seconds when holding position
                    time.sleep(max(0.05, 0.2 - elapsed))
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
