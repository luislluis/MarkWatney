#!/usr/bin/env python3
"""
SUPERVISOR BOT — Independent Trading Analyst
=============================================
Standalone watchdog that monitors the Polymarket trading bot.
Tails the bot's log for its perspective, queries Polymarket APIs
for ground truth, compares the two, and writes audit results to Supabase.

This bot OBSERVES only — it does NOT place any trades.
"""

# ===========================================
# BOT VERSION
# ===========================================
BOT_VERSION = {
    "version": "v0.1",
    "codename": "Hawk Eye",
    "date": "2026-03-03",
    "changes": "Initial supervisor bot — log tailing, API verification, Supabase audits"
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
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://qszosdrmnoglrkttdevz.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

if WALLET_ADDRESS:
    print(f"Wallet: {WALLET_ADDRESS[:10]}...{WALLET_ADDRESS[-6:]}")
else:
    print("WARNING: WALLET_ADDRESS not found in ~/.env")

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
http_session.headers.update({"User-Agent": "PolybotSupervisor/0.1"})

# ===========================================
# CONSTANTS
# ===========================================
WINDOW_DURATION = 900  # 15 minutes
BOT_LOG_PATH = os.path.expanduser("~/polybot/bot.log")

# API staleness protection
API_SETTLE_DELAY = 45       # Wait 45s after window end before querying API
API_RETRY_INTERVAL = 15     # Retry every 15s if stale
API_MAX_RETRIES = 3         # Max retries

# Bot liveness
STALE_LOG_THRESHOLD = 30    # seconds with no new log lines = BOT_DOWN
STARTUP_GRACE_PERIOD = 60   # Don't flag BOT_DOWN in first 60s after supervisor start

# ===========================================
# REGEX PATTERNS FOR LOG PARSING
# ===========================================

# Status line: [HH:MM:SS] STATUS  | T-XXXs | ... | pos:X/X | reason
RE_STATUS = re.compile(
    r'\[(\d{2}:\d{2}:\d{2})\]\s+'        # timestamp
    r'(\w+)\s+\|'                          # status
    r'\s+T-\s*(\d+)s\s*\|'                # TTL
    r'.*?'                                 # middle (BTC, prices, etc)
    r'\|\s*pos:(\d+\.?\d*)/(\d+\.?\d*)'   # positions
    r'\s*\|\s*(.+)',                        # reason
    re.DOTALL
)

# Price extraction from status line: UP:XXc DN:XXc
RE_PRICES = re.compile(r'UP:(\d+)c\s+DN:(\d+)c')

# Danger score: D:X.XX
RE_DANGER = re.compile(r'D:(\d+\.\d+)')

# Confidence: XX%/YY%
RE_CONFIDENCE = re.compile(r'(\w+):(\d+)%/(\d+)%')

# Event markers
RE_STARTUP = re.compile(r'POLYBOT\s+(\S+)\s+\((\S+)\)\s+starting')
RE_ARB_ENABLED = re.compile(r'ARB_ENABLED\s*=\s*(True|False)', re.IGNORECASE)
RE_WINDOW_COMPLETE = re.compile(r'WINDOW COMPLETE')
RE_FILL_DETECTED = re.compile(r'ORDER_FILL_DETECTED:\s+(\w+)\s+([\d.]+)\s+shares')
RE_CAPTURE_FILL = re.compile(r'99c CAPTURE FILLED|CAPTURE_FILL')
RE_PROFIT_LOCK = re.compile(r'PROFIT_LOCK.*(?:Sell filled|FILLED)', re.IGNORECASE)
RE_HARD_STOP = re.compile(r'HARD_STOP.*TRIGGER|HARD_STOP_EXIT')
RE_DANGER_EXIT = re.compile(r'DANGER_EXIT')
RE_BAIL = re.compile(r'BAIL|bail.*imbalance|EARLY_BAIL', re.IGNORECASE)
RE_PAIRING = re.compile(r'PAIRING_MODE|Entered PAIRING')
RE_HALTED = re.compile(r'TRADING HALTED')
RE_TREND_BLOCK = re.compile(r'TREND_GUARD_BLOCK')
RE_HARD_FLATTEN = re.compile(r'HARD_FLATTEN')
RE_NEW_WINDOW = re.compile(r'NEW WINDOW|Window slug changed')
RE_PNL_EXTRACT = re.compile(r'P&?L[:\s]*\$?([-+]?\d+\.?\d*)')


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
        self.bot_fill_price = None
        self.entry_confidence = None
        self.entry_ttl = None
        self.last_ttl = None
        self.danger_score = 0.0
        self.ob_depth = None

        # Events observed from log
        self.saw_order = False
        self.saw_fill = False
        self.saw_capture_fill = False
        self.saw_profit_lock = False
        self.saw_hard_stop = False
        self.saw_danger_exit = False
        self.saw_bail = False
        self.saw_pairing = False
        self.saw_halted = False
        self.saw_trend_block = False
        self.saw_hard_flatten = False
        self.saw_window_complete = False
        self.fill_side = None
        self.bot_reported_pnl = None

        # Prices seen
        self.ask_up_history = []
        self.ask_down_history = []
        self.last_ask_up = 0.0
        self.last_ask_down = 0.0

        # Metadata
        self.first_log_ts = None
        self.last_log_ts = None
        self.observation_complete = True
        self.log_lines_seen = 0

    def update_from_status(self, ts_str, status, ttl, up_shares, down_shares, reason):
        """Update state from a parsed status line."""
        self.bot_status = status
        self.last_ttl = int(ttl)
        self.log_lines_seen += 1

        # Track positions (only increase, matching bot's own rule)
        self.bot_up_shares = max(self.bot_up_shares, float(up_shares))
        self.bot_down_shares = max(self.bot_down_shares, float(down_shares))

        now = time.time()
        if not self.first_log_ts:
            self.first_log_ts = now
        self.last_log_ts = now

    def update_prices(self, ask_up_cents, ask_down_cents):
        """Update price tracking from status line."""
        ask_up = int(ask_up_cents) / 100.0
        ask_down = int(ask_down_cents) / 100.0
        self.last_ask_up = ask_up
        self.last_ask_down = ask_down
        self.ask_up_history.append(ask_up)
        self.ask_down_history.append(ask_down)

    def update_danger(self, score):
        """Update danger score from status line."""
        self.danger_score = max(self.danger_score, float(score))

    def update_confidence(self, side, conf_pct, threshold_pct):
        """Update confidence from status line."""
        conf = int(conf_pct) / 100.0
        if self.entry_confidence is None or conf > self.entry_confidence:
            self.entry_confidence = conf
        if self.entry_ttl is None and self.last_ttl:
            self.entry_ttl = self.last_ttl


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
    try:
        url = f"https://data-api.polymarket.com/activity?user={WALLET_ADDRESS.lower()}&limit=100"
        resp = http_session.get(url, timeout=5)
        all_activity = resp.json()
        # Filter to this slug
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
def classify_window(ws, api_up, api_down, api_trades, market_outcome, strategy_mode):
    """
    Classify a completed window by comparing bot log observations with API ground truth.

    Returns: (classification, severity, exit_type, diagnosis, recommendation)
    """
    has_position = ws.bot_up_shares > 0 or ws.bot_down_shares > 0
    api_has_position = (api_up is not None and api_up > 0) or (api_down is not None and api_down > 0)

    # --- No trade windows ---
    if not has_position and not api_has_position:
        if ws.saw_halted:
            return "ROI_HALTED", "ok", None, "Bot halted — daily ROI target reached.", None
        if ws.saw_trend_block:
            return "TREND_BLOCKED", "ok", None, "Entry blocked by BTC trend filter.", None
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
                if ws.bot_fill_price and abs(ws.bot_fill_price - api_fill_price) > 0.01:
                    price_discrepancy = True

    # --- Strategy-specific classification ---
    if strategy_mode == "arb":
        return _classify_arb(ws, api_up, api_down, market_outcome, position_mismatch, price_discrepancy, api_fill_price)
    else:
        return _classify_99c(ws, api_up, api_down, market_outcome, position_mismatch, price_discrepancy, api_fill_price)


def _classify_arb(ws, api_up, api_down, outcome, pos_mismatch, price_mismatch, api_price):
    """Classify an ARB-mode window."""

    both_legs = ws.bot_up_shares > 0 and ws.bot_down_shares > 0

    # Hard stop / flatten
    if ws.saw_hard_stop or ws.saw_hard_flatten:
        diag = _diagnose_hard_stop(ws)
        return "HARD_STOP", "high", "hard_stop", diag, "Review hard stop trigger threshold. Consider earlier exit via danger score."

    # Bail / rescue
    if ws.saw_bail:
        diag = _diagnose_unpaired(ws)
        return "UNPAIRED_BAIL", "critical", "bail", diag, "Check order book depth before placing second leg. Consider skipping thin markets."

    # Pairing entered but only one leg
    if ws.saw_pairing and not both_legs:
        diag = _diagnose_unpaired(ws)
        return "UNPAIRED_RESCUE", "critical", "rescue", diag, "Second leg never filled. Review pairing timeout and book depth requirements."

    # Both legs filled
    if both_legs:
        min_shares = min(ws.bot_up_shares, ws.bot_down_shares)
        combined = ws.last_ask_up + ws.last_ask_down if ws.last_ask_up and ws.last_ask_down else 1.0
        if combined < 1.0:
            return "ARB_PAIRED_WIN", "ok", "settlement", f"Both legs filled. Combined cost {combined*100:.0f}c < $1.00. Locked profit.", None
        else:
            return "ARB_PAIRED_LOSS", "medium", "settlement", f"Both legs filled but combined cost {combined*100:.0f}c >= $1.00.", "Entry threshold may be too loose."

    # Position mismatch
    if pos_mismatch:
        diag = f"Bot claimed UP:{ws.bot_up_shares:.1f} DN:{ws.bot_down_shares:.1f} but API shows UP:{api_up:.1f} DN:{api_down:.1f}."
        return "POSITION_MISMATCH", "medium", None, diag, "Investigate position tracking — API and bot disagree."

    # Price discrepancy
    if price_mismatch:
        diag = f"Bot logged fill at {ws.bot_fill_price*100:.0f}c but API shows {api_price*100:.0f}c. Delta: {abs(ws.bot_fill_price - api_price)*100:.1f}c."
        return "PRICE_DISCREPANCY", "medium", None, diag, None

    return "IDLE", "ok", None, None, None


def _classify_99c(ws, api_up, api_down, outcome, pos_mismatch, price_mismatch, api_price):
    """Classify a 99c-sniper-mode window."""

    has_fill = ws.saw_capture_fill or ws.saw_fill

    # Hard stop
    if ws.saw_hard_stop or ws.saw_hard_flatten:
        diag = _diagnose_hard_stop(ws)
        return "SNIPE_HARD_STOP", "high", "hard_stop", diag, "Hard stop fired. Review bid collapse speed and trigger threshold."

    # Danger exit
    if ws.saw_danger_exit:
        diag = f"Danger exit triggered. Score: {ws.danger_score:.2f}. Entry confidence: {ws.entry_confidence*100:.0f}% at T-{ws.entry_ttl}s."
        return "SNIPE_DANGER_EXIT", "medium", "danger_exit", diag, "Danger score crossed threshold. Market showed reversal signals."

    # Profit lock filled
    if ws.saw_profit_lock and has_fill:
        return "SNIPE_PROFIT_LOCK", "ok", "profit_lock", "99c capture filled, profit lock sell at 99c completed. 4c/share locked.", None

    # Filled but no profit lock — check outcome
    if has_fill:
        if outcome:
            fill_side = ws.fill_side or ("UP" if ws.bot_up_shares > ws.bot_down_shares else "DOWN")
            if fill_side == outcome:
                return "SNIPE_WIN", "ok", "settlement", f"99c capture {fill_side} won at settlement.", None
            else:
                conf_str = f"{ws.entry_confidence*100:.0f}%" if ws.entry_confidence else "??"
                ttl_str = f"T-{ws.entry_ttl}s" if ws.entry_ttl else "T-??s"
                diag = f"99c capture {fill_side} lost. Entered at {conf_str} confidence, {ttl_str}."
                if ws.entry_confidence and ws.entry_confidence < 0.98:
                    rec = f"Confidence was {conf_str} — consider raising CAPTURE_99C_MIN_CONFIDENCE to 0.98."
                else:
                    rec = "High confidence entry still lost. Market reversed unexpectedly."
                return "SNIPE_LOSS", "high", "settlement", diag, rec
        else:
            # Not resolved yet — shouldn't happen if we waited, mark as pending
            return "SNIPE_WIN", "ok", "settlement", "99c capture filled, awaiting resolution.", None

    # Profit lock miss
    if ws.saw_capture_fill and not ws.saw_profit_lock:
        return "PROFIT_LOCK_MISS", "medium", None, "99c capture filled but profit lock sell did not complete.", "Check if profit lock sell was placed and whether it was cancelled due to bid drop."

    # Position mismatch
    if pos_mismatch:
        diag = f"Bot claimed UP:{ws.bot_up_shares:.1f} DN:{ws.bot_down_shares:.1f} but API shows UP:{api_up:.1f} DN:{api_down:.1f}."
        return "POSITION_MISMATCH", "medium", None, diag, "Investigate position tracking."

    # Price discrepancy
    if price_mismatch:
        diag = f"Bot logged fill at {ws.bot_fill_price*100:.0f}c but API shows {api_price*100:.0f}c."
        return "PRICE_DISCREPANCY", "medium", None, diag, None

    return "IDLE", "ok", None, None, None


def _diagnose_hard_stop(ws):
    """Generate diagnosis for a hard stop event."""
    parts = []
    if ws.ask_up_history and ws.ask_down_history:
        max_price = max(max(ws.ask_up_history), max(ws.ask_down_history))
        min_bid = 1.0 - max_price  # Approximate worst bid from opposing ask
        parts.append(f"Leading side peaked at {max_price*100:.0f}c")
    if ws.danger_score > 0:
        parts.append(f"max danger score: {ws.danger_score:.2f}")
    if ws.entry_confidence:
        parts.append(f"entry confidence: {ws.entry_confidence*100:.0f}%")
    if ws.entry_ttl:
        parts.append(f"entry at T-{ws.entry_ttl}s")
    return "Hard stop triggered. " + ", ".join(parts) + "." if parts else "Hard stop triggered — insufficient log data for diagnosis."


def _diagnose_unpaired(ws):
    """Generate diagnosis for unpaired/bail events."""
    parts = []
    if ws.bot_up_shares > 0 and ws.bot_down_shares == 0:
        parts.append(f"UP leg filled ({ws.bot_up_shares:.0f} shares) but DOWN never filled")
    elif ws.bot_down_shares > 0 and ws.bot_up_shares == 0:
        parts.append(f"DOWN leg filled ({ws.bot_down_shares:.0f} shares) but UP never filled")
    else:
        parts.append(f"Imbalanced: UP:{ws.bot_up_shares:.0f} DN:{ws.bot_down_shares:.0f}")
    return ". ".join(parts) + "."


# ===========================================
# PNL CALCULATION
# ===========================================
def calculate_verified_pnl(ws, api_trades, market_outcome, classification):
    """Calculate verified P&L from API data."""
    if not api_trades or not market_outcome:
        return ws.bot_reported_pnl or 0.0

    buys = [t for t in api_trades if (t.get('side') or '').upper() != 'SELL']
    sells = [t for t in api_trades if (t.get('side') or '').upper() == 'SELL']

    total_buy_cost = sum(float(t.get('price', 0)) * float(t.get('size', 0)) for t in buys)
    total_sell_revenue = sum(float(t.get('price', 0)) * float(t.get('size', 0)) for t in sells)

    # If profit lock sold, P&L = sell revenue - buy cost
    if sells:
        return total_sell_revenue - total_buy_cost

    # If settled, winning shares pay $1 each
    winning_shares = 0
    for t in buys:
        outcome = (t.get('outcome') or '').upper()
        if outcome in ('UP', 'YES') and market_outcome == 'UP':
            winning_shares += float(t.get('size', 0))
        elif outcome in ('DOWN', 'NO') and market_outcome == 'DOWN':
            winning_shares += float(t.get('size', 0))

    settlement_payout = winning_shares * 1.0
    return settlement_payout - total_buy_cost


# ===========================================
# SUPABASE WRITES
# ===========================================
def write_audit(ws, classification, severity, exit_type, diagnosis, recommendation,
                api_up, api_down, api_fill_price, pnl_verified, market_outcome):
    """Write a window audit row to Supabase."""
    if not supabase_client:
        print(f"[AUDIT] {ws.window_id} → {classification} ({severity}) — Supabase disabled")
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
        "bot_fill_price": float(ws.bot_fill_price) if ws.bot_fill_price else None,
        "api_fill_price": float(api_fill_price) if api_fill_price else None,
        "pnl_bot": float(ws.bot_reported_pnl) if ws.bot_reported_pnl else 0,
        "pnl_verified": float(pnl_verified) if pnl_verified else 0,
        "entry_confidence": float(ws.entry_confidence) if ws.entry_confidence else None,
        "entry_ttl": int(ws.entry_ttl) if ws.entry_ttl else None,
        "exit_type": exit_type,
        "exit_price": None,  # TODO: extract from log if available
        "market_outcome": market_outcome,
        "ob_depth_at_entry": float(ws.ob_depth) if ws.ob_depth else None,
        "diagnosis": diagnosis,
        "recommendation": recommendation,
        "observation_complete": ws.observation_complete,
        "api_verified": api_up is not None,
        "details": json.dumps({
            "log_lines": ws.log_lines_seen,
            "max_danger": ws.danger_score,
            "events": {
                "order": ws.saw_order,
                "fill": ws.saw_fill,
                "capture_fill": ws.saw_capture_fill,
                "profit_lock": ws.saw_profit_lock,
                "hard_stop": ws.saw_hard_stop,
                "danger_exit": ws.saw_danger_exit,
                "bail": ws.saw_bail,
                "pairing": ws.saw_pairing,
            }
        }),
    }

    def _do_write():
        try:
            supabase_client.table(AUDITS_TABLE).upsert(data, on_conflict="window_id").execute()
            print(f"[SUPABASE] Audit written: {ws.window_id} → {classification}")
        except Exception as e:
            print(f"[SUPABASE] Audit write failed: {e}")

    threading.Thread(target=_do_write, daemon=True).start()


