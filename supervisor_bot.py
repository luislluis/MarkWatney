#!/usr/bin/env python3
"""
SUPERVISOR BOT — Independent Trading Analyst
=============================================
Standalone watchdog that monitors the Polymarket maker arb bot.
Tails the bot's log for its perspective, queries Polymarket APIs
for ground truth, compares the two, and writes audit results to Supabase.

This bot OBSERVES only — it does NOT place any trades.
"""

# ===========================================
# BOT VERSION
# ===========================================
BOT_VERSION = {
    "version": "v0.2",
    "codename": "Sharp Eye",
    "date": "2026-03-03",
    "changes": "Retarget to maker_arb_bot — new log format, wallet, multi-pair tracking"
}

import os
import sys
import signal
import time
import re
import json
import threading
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
import requests

# ===========================================
# TIMEZONE
# ===========================================
PST = ZoneInfo("America/Los_Angeles")
EST = ZoneInfo("America/New_York")

# ===========================================
# LOGGING SETUP
# ===========================================
class TeeLogger:
    """Writes output to both terminal and log file."""
    def __init__(self, filename):
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        self.terminal = sys.stdout
        self.log = open(filename, "a", buffering=1)
    def write(self, message):
        self.terminal.write(message)
        self.log.write(message)
    def flush(self):
        self.terminal.flush()
        self.log.flush()

LOG_FILE = os.path.expanduser("~/polybot/supervisor.log")
sys.stdout = TeeLogger(LOG_FILE)
sys.stderr = TeeLogger(LOG_FILE)

# ===========================================
# STARTUP BANNER
# ===========================================
print(f"\n{'='*60}")
print(f"SUPERVISOR {BOT_VERSION['codename']} ({BOT_VERSION['version']}) starting...")
print(f"Changes: {BOT_VERSION['changes']}")
print(f"Started: {datetime.now(PST).strftime('%Y-%m-%d %H:%M:%S PST')}")
print(f"Logging to: {LOG_FILE}")
print(f"{'='*60}\n")

