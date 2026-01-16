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
    "version": "v1.3",
    "codename": "Neon Falcon",
    "date": "2026-01-16",
    "changes": "Fix: Cancel race condition - track pending hedge orders to prevent duplicates"
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

# Google Sheets logging
try:
    from sheets_logger import (sheets_log_event, sheets_log_window, init_sheets_logger,
                               buffer_tick, maybe_flush_ticks, flush_ticks)
    SHEETS_LOGGER_AVAILABLE = True
except ImportError:
    SHEETS_LOGGER_AVAILABLE = False
    sheets_log_event = lambda *args, **kwargs: False
    sheets_log_window = lambda *args, **kwargs: False
    init_sheets_logger = lambda: None
    buffer_tick = lambda *args, **kwargs: None
    maybe_flush_ticks = lambda: False
    flush_ticks = lambda: False

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
# ENTRY RESTRICTIONS
# ===========================================
MIN_TIME_FOR_ENTRY = 300           # Never enter with <5 minutes (300s) remaining

# ===========================================
# 99c BID CAPTURE STRATEGY (CONFIDENCE-BASED)
# ===========================================
CAPTURE_99C_ENABLED = True         # Enable/disable 99c capture strategy
CAPTURE_99C_MAX_SPEND = 5.00       # Max $5 per window on this strategy
CAPTURE_99C_BID_PRICE = 0.99       # Place bid at 99c
CAPTURE_99C_MIN_TIME = 10          # Need at least 10 seconds to settle order
CAPTURE_99C_MIN_CONFIDENCE = 0.95  # Only bet when 95%+ confident

# Time penalties: (max_time_remaining, penalty)
# Confidence = ask_price - time_penalty
# Less time = less penalty = higher confidence
CAPTURE_99C_TIME_PENALTIES = [
    (60,   0.00),   # <1 min: no penalty (locked in)
    (120,  0.03),   # 1-2 min: -3%
    (300,  0.08),   # 2-5 min: -8%
    (9999, 0.15),   # 5+ min: -15% (very uncertain)
]

