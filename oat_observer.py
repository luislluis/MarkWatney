#!/usr/bin/env python3
"""
Strategy Extractor — Observer
==============================
Pure data collection bot. Watches Uncommon-Oat's trading activity on
BTC 15-minute Polymarket prediction markets and stores every fill,
order book snapshot, and market condition for strategy analysis.

NO trading logic. NO copy-trading. Just observation.
"""

# ===========================================
# BOT VERSION
# ===========================================
BOT_VERSION = {
    "version": "v0.2",
    "codename": "Glass Wall",
    "date": "2026-03-02",
    "changes": "Add Supabase push layer for live web dashboard"
}

import os
import sys
import signal
import time
import json
import logging
import threading
import requests
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import RotatingFileHandler

import oat_db as db
import oat_analyzer as analyzer
import oat_supabase as supa

# ===========================================
# CONFIGURATION
# ===========================================

# Target account
TARGET_WALLET = "0xd0d6053c3c37e727402d84c14069780d360993aa"
TARGET_NAME = "Uncommon-Oat"

# Polling intervals (seconds)
ACTIVITY_POLL_INTERVAL = 3.0
OB_SNAPSHOT_INTERVAL = 2.0
STATUS_DISPLAY_INTERVAL = 1.0

# Analysis runs after every N window transitions
ANALYSIS_INTERVAL_WINDOWS = 1

# Timezone
EST = ZoneInfo("America/New_York")

# ===========================================
# LOGGING
# ===========================================

LOG_DIR = os.path.expanduser("~/polybot")
LOG_FILE = os.path.join(LOG_DIR, "oat_observer.log")

os.makedirs(LOG_DIR, exist_ok=True)

logger = logging.getLogger("oat_observer")
logger.setLevel(logging.INFO)

file_handler = RotatingFileHandler(LOG_FILE, maxBytes=10*1024*1024, backupCount=5)
file_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
logger.addHandler(file_handler)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(logging.Formatter("%(message)s"))
logger.addHandler(console_handler)


def log(msg):
    logger.info(msg)


def ts():
    return datetime.now(EST).strftime("%H:%M:%S")

# ===========================================
# TELEGRAM
# ===========================================

TELEGRAM_CONFIG_FILE = os.path.expanduser("~/.telegram-bot.json")
telegram_config = None


def init_telegram():
    global telegram_config
    try:
        if os.path.exists(TELEGRAM_CONFIG_FILE):
            with open(TELEGRAM_CONFIG_FILE, "r") as f:
                telegram_config = json.load(f)
            log(f"[Telegram] Bot connected")
            return True
    except Exception as e:
        log(f"[Telegram] Error: {e}")
    return False


def send_telegram(message):
    if not telegram_config:
        return
    try:
        url = f"https://api.telegram.org/bot{telegram_config['token']}/sendMessage"
        requests.post(url, data={
            "chat_id": telegram_config["chat_id"],
            "text": message,
            "parse_mode": "HTML"
        }, timeout=5)
    except Exception:
        pass

# ===========================================
# HTTP SESSION
# ===========================================

http_session = requests.Session()
http_session.headers.update({"User-Agent": "OatObserver/0.1"})

# ===========================================
# WINDOW DETECTION
# ===========================================