def update_daily_summary(date_str, audits_today):
    """Update the daily summary row in Supabase."""
    if not supabase_client:
        return

    total = len(audits_today)
    idle = sum(1 for a in audits_today if a[0] in ("IDLE", "TREND_BLOCKED", "ROI_HALTED"))
    traded = total - idle

    clean_wins = sum(1 for a in audits_today if a[0] in ("ARB_PAIRED_WIN", "SNIPE_WIN", "SNIPE_PROFIT_LOCK"))
    unpaired = sum(1 for a in audits_today if a[0] in ("UNPAIRED_BAIL", "UNPAIRED_RESCUE"))
    bails = sum(1 for a in audits_today if a[0] == "UNPAIRED_BAIL")
    hard_stops = sum(1 for a in audits_today if a[0] in ("HARD_STOP", "SNIPE_HARD_STOP"))
    danger_exits = sum(1 for a in audits_today if a[0] == "SNIPE_DANGER_EXIT")
    profit_locks = sum(1 for a in audits_today if a[0] == "SNIPE_PROFIT_LOCK")

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
    if danger_exits > 0:
        patterns.append(f"DANGER_EXIT: {danger_exits} — reversal signals detected")
    pattern_text = "; ".join(patterns) if patterns else "All trades clean."

    # Recommendations
    recs = []
    if unpaired > 0 and traded > 0 and (unpaired / traded) > 0.2:
        recs.append("Unpaired rate >20%. Consider increasing book depth requirements for second leg.")
    if hard_stops > 1:
        recs.append(f"{hard_stops} hard stops today. Consider raising hard stop trigger from 45c to 55c.")
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
# MAIN SUPERVISOR LOOP
# ===========================================
strategy_mode = "99c_sniper"  # Default; updated when startup banner is parsed
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
    print(f"  Bot perspective: status={ws.bot_status} pos={ws.bot_up_shares:.0f}/{ws.bot_down_shares:.0f}")
    print(f"  Events: fill={ws.saw_fill} capture={ws.saw_capture_fill} profit_lock={ws.saw_profit_lock} "
          f"hard_stop={ws.saw_hard_stop} bail={ws.saw_bail} pairing={ws.saw_pairing}")

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
        ws, api_up, api_down, api_trades, market_outcome, strategy_mode
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
        # Word-wrap diagnosis to fit
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
        # Day rolled over — finalize previous day and start fresh
        if daily_audits:
            update_daily_summary(daily_date, daily_audits)
        daily_audits = []
        daily_date = current_date

    daily_audits.append((classification, pnl_verified, ws.bot_reported_pnl or 0))
    update_daily_summary(daily_date, daily_audits)


