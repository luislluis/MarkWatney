#!/usr/bin/env python3
"""
PERFORMANCE TRACKER BOT
=======================
Standalone bot that monitors the Polymarket trading bot's performance.
Tracks BTC 15-minute windows, grades ARB and 99c capture trades,
and writes results to a dedicated Google Sheet dashboard.

This bot OBSERVES only - it does NOT place any trades.
"""

# ===========================================
# BOT VERSION
# ===========================================
BOT_VERSION = {
    "version": "v0.1",
    "codename": "Silent Observer",
    "date": "2026-01-20",
    "changes": "Initial skeleton - main loop, logging, graceful shutdown"
}

import os
import sys
import signal
import time
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
import requests

# Timezone for logging (Pacific Time)
PST = ZoneInfo("America/Los_Angeles")

# ===========================================
# LOGGING SETUP
# ===========================================
class TeeLogger:
    """Writes output to both terminal and log file."""
    def __init__(self, filename):
        self.terminal = sys.stdout
        self.log = open(filename, "a", buffering=1)  # Line buffered
    def write(self, message):
        self.terminal.write(message)
        self.log.write(message)
    def flush(self):
        self.terminal.flush()
        self.log.flush()

LOG_FILE = os.path.expanduser("~/polybot/tracker.log")
sys.stdout = TeeLogger(LOG_FILE)
sys.stderr = TeeLogger(LOG_FILE)

# ===========================================
# STARTUP BANNER
# ===========================================
print(f"\n{'='*60}")
print(f"PERFORMANCE TRACKER {BOT_VERSION['codename']} ({BOT_VERSION['version']}) starting...")
print(f"Changes: {BOT_VERSION['changes']}")
print(f"Started: {datetime.now(PST).strftime('%Y-%m-%d %H:%M:%S PST')}")
print(f"Logging to: {LOG_FILE}")
print(f"{'='*60}\n")

# ===========================================
# ENVIRONMENT LOADING
# ===========================================
load_dotenv(os.path.expanduser("~/.env"))
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS")

if WALLET_ADDRESS:
    print(f"Wallet: {WALLET_ADDRESS[:10]}...{WALLET_ADDRESS[-6:]}")
else:
    print("WARNING: WALLET_ADDRESS not found in ~/.env")

# ===========================================
# HTTP SESSION
# ===========================================
http_session = requests.Session()
http_session.headers.update({
    'User-Agent': 'PerformanceTracker/0.1'
})

# ===========================================
# WINDOW TIMING CONSTANTS
# ===========================================
WINDOW_DURATION_SECONDS = 900  # 15 minutes

# ===========================================
# GLOBAL WINDOW STATE
# ===========================================
window_state = None