def get_current_slug():
    """Get the current BTC 15-minute window slug and start timestamp."""
    current = int(time.time())
    window_start = (current // 900) * 900
    return f"btc-updown-15m-{window_start}", window_start


def get_time_remaining_secs(window_start):
    """Seconds remaining in the current window."""
    window_end = window_start + 900
    return max(0, window_end - int(time.time()))

# ===========================================
# MARKET DATA
# ===========================================

def get_market_data(slug):
    """Fetch market data from Gamma API."""
    try:
        url = f"https://gamma-api.polymarket.com/events?slug={slug}"
        resp = http_session.get(url, timeout=5)
        data = resp.json()
        return data[0] if data else None
    except Exception as e:
        log(f"[{ts()}] MARKET_DATA_ERROR: {e}")
        return None


def extract_tokens(market):
    """Extract UP and DOWN token IDs from market data."""
    try:
        clob_ids = market.get("markets", [{}])[0].get("clobTokenIds", "")
        clob_ids = clob_ids.replace("[", "").replace("]", "").replace('"', "")
        tokens = [t.strip() for t in clob_ids.split(",")]
        if len(tokens) >= 2:
            return {"up": tokens[0], "down": tokens[1]}
    except Exception as e:
        log(f"[{ts()}] TOKEN_EXTRACT_ERROR: {e}")
    return None

# ===========================================
# ORDER BOOK
# ===========================================

def fetch_single_book(token_id):
    """Fetch order book for a single token."""
    try:
        url = f"https://clob.polymarket.com/book?token_id={token_id}"
        resp = http_session.get(url, timeout=3)
        book = resp.json()
        return book.get("asks", []), book.get("bids", [])
    except Exception:
        return [], []


def snapshot_order_book(tokens):
    """Fetch order books for both UP and DOWN tokens in parallel."""
    if not tokens:
        return None
    try:
        with ThreadPoolExecutor(max_workers=2) as ex:
            up_future = ex.submit(fetch_single_book, tokens["up"])
            down_future = ex.submit(fetch_single_book, tokens["down"])
            up_asks, up_bids = up_future.result(timeout=5)
            down_asks, down_bids = down_future.result(timeout=5)

        def best_price(levels, default=0.0):
            return float(levels[0]["price"]) if levels else default

        def depth(levels, top_n=5):
            return sum(float(lvl.get("size", 0)) for lvl in levels[:top_n])

        up_asks_sorted = sorted(up_asks, key=lambda x: float(x["price"]))
        up_bids_sorted = sorted(up_bids, key=lambda x: float(x["price"]), reverse=True)
        down_asks_sorted = sorted(down_asks, key=lambda x: float(x["price"]))
        down_bids_sorted = sorted(down_bids, key=lambda x: float(x["price"]), reverse=True)

        return {
            "timestamp": time.time(),
            "up_best_bid": best_price(up_bids_sorted),
            "up_best_ask": best_price(up_asks_sorted, 1.0),
            "up_bid_depth": depth(up_bids_sorted),
            "up_ask_depth": depth(up_asks_sorted),
            "down_best_bid": best_price(down_bids_sorted),
            "down_best_ask": best_price(down_asks_sorted, 1.0),
            "down_bid_depth": depth(down_bids_sorted),
            "down_ask_depth": depth(down_asks_sorted),
            "up_asks": up_asks_sorted,
            "up_bids": up_bids_sorted,
            "down_asks": down_asks_sorted,
            "down_bids": down_bids_sorted,
        }
    except Exception as e:
        log(f"[{ts()}] OB_SNAPSHOT_ERROR: {e}")
        return None

# ===========================================
# ACTIVITY POLLING
# ===========================================

def poll_target_activity(seen_tx_hashes):
    """Poll target's recent activity, return only NEW fills."""
    try:
        url = f"https://data-api.polymarket.com/activity?user={TARGET_WALLET}&limit=50"
        resp = http_session.get(url, timeout=5)
        activities = resp.json()
        new_fills = []
        for a in activities:
            tx = a.get("transactionHash", "")
            if tx and tx not in seen_tx_hashes:
                seen_tx_hashes.add(tx)
                if a.get("type") == "TRADE":
                    new_fills.append(a)
        return new_fills
    except Exception as e:
        log(f"[{ts()}] ACTIVITY_POLL_ERROR: {e}")
        return []

# ===========================================
# FILL CLASSIFICATION
# ===========================================

def classify_fill(fill, ob_snapshot):
    """Determine if a fill was maker (limit) or taker (market)."""
    if not ob_snapshot:
        return "UNKNOWN"

    price = float(fill.get("price", 0))
    side = fill.get("side", "").upper()
    outcome = fill.get("outcome", "").lower()

    if "up" in outcome:
        asks = ob_snapshot.get("up_asks", [])
        bids = ob_snapshot.get("up_bids", [])
    else:
        asks = ob_snapshot.get("down_asks", [])
        bids = ob_snapshot.get("down_bids", [])

    if side == "BUY":
        if asks:
            best_ask = float(asks[0]["price"])
            if price < best_ask - 0.005:
                return "MAKER"
            elif price >= best_ask - 0.005:
                return "TAKER"
    elif side == "SELL":
        if bids:
            best_bid = float(bids[0]["price"])
            if price > best_bid + 0.005:
                return "MAKER"
            elif price <= best_bid + 0.005:
                return "TAKER"

    return "UNKNOWN"

# ===========================================
# WINDOW STATE
# ===========================================

def new_window_state(slug, window_start):
    return {
        "slug": slug,
        "window_start": window_start,
        # Target tracking
        "target_traded": False,
        "target_buys": [],
        "target_sells": [],
        "target_up_shares": 0.0,
        "target_down_shares": 0.0,
        "target_up_cost": 0.0,
        "target_down_cost": 0.0,
        "target_maker_count": 0,
        "target_taker_count": 0,
        "target_first_buy_ts": None,
        "target_first_buy_side": None,
        "target_second_side_ts": None,
        "fill_sequence": 0,
        # Market context at first buy
        "up_ask_at_entry": None,
        "down_ask_at_entry": None,
        "ob_imbalance_at_entry": None,
        "time_remaining_at_entry": None,
        # Latest OB snapshot
        "latest_ob": None,
        "latest_ob_id": None,
        # Dedup
        "seen_tx_hashes": set(),
        # Timing
        "last_activity_poll": 0,
        "last_ob_snapshot": 0,
        "last_status_display": 0,
    }

# ===========================================
# FILL PROCESSING
# ===========================================

def process_fill(fill, window_state, ob_snapshot):
    """Process a new target fill — classify, store, update window state."""
    slug = window_state["slug"]
    tx_hash = fill.get("transactionHash", "")
    timestamp = int(fill.get("timestamp", time.time()))
    side = fill.get("side", "").upper()
    outcome = fill.get("outcome", "")
    price = float(fill.get("price", 0))
    size = float(fill.get("size", 0))
    usdc_size = float(fill.get("usdcSize", 0))

    fill_type = classify_fill(fill, ob_snapshot)
    ob_id = window_state.get("latest_ob_id")

    # Track sequence within window
    window_state["fill_sequence"] += 1
    seq = window_state["fill_sequence"]

    was_new = db.insert_fill(slug, tx_hash, timestamp, side, outcome,
                              price, size, usdc_size, fill_type, ob_id, seq)
    if not was_new:
        return  # duplicate

    # Update window state
    window_state["target_traded"] = True

    is_up = "up" in outcome.lower()

    if side == "BUY":
        window_state["target_buys"].append(fill)
        if is_up:
            window_state["target_up_shares"] += size
            window_state["target_up_cost"] += usdc_size
        else:
            window_state["target_down_shares"] += size
            window_state["target_down_cost"] += usdc_size

        # Track first buy timing, side, and market context
        if window_state["target_first_buy_ts"] is None:
            window_state["target_first_buy_ts"] = timestamp
            window_state["target_first_buy_side"] = "UP" if is_up else "DOWN"
            remaining = get_time_remaining_secs(window_state["window_start"])
            window_state["time_remaining_at_entry"] = remaining
            if ob_snapshot:
                window_state["up_ask_at_entry"] = ob_snapshot.get("up_best_ask")
                window_state["down_ask_at_entry"] = ob_snapshot.get("down_best_ask")
                up_d = ob_snapshot.get("up_bid_depth", 0)
                down_d = ob_snapshot.get("down_bid_depth", 0)
                total = up_d + down_d
                window_state["ob_imbalance_at_entry"] = (up_d - down_d) / total if total > 0 else 0
        else:
            # Track when second side appears (for leg gap calculation)
            first_side = window_state["target_first_buy_side"]
            current_side = "UP" if is_up else "DOWN"
            if current_side != first_side and window_state["target_second_side_ts"] is None:
                window_state["target_second_side_ts"] = timestamp
    else:
        window_state["target_sells"].append(fill)

    if fill_type == "MAKER":
        window_state["target_maker_count"] += 1
    elif fill_type == "TAKER":
        window_state["target_taker_count"] += 1

    direction = "UP" if is_up else "DN"
    log(f"[{ts()}] TARGET_{side} | {direction}@{price:.2f}c x{size:.1f} | {fill_type} | tx:{tx_hash[:10]}...")

    # Buffer fill for Supabase push
    supa.buffer_fill({
        "slug": slug, "tx_hash": tx_hash, "timestamp": timestamp,
        "side": side, "outcome": outcome, "price": price,
        "size": size, "usdc_size": usdc_size, "fill_type": fill_type,
        "sequence_in_window": seq,
    })

# ===========================================
# WINDOW SUMMARY
# ===========================================

def summarize_window(window_state, outcome=None):
    """Generate and store window summary."""
    slug = window_state["slug"]
    ws = window_state["window_start"]

    total_buys = len(window_state["target_buys"])
    total_sells = len(window_state["target_sells"])

    up_shares = window_state["target_up_shares"]
    down_shares = window_state["target_down_shares"]
    up_cost = window_state["target_up_cost"]
    down_cost = window_state["target_down_cost"]

    # Determine sides
    if up_shares > 0 and down_shares > 0:
        target_sides = "BOTH"
    elif up_shares > 0:
        target_sides = "UP"
    elif down_shares > 0:
        target_sides = "DOWN"
    else:
        target_sides = "NONE"

    up_avg = up_cost / up_shares if up_shares > 0 else 0
    down_avg = down_cost / down_shares if down_shares > 0 else 0

    # First buy offset
    first_buy_offset = None
    if window_state["target_first_buy_ts"]:
        first_buy_offset = window_state["target_first_buy_ts"] - ws

    # Leg gap
    leg_gap = None
    if window_state["target_first_buy_ts"] and window_state["target_second_side_ts"]:
        leg_gap = window_state["target_second_side_ts"] - window_state["target_first_buy_ts"]

    # Combined cost (only when both sides)
    combined_cost = None
    if up_shares > 0 and down_shares > 0:
        combined_cost = up_avg + down_avg

    # Resolve outcome
    if outcome is None:
        outcome = resolve_window_outcome(slug)

    db.upsert_observation(
        slug=slug,
        window_start=ws,
        target_traded=window_state["target_traded"],
        target_sides=target_sides,
        target_total_buys=total_buys,
        target_total_sells=total_sells,
        target_up_shares=up_shares,
        target_down_shares=down_shares,
        target_up_avg_price=up_avg,
        target_down_avg_price=down_avg,
        target_up_total_usdc=up_cost,
        target_down_total_usdc=down_cost,
        target_first_buy_offset_secs=first_buy_offset,
        target_first_buy_side=window_state.get("target_first_buy_side"),
        target_leg_gap_secs=leg_gap,
        target_combined_cost=combined_cost,
        target_maker_count=window_state["target_maker_count"],
        target_taker_count=window_state["target_taker_count"],
        up_ask_at_entry=window_state.get("up_ask_at_entry"),
        down_ask_at_entry=window_state.get("down_ask_at_entry"),
        ob_imbalance_at_entry=window_state.get("ob_imbalance_at_entry"),
        time_remaining_at_entry=window_state.get("time_remaining_at_entry"),
        outcome=outcome,
        resolved_at=int(time.time()) if outcome else None,
    )

    cc_str = f"{combined_cost:.2f}" if combined_cost is not None else "N/A"
    log(f"[{ts()}] WINDOW_END | {slug} | OAT: {total_buys}B/{total_sells}S "
        f"| sides:{target_sides} | UP:{up_shares:.0f}@{up_avg:.2f} DN:{down_shares:.0f}@{down_avg:.2f} "
        f"| combined:{cc_str} | gap:{leg_gap or 'N/A'}s "
        f"| maker:{window_state['target_maker_count']} taker:{window_state['target_taker_count']} "
        f"| outcome:{outcome or 'PENDING'}")

    return outcome

# ===========================================
# OUTCOME RESOLUTION
# ===========================================

def resolve_window_outcome(slug):
    """Check if a window has resolved and return the winner."""
    try:
        market = get_market_data(slug)
        if not market:
            return None

        markets = market.get("markets", [])
        if not markets:
            return None

        m = markets[0]
        if m.get("resolved") or m.get("winner"):
            winner = m.get("winner", "")
            tokens = extract_tokens(market)
            if tokens:
                if winner == tokens["up"] or "up" in str(m.get("outcome", "")).lower():
                    return "UP"
                elif winner == tokens["down"] or "down" in str(m.get("outcome", "")).lower():
                    return "DOWN"

            outcomes = m.get("outcomes", [])
            outcome_prices = m.get("outcomePrices", "")
            if outcomes and outcome_prices:
                try:
                    prices = json.loads(outcome_prices) if isinstance(outcome_prices, str) else outcome_prices
                    for i, price in enumerate(prices):
                        if float(price) >= 0.99:
                            return outcomes[i].upper() if i < len(outcomes) else None
                except (json.JSONDecodeError, ValueError):
                    pass

        return None
    except Exception as e:
        log(f"[{ts()}] OUTCOME_RESOLVE_ERROR: {e}")
        return None


def try_resolve_pending_windows():
    """Background task: resolve outcomes for unresolved windows."""
    pending = db.get_recent_observations(limit=50)
    for obs in pending:
        if obs["outcome"] is None and obs["window_start"] < int(time.time()) - 900:
            outcome = resolve_window_outcome(obs["slug"])
            if outcome:
                db.upsert_observation(
                    slug=obs["slug"],
                    window_start=obs["window_start"],
                    outcome=outcome,
                    resolved_at=int(time.time()),
                )
                log(f"[{ts()}] RESOLVED | {obs['slug']} → {outcome}")

# ===========================================
# STATUS DISPLAY
# ===========================================

def format_status_line(window_state, stats, remaining_secs):
    """Format the per-second status line."""
    time_str = ts()

    # Target activity
    buys = len(window_state.get("target_buys", []))
    sells = len(window_state.get("target_sells", []))
    up_shares = window_state.get("target_up_shares", 0)
    down_shares = window_state.get("target_down_shares", 0)

    if buys > 0:
        if down_shares > up_shares:
            oat_str = f"{buys}B (DN@{window_state['target_down_cost']/max(down_shares,1):.0f}c)"
        elif up_shares > down_shares:
            oat_str = f"{buys}B (UP@{window_state['target_up_cost']/max(up_shares,1):.0f}c)"
        else:
            oat_str = f"{buys}B (BOTH)"
        if sells > 0:
            oat_str += f" {sells}S"
    else:
        oat_str = "quiet"

    # Order book
    ob = window_state.get("latest_ob")
    if ob:
        up_ask = ob.get("up_best_ask", 0)
        down_ask = ob.get("down_best_ask", 0)
        combined = up_ask + down_ask
        ob_str = f"UP:{up_ask:.0f}c DN:{down_ask:.0f}c ={combined:.2f}"
    else:
        ob_str = "no OB"

    # Stats
    obs_count = stats.get("total_observations", 0)
    readiness = stats.get("overall_readiness", 0)
    fills = stats.get("total_fills", 0)

    return (f"[{time_str}] OBSERVE | T-{remaining_secs:3.0f}s | "
            f"OAT: {oat_str} | {ob_str} | "
            f"obs:{obs_count} fills:{fills} ready:{readiness:.0%}")

# ===========================================
# DAILY SUMMARY
# ===========================================

_last_daily_summary_date = None


def maybe_send_daily_summary():
    """Send a daily Telegram summary at midnight EST."""
    global _last_daily_summary_date
    now_est = datetime.now(EST)
    today = now_est.date()

    if _last_daily_summary_date == today:
        return
    if now_est.hour != 0 or now_est.minute > 5:
        return

    _last_daily_summary_date = today
    stats = db.compute_basic_stats()

    yesterday = today - timedelta(days=1)
    recent = db.get_recent_observations(limit=100)
    yesterday_start = int(datetime.combine(yesterday, datetime.min.time()).replace(tzinfo=EST).timestamp())
    yesterday_end = yesterday_start + 86400
    day_obs = [o for o in recent if yesterday_start <= o["window_start"] < yesterday_end]
    day_traded = sum(1 for o in day_obs if o["target_traded"])

    msg = (
        f"<b>OAT OBSERVER DAILY</b>\n"
        f"Date: {yesterday}\n\n"
        f"<b>Yesterday:</b>\n"
        f"Windows observed: {len(day_obs)}\n"
        f"Windows Oat traded: {day_traded}\n\n"
        f"<b>All-Time:</b>\n"
        f"Total observations: {stats['total_observations']}\n"
        f"Total fills recorded: {stats['total_fills']}\n"
        f"OB snapshots: {stats['total_snapshots']}\n"
        f"Strategy readiness: {stats['overall_readiness']:.0%}\n"
    )

    send_telegram(msg)
    log(f"[{ts()}] DAILY_SUMMARY sent")

# ===========================================
# SIGNAL HANDLERS
# ===========================================

_shutdown = False


def handle_signal(signum, frame):
    global _shutdown
    log(f"[{ts()}] Received signal {signum}, shutting down gracefully...")
    _shutdown = True

# ===========================================
# MAIN LOOP
# ===========================================

def main():
    global _shutdown

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    db.init_db()
    supa.init()
    init_telegram()

    log(f"[{ts()}] =============================================")
    log(f"[{ts()}] OAT OBSERVER {BOT_VERSION['version']} \"{BOT_VERSION['codename']}\"")
    log(f"[{ts()}] Target: {TARGET_NAME} ({TARGET_WALLET[:10]}...)")
    log(f"[{ts()}] Pure observation mode — no trading")
    log(f"[{ts()}] =============================================")

    send_telegram(
        f"<b>OAT OBSERVER STARTED</b>\n"
        f"Version: {BOT_VERSION['version']} \"{BOT_VERSION['codename']}\"\n"
        f"Target: {TARGET_NAME}\n"
        f"Mode: Pure observation"
    )

    last_slug = None
    window_state = None
    tokens = None
    stats = db.compute_basic_stats()
    windows_since_cleanup = 0

    # Background resolution thread
    def resolution_loop():
        while not _shutdown:
            try:
                try_resolve_pending_windows()
            except Exception as e:
                log(f"[{ts()}] RESOLUTION_ERROR: {e}")
            time.sleep(30)

    resolver_thread = threading.Thread(target=resolution_loop, daemon=True)
    resolver_thread.start()

    while not _shutdown:
        loop_start = time.time()

        try:
            slug, window_start = get_current_slug()
            remaining = get_time_remaining_secs(window_start)

            # --- NEW WINDOW ---
            if slug != last_slug:
                if window_state:
                    summarize_window(window_state)
                    # Push observation to Supabase
                    obs = db.get_observation(window_state["slug"])
                    if obs:
                        supa.push_observation(obs)
                    supa.flush_fills()
                    # Run strategy analysis after each window
                    try:
                        result = analyzer.run_analysis()
                        if result:
                            log(f"[{ts()}] ANALYSIS | readiness:{result['overall_readiness']:.0%} "
                                f"| n={result['sample_size']}")
                            supa.push_analysis(result)
                    except Exception as e:
                        log(f"[{ts()}] ANALYSIS_ERROR: {e}")
                    stats = db.compute_basic_stats()
                    windows_since_cleanup += 1

                window_state = new_window_state(slug, window_start)
                last_slug = slug

                market = get_market_data(slug)
                if market:
                    tokens = extract_tokens(market)
                    log(f"[{ts()}] NEW_WINDOW | {slug} | tokens: {'OK' if tokens else 'MISSING'}")
                else:
                    tokens = None
                    log(f"[{ts()}] NEW_WINDOW | {slug} | NO MARKET DATA")

                # Weekly OB cleanup
                if windows_since_cleanup >= 672:  # ~7 days of windows
                    deleted = db.cleanup_old_ob_snapshots(days=7)
                    if deleted:
                        log(f"[{ts()}] CLEANUP | deleted {deleted} old OB snapshots")
                    windows_since_cleanup = 0

                maybe_send_daily_summary()

            if not window_state:
                time.sleep(1)
                continue

            now = time.time()

            # --- POLL TARGET ACTIVITY (every 3s) ---
            if now - window_state["last_activity_poll"] >= ACTIVITY_POLL_INTERVAL:
                window_state["last_activity_poll"] = now
                new_fills = poll_target_activity(window_state["seen_tx_hashes"])

                for fill in new_fills:
                    fill_slug = fill.get("slug", "")
                    if "btc-updown-15m" in fill_slug.lower() or fill_slug == slug:
                        process_fill(fill, window_state, window_state.get("latest_ob"))

            # --- SNAPSHOT ORDER BOOK (every 2s) ---
            if now - window_state["last_ob_snapshot"] >= OB_SNAPSHOT_INTERVAL:
                window_state["last_ob_snapshot"] = now
                ob = snapshot_order_book(tokens)
                if ob:
                    window_state["latest_ob"] = ob
                    ob_id = db.insert_ob_snapshot(
                        slug, ob["timestamp"],
                        ob["up_best_bid"], ob["up_best_ask"],
                        ob["up_bid_depth"], ob["up_ask_depth"],
                        ob["down_best_bid"], ob["down_best_ask"],
                        ob["down_bid_depth"], ob["down_ask_depth"],
                    )
                    window_state["latest_ob_id"] = ob_id

            # --- FLUSH SUPABASE FILLS (every 30s) ---
            supa.maybe_flush_fills()

            # --- STATUS LINE (every 1s to console, every 15s to log) ---
            if now - window_state["last_status_display"] >= STATUS_DISPLAY_INTERVAL:
                window_state["last_status_display"] = now
                status = format_status_line(window_state, stats, remaining)
                print(f"\r{status}", end="", flush=True)
                if int(now) % 15 == 0:
                    log(status)

        except Exception as e:
            log(f"\n[{ts()}] MAIN_LOOP_ERROR: {e}")
            import traceback
            traceback.print_exc()

        elapsed = time.time() - loop_start
        sleep_time = max(0.1, 1.0 - elapsed)
        time.sleep(sleep_time)

    # Graceful shutdown
    log(f"\n[{ts()}] Shutdown complete.")
    if window_state:
        summarize_window(window_state)
    send_telegram(f"<b>OAT OBSERVER STOPPED</b>\nVersion: {BOT_VERSION['version']}")


if __name__ == "__main__":
    main()