# 99c Capture Hedge Protection
CAPTURE_99C_HEDGE_ENABLED = True        # Enable auto-hedge on confidence drop
CAPTURE_99C_HEDGE_THRESHOLD = 0.85      # Hedge if confidence drops below 85%

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
    """Log activity to shared JSONL file"""
    try:
        entry = {
            "timestamp": datetime.now().isoformat(),
            "bot": BOT_ID,
            "action": action,
            "details": details or {}
        }
        with open(ACTIVITY_LOG_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except:
        pass

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
        "started_mid_window": False,         # True if bot started mid-window (skip trading)
        "pairing_start_time": None,          # When PAIRING_MODE was entered
        "best_distance_seen": None,          # Best (lowest) distance from profit target (in cents)
        "pending_hedge_order_id": None,      # Track pending hedge order to prevent duplicates
        "pending_hedge_side": None,          # Which side the pending hedge is for (UP/DOWN)
    }

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
        sheets_log_event("PROFIT_PAIR", window_state.get('window_id', ''),
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
        sheets_log_event("LOSS_AVOID", window_state.get('window_id', ''),
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
    except:
        return None

def get_time_remaining(market):
    try:
        end_str = market.get('markets', [{}])[0].get('endDate', '')
        end_time = datetime.fromisoformat(end_str.replace('Z', '+00:00'))
        remaining = (end_time - datetime.now(timezone.utc)).total_seconds()
        if remaining < 0:
            return "ENDED", -1
        return f"{int(remaining)//60:02d}:{int(remaining)%60:02d}", remaining
    except:
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
            except:
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
    except:
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
    except:
        pass
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
    imb = get_imbalance()
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

    imb = get_imbalance()
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
    if window_state.get('started_mid_window'):
        status = "WAIT"
        reason = "waiting for fresh window"
    elif state == STATE_PAIRING:
        status = "PAIRING"
        reason = f"need {'DN' if imb > 0 else 'UP'}"
    elif up_shares > 0 or down_shares > 0:
        status = "PAIRED" if imb == 0 else "IMBAL"
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

    # Get Chainlink BTC price
    btc_str = ""
    btc_price = None
    if CHAINLINK_AVAILABLE and chainlink_feed:
        btc_price, btc_age = chainlink_feed.get_price_with_age()
        if btc_price:
            btc_str = f"BTC:${btc_price:,.0f}({btc_age}s) | "

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

    price_str = f"UP:{ask_up*100:2.0f}c DN:{ask_down*100:2.0f}c"
    print(f"[{ts()}] {status:7} | T-{ttc:3.0f}s | {btc_str}{price_str}{ob_str} | pos:{up_shares:.0f}/{down_shares:.0f} | {reason}")

    # Buffer tick for Google Sheets (batched upload)
    buffer_tick(
        window_state.get('window_id', ''),
        ttc, status, ask_up, ask_down, up_shares, down_shares,
        btc_price=btc_price, up_imb=up_imb, down_imb=down_imb, reason=reason
    )
    maybe_flush_ticks()

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

def cancel_order(order_id):
    try:
        clob_client.cancel(order_id)
        return True
    except:
        return False

def cancel_all_orders():
    try:
        clob_client.cancel_all()
        return True
    except:
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
    except:
        pass
    return {'filled': 0, 'original': 0, 'is_filled': False, 'fully_filled': False, 'price': 0, 'status': 'ERROR'}

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
        return {'side': 'UP', 'ask': ask_up, 'confidence': conf_up, 'penalty': penalty_up}

    # Check DOWN side
    conf_down, penalty_down = calculate_99c_confidence(ask_down, ttc)
    if conf_down >= CAPTURE_99C_MIN_CONFIDENCE:
        return {'side': 'DOWN', 'ask': ask_down, 'confidence': conf_down, 'penalty': penalty_down}

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
        sheets_log_event("CAPTURE_99C", window_state.get('window_id', ''),
                        side=side, ask_price=current_ask, shares=shares,
                        confidence=confidence, penalty=penalty, ttl=ttc)
        return True
    else:
        print(f"🎰 99c CAPTURE: ❌ Failed - {status}")
        print()
        return False


def check_99c_capture_hedge(books, ttc):
    """Monitor 99c capture position and hedge if confidence drops."""
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

    if current_ask == 0 or not opposite_asks:
        return

    # Recalculate confidence
    new_confidence, time_penalty = calculate_99c_confidence(current_ask, ttc)

    # Check if we should hedge
    if new_confidence < CAPTURE_99C_HEDGE_THRESHOLD:
        opposite_ask = float(opposite_asks[0]['price'])
        shares = window_state.get('capture_99c_shares', 0)

        if shares > 0 and opposite_ask < 0.50:  # Don't hedge if opposite too expensive
            print()
            print(f"┌─────────────── 99c HEDGE TRIGGERED ───────────────┐")
            print(f"│  Confidence dropped: {new_confidence*100:.0f}% < {CAPTURE_99C_HEDGE_THRESHOLD*100:.0f}% threshold".ljust(50) + "│")
            print(f"│  Bet: {bet_side} @ 99c | Now: {current_ask*100:.0f}c".ljust(50) + "│")
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

                sheets_log_event("99C_HEDGE", window_state.get('window_id', ''),
                               bet_side=bet_side, hedge_side=opposite_side,
                               hedge_price=opposite_ask, combined=combined, loss=total_loss)
            else:
                print(f"│  ❌ HEDGE FAILED: {status}".ljust(50) + "│")
                print(f"└───────────────────────────────────────────────────┘")
                print()


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
            print(f"🔄 PRE-ARB POSITION SYNC: local=({local_up},{local_down}) api=({api_up},{api_down})")
            window_state['filled_up_shares'] = api_up
            window_state['filled_down_shares'] = api_down
            save_trades()
            return False

    imb = get_imbalance()
    if imb != 0:
        print(f"[{ts()}] BLOCK_NEW_ARBS imbalance={imb} -> entering PAIRING_MODE")
        window_state['state'] = STATE_PAIRING
        window_state['pairing_start_time'] = time.time()
        window_state['best_distance_seen'] = float('inf')
        sheets_log_event("PAIRING_ENTRY", window_state.get('window_id', ''), imbalance=imb, reason="imbalance_detected")
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
                "direction": signal.direction
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
            "size_multiplier": size_multiplier
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
        sheets_log_event("ARB_ORDER", window_state.get('window_id', ''), side=first_side, price=first_bid, shares=q,
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
                sheets_log_event("ARB_FILL", window_state.get('window_id', ''), side=first_side, shares=first_fill_shares, price=first_bid)
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
            success_up = first_side == "UP"
            success_down = first_side == "DOWN"
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
    # EARLY BAIL CHECK - if stuck too long with no hope
    # Triggers if: 5+ min in pairing AND current distance > 20c AND best_ever > 8c
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

        # Early bail conditions: stuck too long with no hope of good hedge
        if time_in_pairing > 300 and current_distance > 20 and best_ever > 8:
            print(f"[{ts()}] EARLY_BAIL: {time_in_pairing:.0f}s elapsed, distance={current_distance:.0f}c, best_ever={best_ever:.0f}c")
            execute_bail(missing_side, missing_shares, missing_token, books)
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
        sheets_log_event("HARD_FLATTEN", window_state.get('window_id', ''),
                        side=excess_side, shares=excess_shares, ttl=ttc)

# ============================================================================
# TRADE LOGGING
# ============================================================================

def save_trades():
    try:
        with open("trades_smart.json", "w") as f:
            json.dump(trades_log, f, indent=2, default=str)
    except:
        pass

# ============================================================================
# MAIN BOT
# ============================================================================

def main():
    global window_state, trades_log, error_count, clob_client

    print("=" * 100)
    print(f"CHATGPT POLY BOT - SMART STRATEGY VERSION")
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

    print("Initializing Google Sheets logger...")
    if SHEETS_LOGGER_AVAILABLE:
        init_sheets_logger()
    else:
        print("  Sheets logger: DISABLED (module not found)")
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

                        # Log window end to Google Sheets
                        sheets_log_window(window_state)
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
                    cached_market = None
                    last_slug = slug
                    error_count = 0
                    session_stats['windows'] += 1

                    print()
                    print("=" * 100)
                    print(f"[{ts()}] NEW WINDOW: {slug}")
                    print(f"[{ts()}] Session: {session_stats['windows']} windows | {session_stats['paired']} paired | PnL: ${session_stats['pnl']:.2f}")
                    print("=" * 100)

                    # Log window start to Google Sheets
                    sheets_log_event("WINDOW_START", slug, session_windows=session_stats['windows'])

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

                    # Check if started mid-window (< 14 min remaining = not fresh)
                    if remaining_secs < 840:
                        window_state['started_mid_window'] = True
                        print(f"[{ts()}] STARTUP: Started mid-window (T-{remaining_secs:.0f}s), waiting for fresh window...")

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

                # Skip all trading if started mid-window
                if window_state.get('started_mid_window'):
                    time.sleep(max(0, 1 - (time.time() - cycle_start)))
                    continue

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
                if CAPTURE_99C_ENABLED and books and not window_state.get('capture_99c_used'):
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
                        print()
                        print(f"┌─────────────── 99c CAPTURE FILLED ───────────────┐")
                        print(f"│  ✅ {side}: {filled:.0f} shares filled @ ~99c".ljust(48) + "│")
                        print(f"│  💰 Expected profit: ${filled * 0.01:.2f}".ljust(48) + "│")
                        print(f"└──────────────────────────────────────────────────┘")
                        print()
                        window_state['capture_99c_fill_notified'] = True
                        sheets_log_event("CAPTURE_FILL", slug, side=side, shares=filled,
                                        pnl=filled * 0.01)

                # Check if 99c capture needs hedging (confidence dropped)
                if window_state.get('capture_99c_fill_notified') and not window_state.get('capture_99c_hedged'):
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
                sheets_log_event("ERROR", slug if slug else "unknown",
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