def parse_log_line(line, ws):
    """Parse a single bot log line and update window state."""
    if not ws or not line:
        return

    # --- Status line ---
    m = RE_STATUS.match(line)
    if m:
        ts_str, status, ttl, up, down, reason = m.groups()
        ws.update_from_status(ts_str, status, ttl, up, down, reason)

        # Extract prices
        pm = RE_PRICES.search(line)
        if pm:
            ws.update_prices(pm.group(1), pm.group(2))

        # Extract danger score
        dm = RE_DANGER.search(line)
        if dm:
            ws.update_danger(dm.group(1))

        # Extract confidence
        cm = RE_CONFIDENCE.search(line)
        if cm:
            ws.update_confidence(cm.group(1), cm.group(2), cm.group(3))
        return

    # --- Event markers ---
    if RE_FILL_DETECTED.search(line):
        m = RE_FILL_DETECTED.search(line)
        ws.saw_fill = True
        ws.fill_side = m.group(1)

    if RE_CAPTURE_FILL.search(line):
        ws.saw_capture_fill = True

    if RE_PROFIT_LOCK.search(line):
        ws.saw_profit_lock = True

    if RE_HARD_STOP.search(line):
        ws.saw_hard_stop = True

    if RE_DANGER_EXIT.search(line):
        ws.saw_danger_exit = True

    if RE_BAIL.search(line):
        ws.saw_bail = True

    if RE_PAIRING.search(line):
        ws.saw_pairing = True

    if RE_HALTED.search(line):
        ws.saw_halted = True

    if RE_TREND_BLOCK.search(line):
        ws.saw_trend_block = True

    if RE_HARD_FLATTEN.search(line):
        ws.saw_hard_flatten = True

    if RE_WINDOW_COMPLETE.search(line):
        ws.saw_window_complete = True

    # Extract P&L from log
    pnl_m = RE_PNL_EXTRACT.search(line)
    if pnl_m and ("Session PnL" in line or "PROFIT" in line or "LOSS" in line):
        try:
            ws.bot_reported_pnl = float(pnl_m.group(1))
        except ValueError:
            pass