# ===========================================
# WINDOW DETECTION FUNCTIONS
# ===========================================
def get_current_slug():
    """Calculate current BTC 15-min window slug from Unix timestamp."""
    current = int(time.time())
    window_start = (current // WINDOW_DURATION_SECONDS) * WINDOW_DURATION_SECONDS
    return f"btc-updown-15m-{window_start}", window_start

def get_market_data(slug):
    """Fetch market metadata from Polymarket gamma-api."""
    try:
        url = f"https://gamma-api.polymarket.com/events?slug={slug}"
        resp = http_session.get(url, timeout=3)
        data = resp.json()
        return data[0] if data else None
    except Exception as e:
        print(f"[WARN] Market data fetch failed: {e}")
        return None

def get_time_remaining(market):
    """Calculate time remaining from market endDate."""
    try:
        end_str = market.get('markets', [{}])[0].get('endDate', '')
        end_time = datetime.fromisoformat(end_str.replace('Z', '+00:00'))
        remaining = (end_time - datetime.now(timezone.utc)).total_seconds()
        if remaining < 0:
            return "ENDED", -1
        return f"{int(remaining)//60:02d}:{int(remaining)%60:02d}", int(remaining)
    except Exception as e:
        print(f"[WARN] Time remaining parse failed: {e}")
        return "??:??", 0

# ===========================================
# WINDOW STATE MANAGEMENT
# ===========================================
def reset_window_state(slug):
    """Initialize fresh window state for tracking."""
    return {
        'slug': slug,
        'started_at': datetime.now(PST),
        # Position tracking (populated in Phase 2)
        'arb_entry': None,       # {'up_shares': X, 'down_shares': X, 'cost': X}
        'arb_result': None,      # 'PAIRED', 'BAIL', 'LOPSIDED'
        'arb_pnl': 0.0,
        'capture_entry': None,   # {'side': 'UP'/'DOWN', 'shares': X, 'cost': X}
        'capture_result': None,  # 'WIN', 'LOSS'
        'capture_pnl': 0.0,
        # Metadata
        'window_end_price': None,
        'outcome': None,         # 'UP' or 'DOWN'
        'graded': False,
    }

def grade_window(state):
    """Grade a completed window and output summary row.

    This is a skeleton that outputs placeholder data.
    Phase 2 will populate actual position and P/L data.
    """
    if not state:
        return

    slug = state.get('slug', 'unknown')
    started = state.get('started_at', datetime.now(PST))

    # Extract window time from slug (e.g., btc-updown-15m-1737417600)
    try:
        window_ts = int(slug.split('-')[-1])
        window_time = datetime.fromtimestamp(window_ts, tz=PST).strftime('%H:%M')
    except:
        window_time = started.strftime('%H:%M')

    # Placeholder data (Phase 2 will populate)
    arb_entry = state.get('arb_entry')
    arb_result = state.get('arb_result', '-')
    arb_pnl = state.get('arb_pnl', 0)

    capture_entry = state.get('capture_entry')
    capture_result = state.get('capture_result', '-')
    capture_pnl = state.get('capture_pnl', 0)

    total_pnl = arb_pnl + capture_pnl

    # Format row
    print(f"\n{'='*60}")
    print(f"WINDOW GRADED: {slug}")
    print(f"{'='*60}")
    print(f"  Time:        {window_time}")
    print(f"  ARB Entry:   {'Yes' if arb_entry else '-'}")
    print(f"  ARB Result:  {arb_result}")
    print(f"  ARB P/L:     ${arb_pnl:+.2f}")
    print(f"  99c Entry:   {'Yes' if capture_entry else '-'}")
    print(f"  99c Result:  {capture_result}")
    print(f"  99c P/L:     ${capture_pnl:+.2f}")
    print(f"  -----------")
    print(f"  TOTAL P/L:   ${total_pnl:+.2f}")
    print(f"{'='*60}\n")

# ===========================================
# SIGNAL HANDLER
# ===========================================
def signal_handler(sig, frame):
    print("\n\nCtrl+C pressed. Performance Tracker exiting...")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

# ===========================================
# MAIN LOOP
# ===========================================
def main():
    global window_state
    print("Performance Tracker starting main loop...")
    print()

    cached_market = None
    last_slug = None

    while True:
        cycle_start = time.time()

        try:
            # Get current window
            slug, window_start = get_current_slug()

            # Detect window transition
            if slug != last_slug:
                # Grade completed window (if exists)
                if last_slug is not None and window_state is not None:
                    grade_window(window_state)

                # Start fresh window
                window_state = reset_window_state(slug)
                cached_market = None
                last_slug = slug

                print(f"\n{'='*50}")
                print(f"NEW WINDOW: {slug}")
                print(f"{'='*50}")

            # Fetch market data (cache per window)
            if not cached_market:
                cached_market = get_market_data(slug)
                if not cached_market:
                    print(f"[{datetime.now(PST).strftime('%H:%M:%S')}] Waiting for market data...")
                    time.sleep(2)
                    continue

            # Calculate time remaining
            time_str, remaining_secs = get_time_remaining(cached_market)

            if remaining_secs < 0:
                print(f"[{datetime.now(PST).strftime('%H:%M:%S')}] Window ended, waiting for next...")
                time.sleep(2)
                continue

            # Display status line
            # TODO: Position detection (Phase 2)
            print(f"[{datetime.now(PST).strftime('%H:%M:%S')}] T-{remaining_secs:3d}s | {slug}")

            # Maintain 1-second loop
            elapsed = time.time() - cycle_start
            time.sleep(max(0, 1 - elapsed))

        except Exception as e:
            print(f"ERROR: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(1)

if __name__ == "__main__":
    main()