# ===========================================
# ENVIRONMENT
# ===========================================
load_dotenv(os.path.expanduser("~/.env"))
# The maker arb bot derives its own trading wallet from the private key.
# We need the TRADING wallet for position/activity queries, not the funder.
# Detect from bot startup banner, or override via env.
WALLET_ADDRESS = os.getenv("MAKER_BOT_WALLET", "")
FUNDER_ADDRESS = os.getenv("WALLET_ADDRESS", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://qszosdrmnoglrkttdevz.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

if WALLET_ADDRESS:
    print(f"Trading wallet: {WALLET_ADDRESS[:10]}...{WALLET_ADDRESS[-6:]}")
else:
    print("Trading wallet: (will detect from bot startup banner)")
if FUNDER_ADDRESS:
    print(f"Funder wallet: {FUNDER_ADDRESS[:10]}...{FUNDER_ADDRESS[-6:]}")

# ===========================================
# SUPABASE CLIENT
# ===========================================
supabase_client = None
try:
    from supabase import create_client
    if SUPABASE_URL and SUPABASE_KEY:
        supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
        print(f"[SUPABASE] Connected")
    else:
        print("[SUPABASE] Missing URL or KEY — audit writes disabled")
except ImportError:
    print("[SUPABASE] supabase-py not installed — audit writes disabled")

AUDITS_TABLE = "Supervisor - Window Audits"
DAILY_TABLE = "Supervisor - Daily Summary"

# ===========================================
# HTTP SESSION
# ===========================================
http_session = requests.Session()
http_session.headers.update({"User-Agent": "PolybotSupervisor/0.2"})

# ===========================================
# CONSTANTS
# ===========================================
WINDOW_DURATION = 900  # 15 minutes
BOT_LOG_PATH = os.path.expanduser("~/polybot_solyasa/maker_bot_debug.log")

# API staleness protection
API_SETTLE_DELAY = 45       # Wait 45s after window end before querying API
API_RETRY_INTERVAL = 15     # Retry every 15s if stale
API_MAX_RETRIES = 3         # Max retries

# Bot liveness — maker arb bot has natural quiet periods of 5-10 minutes
# (after all pairs complete, waiting for window settlement, between windows)
STALE_LOG_THRESHOLD = 600   # 10 minutes — only flag truly dead bot
STARTUP_GRACE_PERIOD = 120  # Don't flag BOT_DOWN in first 2 min after supervisor start

# ===========================================
# REGEX PATTERNS FOR MAKER ARB BOT LOG
# ===========================================

# Status line: HH:MM:SS [STATUS ] T-XmXXs | P1:-/- | bid:X.XX/X.XX=X.XX | ask:X.XX/X.XX | ord:XF/XF | ...
RE_STATUS = re.compile(
    r'(\d{2}:\d{2}:\d{2})\s+'                     # timestamp
    r'\[(\w+)\s*\]\s+'                             # status (IDLE, PAIRED, RESCUE)
    r'T-(\d+)m(\d+)s'                              # TTL: minutes and seconds
)

# Info line with positions: pos:XU/XD imbal:X
RE_POS_INFO = re.compile(
    r'pos:(\d+\.?\d*)U/(\d+\.?\d*)D\s+imbal:(\d+\.?\d*)'
)

# Info line with prices: ask UP:X.XX DN:X.XX | bid UP:X.XX DN:X.XX | combined:X.XX
RE_PRICES_INFO = re.compile(
    r'ask UP:([\d.]+)\s+DN:([\d.]+)\s+\|\s+bid UP:([\d.]+)\s+DN:([\d.]+)\s+\|\s+combined:([\d.]+)'
)

# Slots info: slots:X/X [P/P/R]
RE_SLOTS = re.compile(r'slots:(\d+)/(\d+)\s+\[([^\]]*)\]')

# Paired count from status line: X/X paired
RE_PAIRED_COUNT = re.compile(r'(\d+)/(\d+)\s+paired')

# Market found: btc-updown-15m-XXXXXXXXXX
RE_MARKET_FOUND = re.compile(r'Market found:\s+(btc-updown-15m-\d+)')

# Fill: ✅ FILL: UP X.X shares @ X.XX
RE_FILL = re.compile(r'FILL:\s+(\w+)\s+([\d.]+)\s+shares?\s*@\s*([\d.]+)')

# Pair complete: 💰 PAIR#X PAIRED! UP@X.XX + DN@X.XX = X.XX | Profit: $X.XX (Xc/share, X.X%)
RE_PAIR_COMPLETE = re.compile(
    r'PAIR#(\d+)\s+PAIRED!\s+UP@([\d.]+)\s+\+\s+DN@([\d.]+)\s+=\s+([\d.]+)\s+\|\s+Profit:\s+\$([\d.]+)\s+\((\d+)c/share'
)

# Order placement: 🎯 P#X placing: UP@X.XX + DN@X.XX = X.XX, size=X.X
RE_ORDER_PLACING = re.compile(r'P#(\d+)\s+placing.*size=([\d.]+)')

# Individual order placed: 🎯 UP BUY placed @ X.XX x X.X
RE_ORDER_PLACED = re.compile(r'(\w+)\s+BUY\s+placed\s+@\s+([\d.]+)\s+x\s+([\d.]+)')

# Chase: P#X CHASE: need UP | filled@X.XX | chase:X.XX
RE_CHASE = re.compile(r'P#(\d+)\s+CHASE:\s+need\s+(\w+)')

# Cancelling orders before window close
RE_CANCEL_CLOSE = re.compile(r'cancelling orders before window close')

# Waiting for next window
RE_WAITING_NEXT = re.compile(r'Waiting\s+(\d+)s\s+for next window')

# Startup banner
RE_STARTUP = re.compile(r'MAKER ARB BOT')

# Wallet detection from startup
RE_WALLET = re.compile(r'Wallet:\s+(0x[0-9a-fA-F]+)')
RE_FUNDER = re.compile(r'Funder:\s+(0x[0-9a-fA-F]+)')

# Balance
RE_BALANCE = re.compile(r'Balance:\s+\$([\d.]+)')

# Rescue indicator: "need rescue" in status or [RESCUE ] status
RE_RESCUE_STATUS = re.compile(r'need rescue|RESCUE')

# Order check: ORDER_CHECK UP: status=live filled=0
RE_ORDER_CHECK = re.compile(r'ORDER_CHECK\s+(\w+):\s+status=(\w+)\s+filled=([\d.]+)')

# Redeem
RE_REDEEM = re.compile(r'\[REDEEM\]')

# Warning
RE_WARNING = re.compile(r'\[WARNING\]')

# Error
RE_ERROR = re.compile(r'\[ERROR\]|Error:|Traceback')

# Detected existing pairs from resume
RE_RESUME_PAIRS = re.compile(r'Detected\s+(\d+)\s+existing pairs')


# ===========================================
# WINDOW STATE TRACKING
# ===========================================
class WindowState:
    """Tracks what the supervisor observes about a single window."""

    def __init__(self, window_id):
        self.window_id = window_id
        self.window_start_ts = int(window_id.split('-')[-1]) if '-' in window_id else 0

        # Bot perspective (from log)
        self.bot_status = "IDLE"
        self.bot_up_shares = 0.0
        self.bot_down_shares = 0.0
        self.bot_imbalance = 0.0
        self.last_ttl = None

        # Multi-pair tracking
        self.pairs_placed = 0
        self.pairs_completed = 0
        self.max_pairs = 3
        self.pair_profits = []    # List of per-pair profit amounts
        self.pair_details = []    # List of (up_price, dn_price, combined, profit)
        self.total_size = 0.0     # Total shares per side across all pairs

        # Prices (from info lines)
        self.ask_up_history = []
        self.ask_down_history = []
        self.bid_up_history = []
        self.bid_down_history = []
        self.last_ask_up = 0.0
        self.last_ask_down = 0.0
        self.last_bid_up = 0.0
        self.last_bid_down = 0.0
        self.last_combined = 0.0

        # Events observed
        self.saw_order = False
        self.saw_fill = False
        self.saw_pair_complete = False
        self.saw_rescue = False
        self.saw_chase = False
        self.saw_cancel_close = False
        self.saw_waiting_next = False
        self.saw_resume_pairs = False
        self.saw_warning = False
        self.saw_error = False
        self.saw_redeem = False

        # Fill details
        self.fills = []           # List of (side, shares, price)
        self.bot_reported_pnl = None
        self.bot_fill_price = None  # Average fill price across all buys

        # Metadata
        self.first_log_ts = None
        self.last_log_ts = None
        self.observation_complete = True
        self.log_lines_seen = 0
        self.balance = None
        self.resumed_pairs = 0    # Pairs detected at startup (from previous window)

    def update_from_status(self, ts_str, status, ttl_min, ttl_sec):
        """Update state from a parsed status line."""
        self.bot_status = status
        self.last_ttl = int(ttl_min) * 60 + int(ttl_sec)
        self.log_lines_seen += 1

        now = time.time()
        if not self.first_log_ts:
            self.first_log_ts = now
        self.last_log_ts = now

    def update_positions(self, up_shares, down_shares, imbalance):
        """Update position tracking from info line."""
        self.bot_up_shares = max(self.bot_up_shares, float(up_shares))
        self.bot_down_shares = max(self.bot_down_shares, float(down_shares))
        self.bot_imbalance = float(imbalance)

    def update_prices(self, ask_up, ask_dn, bid_up, bid_dn, combined):
        """Update price tracking from info line."""
        self.last_ask_up = float(ask_up)
        self.last_ask_down = float(ask_dn)
        self.last_bid_up = float(bid_up)
        self.last_bid_down = float(bid_dn)
        self.last_combined = float(combined)
        self.ask_up_history.append(self.last_ask_up)
        self.ask_down_history.append(self.last_ask_down)
        self.bid_up_history.append(self.last_bid_up)
        self.bid_down_history.append(self.last_bid_down)

    def record_fill(self, side, shares, price):
        """Record a fill event."""
        self.saw_fill = True
        self.fills.append((side, float(shares), float(price)))

    def record_pair_complete(self, pair_num, up_price, dn_price, combined, profit):
        """Record a completed pair."""
        self.saw_pair_complete = True
        self.pairs_completed += 1
        self.pair_profits.append(float(profit))
        self.pair_details.append((float(up_price), float(dn_price), float(combined), float(profit)))
        if self.bot_reported_pnl is None:
            self.bot_reported_pnl = 0.0
        self.bot_reported_pnl += float(profit)

    def get_avg_fill_price(self):
        """Calculate average fill price across all buy fills."""
        if not self.fills:
            return None
        total_cost = sum(shares * price for _, shares, price in self.fills)
        total_shares = sum(shares for _, shares, _ in self.fills)
        return total_cost / total_shares if total_shares > 0 else None


# ===========================================
# POLYMARKET API — GROUND TRUTH
# ===========================================
def get_current_slug():
    """Calculate current BTC 15-min window slug."""
    current = int(time.time())
    window_start = (current // WINDOW_DURATION) * WINDOW_DURATION
    return f"btc-updown-15m-{window_start}"


def get_market_data(slug):
    """Fetch market metadata from gamma-api."""
    try:
        url = f"https://gamma-api.polymarket.com/events?slug={slug}"
        resp = http_session.get(url, timeout=5)
        data = resp.json()
        return data[0] if data else None
    except Exception as e:
        print(f"[API] Market data fetch failed: {e}")
        return None


def get_token_ids(market):
    """Extract UP and DOWN token IDs from market data."""
    try:
        clob_ids = market.get('markets', [{}])[0].get('clobTokenIds', '')
        clob_ids = clob_ids.replace('[', '').replace(']', '').replace('"', '')
        tokens = [t.strip() for t in clob_ids.split(',')]
        if len(tokens) >= 2:
            return tokens[0], tokens[1]
    except Exception as e:
        print(f"[API] Token ID extraction failed: {e}")
    return None, None


def fetch_positions(up_token, down_token):
    """Fetch current positions from Polymarket data-api."""
    if not WALLET_ADDRESS:
        return None, None
    try:
        url = f"https://data-api.polymarket.com/positions?user={WALLET_ADDRESS.lower()}"
        resp = http_session.get(url, timeout=5)
        positions = resp.json()
        up_shares = 0.0
        down_shares = 0.0
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
        print(f"[API] Position fetch failed: {e}")
        return None, None


def fetch_activity_for_window(slug):
    """Fetch trade activity for a specific window slug."""
    if not WALLET_ADDRESS:
        return []
    try:
        url = f"https://data-api.polymarket.com/activity?user={WALLET_ADDRESS.lower()}&limit=100"
        resp = http_session.get(url, timeout=5)
        all_activity = resp.json()
        window_trades = [a for a in all_activity if a.get('slug') == slug and a.get('type') == 'TRADE']
        return window_trades
    except Exception as e:
        print(f"[API] Activity fetch failed: {e}")
        return []


def get_market_resolution(market):
    """Check market resolution via gamma-api outcomePrices."""
    try:
        outcome_prices = market.get('markets', [{}])[0].get('outcomePrices', '')
        if outcome_prices:
            prices = json.loads(outcome_prices) if isinstance(outcome_prices, str) else outcome_prices
            if len(prices) >= 2:
                up_price = float(prices[0])
                down_price = float(prices[1])
                if up_price > 0.9:
                    return "UP"
                elif down_price > 0.9:
                    return "DOWN"
    except Exception:
        pass
    return None


# ===========================================
# CLASSIFICATION ENGINE
# ===========================================
def classify_window(ws, api_up, api_down, api_trades, market_outcome):
    """
    Classify a completed window for the maker arb bot.
    Returns: (classification, severity, exit_type, diagnosis, recommendation)
    """
    has_position = ws.bot_up_shares > 0 or ws.bot_down_shares > 0
    api_has_position = (api_up is not None and api_up > 0) or (api_down is not None and api_down > 0)

    # --- No trade windows ---
    if not has_position and not api_has_position and not ws.saw_fill:
        return "IDLE", "ok", None, None, None

    # --- Position mismatch check ---
    position_mismatch = False
    if api_up is not None and api_down is not None:
        up_diff = abs(ws.bot_up_shares - api_up)
        down_diff = abs(ws.bot_down_shares - api_down)
        if up_diff > 0.5 or down_diff > 0.5:
            position_mismatch = True

    # --- Price discrepancy check ---
    price_discrepancy = False
    api_fill_price = None
    if api_trades:
        buy_trades = [t for t in api_trades if (t.get('side') or '').upper() != 'SELL']
        if buy_trades:
            total_cost = sum(float(t.get('price', 0)) * float(t.get('size', 0)) for t in buy_trades)
            total_size = sum(float(t.get('size', 0)) for t in buy_trades)
            if total_size > 0:
                api_fill_price = total_cost / total_size
                bot_avg = ws.get_avg_fill_price()
                if bot_avg and abs(bot_avg - api_fill_price) > 0.01:
                    price_discrepancy = True

    # --- Maker ARB classification ---
    return _classify_maker_arb(ws, api_up, api_down, market_outcome, position_mismatch, price_discrepancy, api_fill_price)


def _classify_maker_arb(ws, api_up, api_down, outcome, pos_mismatch, price_mismatch, api_price):
    """Classify a maker ARB bot window."""

    # All pairs completed successfully
    if ws.pairs_completed > 0 and ws.bot_imbalance == 0 and not ws.saw_rescue:
        total_profit = sum(ws.pair_profits)
        detail_str = ", ".join(
            f"P#{i}: {d[0]:.2f}+{d[1]:.2f}={d[2]:.2f} (${d[3]:.2f})"
            for i, d in enumerate(ws.pair_details)
        )
        diag = f"{ws.pairs_completed} pair(s) completed cleanly. {detail_str}. Total profit: ${total_profit:.2f}."
        return "ARB_PAIRED_WIN", "ok", "settlement", diag, None

    # Rescue was needed (one leg filled first, had to chase second)
    if ws.saw_rescue and ws.pairs_completed > 0 and ws.bot_imbalance == 0:
        total_profit = sum(ws.pair_profits)
        diag = (f"{ws.pairs_completed} pair(s) completed but rescue was needed. "
                f"One leg filled first, chased second. Total profit: ${total_profit:.2f}.")
        return "ARB_PAIRED_WIN", "ok", "settlement", diag, "Rescue chase succeeded. Monitor chase frequency — frequent rescues suggest order book is thin."

    # Rescue ongoing / imbalance at window end
    if ws.saw_rescue and ws.bot_imbalance > 0:
        filled_sides = {}
        for side, shares, price in ws.fills:
            filled_sides[side] = filled_sides.get(side, 0) + shares
        up_filled = filled_sides.get("UP", 0)
        dn_filled = filled_sides.get("DOWN", 0)
        stranded_side = "UP" if up_filled > dn_filled else "DOWN"
        stranded_shares = abs(up_filled - dn_filled)
        diag = (f"Imbalance at window end: {stranded_shares:.0f} {stranded_side} shares stranded. "
                f"Filled UP:{up_filled:.0f} DN:{dn_filled:.0f}. Chase/rescue did not complete in time.")
        return "UNPAIRED_RESCUE", "critical", "rescue", diag, "Second leg failed to fill. Consider increasing chase aggressiveness or skipping thin markets."

    # Had fills but imbalanced (no explicit rescue marker)
    if ws.saw_fill and ws.bot_imbalance > 0:
        diag = f"Position imbalance at window end: UP:{ws.bot_up_shares:.0f} DN:{ws.bot_down_shares:.0f} imbal:{ws.bot_imbalance:.0f}."
        return "UNPAIRED_BAIL", "critical", "bail", diag, "One leg filled but pair never completed. Check order book depth."

    # Some pairs completed but with losses (combined > $1.00 — unlikely with max 0.96 but track it)
    if ws.pairs_completed > 0:
        for i, (up_p, dn_p, combined, profit) in enumerate(ws.pair_details):
            if combined > 1.0:
                diag = f"Pair #{i} combined cost {combined:.2f} > $1.00. Loss on this pair."
                return "ARB_PAIRED_LOSS", "medium", "settlement", diag, "Entry threshold may be too loose."
        # All pairs profitable
        total_profit = sum(ws.pair_profits)
        return "ARB_PAIRED_WIN", "ok", "settlement", f"{ws.pairs_completed} pair(s), ${total_profit:.2f} profit.", None

    # Fills happened but no pair completed
    if ws.saw_fill and ws.pairs_completed == 0:
        diag = f"Fills detected but no pair completed. Fills: {ws.fills}."
        return "UNPAIRED_BAIL", "critical", "bail", diag, "Orders filled but pair never closed. Investigate why."

    # Position mismatch
    if pos_mismatch:
        diag = f"Bot claimed UP:{ws.bot_up_shares:.1f} DN:{ws.bot_down_shares:.1f} but API shows UP:{api_up:.1f} DN:{api_down:.1f}."
        return "POSITION_MISMATCH", "medium", None, diag, "Investigate position tracking — API and bot disagree."

    # Price discrepancy
    if price_mismatch:
        bot_avg = ws.get_avg_fill_price()
        diag = f"Bot avg fill price {bot_avg*100:.0f}c but API shows {api_price*100:.0f}c."
        return "PRICE_DISCREPANCY", "medium", None, diag, None

    return "IDLE", "ok", None, None, None


# ===========================================
# PNL CALCULATION
# ===========================================
def calculate_verified_pnl(ws, api_trades, market_outcome, classification):
    """Calculate verified P&L from API data."""
    # For arb bot, P&L = sum of pair profits (all pairs settle at $1.00 each side)
    # Bot-reported P&L from pair completions is reliable, use as fallback
    if not api_trades:
        return ws.bot_reported_pnl or 0.0

    buys = [t for t in api_trades if (t.get('side') or '').upper() != 'SELL']
    sells = [t for t in api_trades if (t.get('side') or '').upper() == 'SELL']

    total_buy_cost = sum(float(t.get('price', 0)) * float(t.get('size', 0)) for t in buys)
    total_sell_revenue = sum(float(t.get('price', 0)) * float(t.get('size', 0)) for t in sells)

    if sells:
        return total_sell_revenue - total_buy_cost

    # If settled, winning shares pay $1 each
    winning_shares = 0
    for t in buys:
        outcome_str = (t.get('outcome') or '').upper()
        if outcome_str in ('UP', 'YES') and market_outcome == 'UP':
            winning_shares += float(t.get('size', 0))
        elif outcome_str in ('DOWN', 'NO') and market_outcome == 'DOWN':
            winning_shares += float(t.get('size', 0))

    settlement_payout = winning_shares * 1.0
    return settlement_payout - total_buy_cost


# ===========================================
# SUPABASE WRITES
# ===========================================
strategy_mode = "arb"  # Maker arb bot is always ARB

def write_audit(ws, classification, severity, exit_type, diagnosis, recommendation,
                api_up, api_down, api_fill_price, pnl_verified, market_outcome):
    """Write a window audit row to Supabase."""
    if not supabase_client:
        print(f"[AUDIT] {ws.window_id} -> {classification} ({severity}) — Supabase disabled")
        return

    window_start_dt = datetime.fromtimestamp(ws.window_start_ts, tz=PST)

    data = {
        "window_id": ws.window_id,
        "audit_timestamp": datetime.now(PST).isoformat(),
        "window_start": window_start_dt.isoformat(),
        "classification": classification,
        "severity": severity,
        "strategy_mode": strategy_mode,
        "bot_status": ws.bot_status,
        "bot_up_shares": float(ws.bot_up_shares),
        "bot_down_shares": float(ws.bot_down_shares),
        "api_up_shares": float(api_up) if api_up is not None else None,
        "api_down_shares": float(api_down) if api_down is not None else None,
        "bot_fill_price": ws.get_avg_fill_price(),
        "api_fill_price": float(api_fill_price) if api_fill_price else None,
        "pnl_bot": float(ws.bot_reported_pnl) if ws.bot_reported_pnl else 0,
        "pnl_verified": float(pnl_verified) if pnl_verified else 0,
        "entry_confidence": None,
        "entry_ttl": int(ws.last_ttl) if ws.last_ttl else None,
        "exit_type": exit_type,
        "exit_price": None,
        "market_outcome": market_outcome,
        "ob_depth_at_entry": None,
        "diagnosis": diagnosis,
        "recommendation": recommendation,
        "observation_complete": ws.observation_complete,
        "api_verified": api_up is not None,
        "details": json.dumps({
            "log_lines": ws.log_lines_seen,
            "pairs_completed": ws.pairs_completed,
            "pairs_placed": ws.pairs_placed,
            "pair_details": ws.pair_details,
            "fills": ws.fills,
            "imbalance": ws.bot_imbalance,
            "last_combined": ws.last_combined,
            "balance": ws.balance,
            "resumed_pairs": ws.resumed_pairs,
            "events": {
                "fill": ws.saw_fill,
                "pair_complete": ws.saw_pair_complete,
                "rescue": ws.saw_rescue,
                "chase": ws.saw_chase,
                "cancel_close": ws.saw_cancel_close,
                "warning": ws.saw_warning,
                "error": ws.saw_error,
            }
        }),
    }

    def _do_write():
        try:
            supabase_client.table(AUDITS_TABLE).upsert(data, on_conflict="window_id").execute()
            print(f"[SUPABASE] Audit written: {ws.window_id} -> {classification}")
        except Exception as e:
            print(f"[SUPABASE] Audit write failed: {e}")

    threading.Thread(target=_do_write, daemon=True).start()


def update_daily_summary(date_str, audits_today):
    """Update the daily summary row in Supabase."""
    if not supabase_client:
        return

    total = len(audits_today)
    idle = sum(1 for a in audits_today if a[0] == "IDLE")
    traded = total - idle

    clean_wins = sum(1 for a in audits_today if a[0] == "ARB_PAIRED_WIN")
    unpaired = sum(1 for a in audits_today if a[0] in ("UNPAIRED_BAIL", "UNPAIRED_RESCUE"))
    bails = sum(1 for a in audits_today if a[0] == "UNPAIRED_BAIL")
    hard_stops = sum(1 for a in audits_today if a[0] == "HARD_STOP")
    danger_exits = sum(1 for a in audits_today if a[0] == "DANGER_EXIT")
    profit_locks = 0  # Not applicable for maker arb bot

    pnl_verified = sum(a[1] for a in audits_today if a[1])
    pnl_bot = sum(a[2] for a in audits_today if a[2])

    pair_rate = (clean_wins / traded * 100) if traded > 0 else 0
    win_rate = (clean_wins / traded * 100) if traded > 0 else 0

    # Pattern analysis
    patterns = []
    if unpaired > 0:
        pct = unpaired / traded * 100 if traded > 0 else 0
        patterns.append(f"UNPAIRED: {unpaired} ({pct:.0f}% of trades) — second leg failing to fill")
    if hard_stops > 0:
        patterns.append(f"HARD_STOP: {hard_stops} — bid collapse triggered emergency exit")
    rescue_count = sum(1 for a in audits_today if a[0] == "ARB_PAIRED_WIN" and "rescue" in str(a).lower())
    if rescue_count > 0:
        patterns.append(f"RESCUES: {rescue_count} — pairs that needed chase to complete")
    pattern_text = "; ".join(patterns) if patterns else "All trades clean."

    # Recommendations
    recs = []
    if unpaired > 0 and traded > 0 and (unpaired / traded) > 0.2:
        recs.append("Unpaired rate >20%. Consider increasing book depth requirements for second leg.")
    if rescue_count > traded * 0.5 and traded > 2:
        recs.append("Over 50% of trades needed rescue. Order book may be too thin for current size.")
    rec_text = " ".join(recs) if recs else None

    data = {
        "date": date_str,
        "total_windows": total,
        "idle_windows": idle,
        "traded_windows": traded,
        "clean_wins": clean_wins,
        "unpaired": unpaired,
        "bails": bails,
        "hard_stops": hard_stops,
        "danger_exits": danger_exits,
        "profit_locks": profit_locks,
        "pnl_verified": float(pnl_verified),
        "pnl_bot_reported": float(pnl_bot),
        "pair_rate": float(pair_rate),
        "win_rate": float(win_rate),
        "pattern_analysis": pattern_text,
        "recommendations": rec_text,
    }

    def _do_write():
        try:
            supabase_client.table(DAILY_TABLE).upsert(data, on_conflict="date").execute()
            print(f"[SUPABASE] Daily summary updated: {date_str}")
        except Exception as e:
            print(f"[SUPABASE] Daily summary write failed: {e}")

    threading.Thread(target=_do_write, daemon=True).start()


# ===========================================
# LOG FILE TAILER
# ===========================================
class LogTailer:
    """Tails a log file, yielding new lines as they appear."""

    def __init__(self, path):
        self.path = path
        self.fh = None
        self._open()

    def _open(self):
        """Open file and seek to end."""
        try:
            self.fh = open(self.path, 'r')
            self.fh.seek(0, 2)  # Seek to end
            print(f"[TAILER] Opened {self.path} (seeking to end)")
        except FileNotFoundError:
            print(f"[TAILER] File not found: {self.path} — will retry")
            self.fh = None

    def read_lines(self):
        """Read any new lines from the file. Returns list of lines."""
        if not self.fh:
            self._open()
            if not self.fh:
                return []

        lines = []
        try:
            while True:
                line = self.fh.readline()
                if not line:
                    break
                lines.append(line.rstrip('\n'))
        except Exception as e:
            print(f"[TAILER] Read error: {e}")
            self.fh = None

        return lines


# ===========================================
# LOG LINE PARSER
# ===========================================
def parse_log_line(line, ws):
    """Parse a single maker arb bot log line and update window state."""
    if not ws or not line:
        return

    now = time.time()
    if not ws.first_log_ts:
        ws.first_log_ts = now
    ws.last_log_ts = now

    # --- Status line: HH:MM:SS [STATUS ] T-XmXXs | ... ---
    m = RE_STATUS.match(line)
    if m:
        ts_str, status, ttl_min, ttl_sec = m.groups()
        ws.update_from_status(ts_str, status.strip(), ttl_min, ttl_sec)

        # Check for rescue status
        if status.strip() == "RESCUE" or RE_RESCUE_STATUS.search(line):
            ws.saw_rescue = True

        # Extract paired count: X/X paired
        pm = RE_PAIRED_COUNT.search(line)
        if pm:
            ws.pairs_completed = max(ws.pairs_completed, int(pm.group(1)))

    # --- Info line with positions ---
    pm = RE_POS_INFO.search(line)
    if pm:
        ws.update_positions(pm.group(1), pm.group(2), pm.group(3))

    # --- Info line with prices ---
    pm = RE_PRICES_INFO.search(line)
    if pm:
        ws.update_prices(pm.group(1), pm.group(2), pm.group(3), pm.group(4), pm.group(5))

    # --- Slots info ---
    sm = RE_SLOTS.search(line)
    if sm:
        ws.pairs_placed = max(ws.pairs_placed, int(sm.group(1)))
        ws.max_pairs = int(sm.group(2))

    # --- Fill event ---
    fm = RE_FILL.search(line)
    if fm:
        side, shares, price = fm.group(1), fm.group(2), fm.group(3)
        ws.record_fill(side, shares, price)

    # --- Pair complete ---
    pcm = RE_PAIR_COMPLETE.search(line)
    if pcm:
        pair_num = pcm.group(1)
        up_price = pcm.group(2)
        dn_price = pcm.group(3)
        combined = pcm.group(4)
        profit = pcm.group(5)
        ws.record_pair_complete(pair_num, up_price, dn_price, combined, profit)

    # --- Order placement ---
    if RE_ORDER_PLACING.search(line):
        ws.saw_order = True

    if RE_ORDER_PLACED.search(line):
        ws.saw_order = True

    # --- Chase ---
    if RE_CHASE.search(line):
        ws.saw_chase = True
        ws.saw_rescue = True  # Chase implies rescue was needed

    # --- Cancel before close ---
    if RE_CANCEL_CLOSE.search(line):
        ws.saw_cancel_close = True

    # --- Waiting for next window ---
    if RE_WAITING_NEXT.search(line):
        ws.saw_waiting_next = True

    # --- Resume pairs detection ---
    rpm = RE_RESUME_PAIRS.search(line)
    if rpm:
        ws.saw_resume_pairs = True
        ws.resumed_pairs = int(rpm.group(1))

    # --- Balance ---
    bm = RE_BALANCE.search(line)
    if bm:
        ws.balance = float(bm.group(1))

    # --- Warning ---
    if RE_WARNING.search(line):
        ws.saw_warning = True

    # --- Error ---
    if RE_ERROR.search(line):
        ws.saw_error = True

    # --- Redeem ---
    if RE_REDEEM.search(line):
        ws.saw_redeem = True


# ===========================================
# MAIN SUPERVISOR LOOP
# ===========================================
supervisor_start_time = time.time()

# Track completed audits for daily summary: list of (classification, pnl_verified, pnl_bot)
daily_audits = []
daily_date = datetime.now(EST).strftime("%Y-%m-%d")


def finalize_window(ws, cached_market):
    """Finalize a window: query API, classify, write audit, update daily summary."""
    global daily_audits, daily_date

    if not ws or ws.window_id == "":
        return

    print(f"\n[SUPERVISOR] Finalizing window: {ws.window_id}")
    print(f"  Bot perspective: status={ws.bot_status} pos={ws.bot_up_shares:.0f}/{ws.bot_down_shares:.0f} imbal={ws.bot_imbalance:.0f}")
    print(f"  Pairs: {ws.pairs_completed} completed / {ws.pairs_placed} placed (max {ws.max_pairs})")
    print(f"  Events: fill={ws.saw_fill} pair={ws.saw_pair_complete} rescue={ws.saw_rescue} "
          f"chase={ws.saw_chase} order={ws.saw_order}")
    if ws.pair_profits:
        print(f"  Pair profits: {['${:.2f}'.format(p) for p in ws.pair_profits]}")

    # --- Wait for API settlement ---
    print(f"  Waiting {API_SETTLE_DELAY}s for API settlement...")
    time.sleep(API_SETTLE_DELAY)

    # --- Fetch ground truth ---
    api_up, api_down = None, None
    api_trades = []
    market_outcome = None

    if cached_market:
        up_token, down_token = get_token_ids(cached_market)

        # Retry loop for stale API data
        for attempt in range(API_MAX_RETRIES):
            if up_token and down_token:
                api_up, api_down = fetch_positions(up_token, down_token)

            # If bot showed fills but API shows 0, it's likely stale
            bot_has_fills = ws.bot_up_shares > 0 or ws.bot_down_shares > 0
            api_shows_zero = (api_up is not None and api_up == 0 and api_down is not None and api_down == 0)

            if bot_has_fills and api_shows_zero and attempt < API_MAX_RETRIES - 1:
                print(f"  [RETRY] API returned 0/0 but bot shows fills. Retry {attempt+1}/{API_MAX_RETRIES} in {API_RETRY_INTERVAL}s...")
                time.sleep(API_RETRY_INTERVAL)
                continue
            break

        # Fetch activity trades for this window
        api_trades = fetch_activity_for_window(ws.window_id)

        # Check resolution
        market_outcome = get_market_resolution(cached_market)

    print(f"  API reality: pos={api_up}/{api_down} trades={len(api_trades)} outcome={market_outcome}")

    # --- Classify ---
    classification, severity, exit_type, diagnosis, recommendation = classify_window(
        ws, api_up, api_down, api_trades, market_outcome
    )

    # --- Calculate verified P&L ---
    pnl_verified = calculate_verified_pnl(ws, api_trades, market_outcome, classification)

    # --- Get API fill price ---
    api_fill_price = None
    buy_trades = [t for t in api_trades if (t.get('side') or '').upper() != 'SELL']
    if buy_trades:
        total_cost = sum(float(t.get('price', 0)) * float(t.get('size', 0)) for t in buy_trades)
        total_size = sum(float(t.get('size', 0)) for t in buy_trades)
        if total_size > 0:
            api_fill_price = total_cost / total_size

    # --- Print audit result ---
    icon = {"ok": "✓", "medium": "●", "high": "▲", "critical": "✗"}.get(severity, "?")
    print(f"\n  ╔══════════════════════════════════════╗")
    print(f"  ║ {icon} {classification:34} ║")
    print(f"  ╠══════════════════════════════════════╣")
    if diagnosis:
        for i in range(0, len(diagnosis), 36):
            chunk = diagnosis[i:i+36]
            print(f"  ║ {chunk:36} ║")
    if recommendation:
        print(f"  ║ {'':36} ║")
        print(f"  ║ REC: {recommendation[:31]:31} ║")
    print(f"  ║ P&L verified: ${pnl_verified:+.2f}{' ':19} ║")
    print(f"  ╚══════════════════════════════════════╝\n")

    # --- Write to Supabase ---
    write_audit(ws, classification, severity, exit_type, diagnosis, recommendation,
                api_up, api_down, api_fill_price, pnl_verified, market_outcome)

    # --- Track for daily summary ---
    current_date = datetime.now(EST).strftime("%Y-%m-%d")
    if current_date != daily_date:
        if daily_audits:
            update_daily_summary(daily_date, daily_audits)
        daily_audits = []
        daily_date = current_date

    daily_audits.append((classification, pnl_verified, ws.bot_reported_pnl or 0))
    update_daily_summary(daily_date, daily_audits)


def main():
    global WALLET_ADDRESS, supervisor_start_time, daily_audits, daily_date

    print("[SUPERVISOR] Starting main loop...")
    print(f"  Bot log: {BOT_LOG_PATH}")
    print(f"  Strategy mode: {strategy_mode}")
    print()

    tailer = LogTailer(BOT_LOG_PATH)
    current_ws = None
    last_slug = None
    last_slug_ts = 0           # Timestamp from the slug (monotonically increasing)
    cached_market = None
    last_line_time = time.time()
    bot_down_reported = False
    pending_finalize = None     # (ws, market) waiting to be finalized

    while True:
        cycle_start = time.time()

        try:
            # --- Read new log lines ---
            lines = tailer.read_lines()

            for line in lines:
                last_line_time = time.time()
                bot_down_reported = False  # Reset on any new line

                # --- Detect wallet from bot startup ---
                wallet_m = RE_WALLET.search(line)
                if wallet_m and not WALLET_ADDRESS:
                    WALLET_ADDRESS = wallet_m.group(1)
                    print(f"[SUPERVISOR] Detected trading wallet: {WALLET_ADDRESS}")

                funder_m = RE_FUNDER.search(line)
                if funder_m:
                    print(f"[SUPERVISOR] Detected funder wallet: {funder_m.group(1)}")

                # --- Detect bot startup ---
                if RE_STARTUP.search(line):
                    print(f"[SUPERVISOR] Bot startup detected: MAKER ARB BOT")

                # --- Detect window transition via Market found ---
                # The bot outputs "Market found:" many times per search.
                # Only transition when the slug timestamp INCREASES (new window).
                mf = RE_MARKET_FOUND.search(line)
                if mf:
                    new_slug = mf.group(1)
                    new_ts = int(new_slug.split('-')[-1])

                    # Only transition to a NEWER window (ignore duplicate/old slugs)
                    if new_ts > last_slug_ts:
                        # Queue finalization of previous window
                        if current_ws and last_slug:
                            prev_ws = current_ws
                            prev_market = cached_market
                            threading.Thread(
                                target=finalize_window,
                                args=(prev_ws, prev_market),
                                daemon=True
                            ).start()

                        # Start new window
                        last_slug = new_slug
                        last_slug_ts = new_ts
                        current_ws = WindowState(new_slug)
                        cached_market = get_market_data(new_slug)

                        window_time = datetime.fromtimestamp(new_ts, tz=PST).strftime('%H:%M')
                        print(f"\n[SUPERVISOR] Tracking window: {new_slug} ({window_time})")

                # Parse the line
                if current_ws:
                    parse_log_line(line, current_ws)

            # --- Bot liveness check ---
            elapsed_since_line = time.time() - last_line_time
            since_startup = time.time() - supervisor_start_time

            if (elapsed_since_line > STALE_LOG_THRESHOLD
                    and since_startup > STARTUP_GRACE_PERIOD
                    and not bot_down_reported):
                print(f"[SUPERVISOR] ⚠ BOT_DOWN: No log output for {elapsed_since_line:.0f}s")
                bot_down_reported = True
                if current_ws:
                    current_ws.bot_status = "DOWN"

            # --- Maintain loop cadence ---
            elapsed = time.time() - cycle_start
            time.sleep(max(0.1, 1.0 - elapsed))

        except KeyboardInterrupt:
            print("\n[SUPERVISOR] Shutting down...")
            if current_ws and daily_audits:
                update_daily_summary(daily_date, daily_audits)
            break
        except Exception as e:
            print(f"[SUPERVISOR] ERROR: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(2)


def ws_is_stale(ws, current_window_start):
    """Check if window state belongs to a previous window."""
    return ws.window_start_ts < current_window_start


# ===========================================
# SIGNAL HANDLER
# ===========================================
def signal_handler(sig, frame):
    print("\n[SUPERVISOR] Signal received, shutting down...")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


if __name__ == "__main__":
    main()