def main():
    global strategy_mode, supervisor_start_time, daily_audits, daily_date

    print("[SUPERVISOR] Starting main loop...")
    print(f"  Bot log: {BOT_LOG_PATH}")
    print(f"  Strategy mode: {strategy_mode} (will update from bot startup banner)")
    print()

    tailer = LogTailer(BOT_LOG_PATH)
    current_ws = None
    last_slug = None
    cached_market = None
    last_line_time = time.time()
    bot_down_reported = False

    while True:
        cycle_start = time.time()

        try:
            # --- Read new log lines ---
            lines = tailer.read_lines()

            for line in lines:
                last_line_time = time.time()
                bot_down_reported = False  # Reset on any new line

                # Check for startup banner (strategy mode detection)
                startup_m = RE_STARTUP.search(line)
                if startup_m:
                    codename = startup_m.group(1)
                    version = startup_m.group(2)
                    print(f"[SUPERVISOR] Bot startup detected: {codename} {version}")
                    # Check for ARB_ENABLED in nearby lines
                    if 'ARB_ENABLED = True' in line or 'ARB_ENABLED=True' in line:
                        strategy_mode = "arb"
                        print(f"[SUPERVISOR] Strategy mode: ARB")

                # Detect ARB_ENABLED explicitly
                arb_m = RE_ARB_ENABLED.search(line)
                if arb_m:
                    strategy_mode = "arb" if arb_m.group(1).lower() == 'true' else "99c_sniper"
                    print(f"[SUPERVISOR] Strategy mode detected: {strategy_mode}")

                # --- Detect window transition ---
                current_slug = get_current_slug()

                if current_slug != last_slug:
                    # Finalize previous window (in background to not block parsing)
                    if current_ws and last_slug:
                        prev_ws = current_ws
                        prev_market = cached_market
                        threading.Thread(
                            target=finalize_window,
                            args=(prev_ws, prev_market),
                            daemon=True
                        ).start()

                    # Start new window
                    last_slug = current_slug
                    current_ws = WindowState(current_slug)
                    cached_market = get_market_data(current_slug)

                    window_ts = int(current_slug.split('-')[-1])
                    window_time = datetime.fromtimestamp(window_ts, tz=PST).strftime('%H:%M')
                    print(f"\n[SUPERVISOR] Tracking window: {current_slug} ({window_time})")

                # Parse the line
                if current_ws:
                    parse_log_line(line, current_ws)

            # --- Check for window transition even without log lines ---
            current_slug = get_current_slug()
            if current_slug != last_slug:
                if current_ws and last_slug:
                    prev_ws = current_ws
                    prev_market = cached_market
                    threading.Thread(
                        target=finalize_window,
                        args=(prev_ws, prev_market),
                        daemon=True
                    ).start()

                last_slug = current_slug
                current_ws = WindowState(current_slug)
                cached_market = get_market_data(current_slug)

                window_ts = int(current_slug.split('-')[-1])
                window_time = datetime.fromtimestamp(window_ts, tz=PST).strftime('%H:%M')
                print(f"\n[SUPERVISOR] Tracking window: {current_slug} ({window_time})")

            # --- Bot liveness check ---
            elapsed_since_line = time.time() - last_line_time
            since_startup = time.time() - supervisor_start_time

            if (elapsed_since_line > STALE_LOG_THRESHOLD
                    and since_startup > STARTUP_GRACE_PERIOD
                    and not bot_down_reported):
                # Check if we're near a window boundary (quiet period)
                now_in_window = int(time.time()) % WINDOW_DURATION
                near_boundary = now_in_window < 30 or now_in_window > (WINDOW_DURATION - 30)

                if not near_boundary:
                    print(f"[SUPERVISOR] ⚠ BOT_DOWN: No log output for {elapsed_since_line:.0f}s")
                    bot_down_reported = True
                    if current_ws:
                        current_ws.bot_status = "DOWN"

            # --- Maintain loop cadence ---
            elapsed = time.time() - cycle_start
            time.sleep(max(0.1, 1.0 - elapsed))

        except KeyboardInterrupt:
            print("\n[SUPERVISOR] Shutting down...")
            # Finalize current window on exit
            if current_ws and daily_audits:
                update_daily_summary(daily_date, daily_audits)
            break
        except Exception as e:
            print(f"[SUPERVISOR] ERROR: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(2)


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
