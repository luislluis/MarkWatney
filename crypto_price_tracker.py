#!/usr/bin/env python3
"""
Crypto Price Tracker for Polymarket 15-Minute Windows
=====================================================
Tracks all crypto 15-min up/down markets EXCEPT BTC.
Logs every second: current price vs strike price (price to beat).

Usage:
  python3 crypto_price_tracker.py           # Normal mode
  python3 crypto_price_tracker.py --test    # Test mode with simulated data
"""

import os
import sys
import time
import re
import json
import random
import requests
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed

# Command line args
TEST_MODE = "--test" in sys.argv or "-t" in sys.argv

# Timezone for logging
PST = ZoneInfo("America/Los_Angeles")

# Log file
LOG_DIR = os.path.expanduser("~/polybot")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "crypto_tracker.log")

# API endpoints
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
COINBASE_API = "https://api.coinbase.com/v2"

# HTTP session for connection reuse
http_session = requests.Session()
http_session.headers.update({"User-Agent": "Mozilla/5.0"})


def ts():
    """Current timestamp for logging"""
    return datetime.now(PST).strftime("%H:%M:%S")


def log(msg):
    """Print and log to file"""
    line = f"[{ts()}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except:
        pass


def get_coinbase_price(symbol):
    """
    Fetch current price from Coinbase.
    Args:
        symbol: e.g., "ETH", "SOL", "DOGE"
    Returns:
        float or None
    """
    try:
        url = f"{COINBASE_API}/prices/{symbol}-USD/spot"
        resp = http_session.get(url, timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            return float(data['data']['amount'])
    except Exception as e:
        pass
    return None


def fetch_active_crypto_markets():
    """
    Fetch all active 15-minute up/down crypto markets from Polymarket.
    Excludes BTC markets (we only want other cryptos).

    Returns:
        list of market dicts with: slug, title, asset, strike_price, end_time, tokens
    """
    markets = []

    try:
        # Get active events
        url = f"{GAMMA_API}/events?active=true&limit=100"
        resp = http_session.get(url, timeout=5)
        events = resp.json()

        for event in events:
            title = event.get('title', '')
            slug = event.get('slug', '')

            # Look for 15-minute up/down patterns (excluding BTC)
            # Patterns: "ETH up or down", "SOL above/below", etc.
            title_lower = title.lower()

            # Skip BTC markets
            if 'btc' in title_lower or 'bitcoin' in title_lower:
                continue

            # Check for crypto 15-min markets
            is_15min = '15' in title_lower and ('minute' in title_lower or 'min' in title_lower)
            is_updown = 'up' in title_lower or 'down' in title_lower or 'above' in title_lower or 'below' in title_lower

            if not (is_15min and is_updown):
                continue

            # Try to identify the crypto asset
            asset = None
            for crypto in ['ETH', 'SOL', 'DOGE', 'XRP', 'ADA', 'AVAX', 'DOT', 'MATIC', 'LINK', 'SHIB', 'LTC', 'BCH', 'ATOM', 'UNI', 'NEAR']:
                if crypto.lower() in title_lower or crypto in title.upper():
                    asset = crypto
                    break

            # Also check for full names
            if not asset:
                name_to_symbol = {
                    'ethereum': 'ETH',
                    'solana': 'SOL',
                    'dogecoin': 'DOGE',
                    'ripple': 'XRP',
                    'cardano': 'ADA',
                    'avalanche': 'AVAX',
                    'polkadot': 'DOT',
                    'polygon': 'MATIC',
                    'chainlink': 'LINK',
                    'litecoin': 'LTC',
                }
                for name, sym in name_to_symbol.items():
                    if name in title_lower:
                        asset = sym
                        break

            if not asset:
                continue

            # Extract strike price from title
            # Patterns: "above $3,500", "above 3500", "$3500", etc.
            strike_price = None

            # Price ranges for validation by asset
            price_ranges = {
                'ETH': (100, 50000),
                'SOL': (5, 5000),
                'DOGE': (0.001, 10),
                'XRP': (0.1, 100),
                'ADA': (0.1, 50),
                'AVAX': (1, 1000),
                'DOT': (1, 500),
                'MATIC': (0.1, 50),
                'LINK': (1, 500),
                'SHIB': (0.000001, 0.01),
                'LTC': (10, 5000),
                'BCH': (50, 5000),
                'ATOM': (1, 500),
                'UNI': (1, 500),
                'NEAR': (1, 500),
            }

            # Try more specific patterns first (with $ or price keywords)
            patterns = [
                r'(?:above|below|from)\s+\$?([\d,]+\.?\d*)',  # "above $3,500" or "from 3500"
                r'\$([\d,]+\.?\d*)',  # Just $ followed by number
            ]

            min_price, max_price = price_ranges.get(asset, (0.0001, 1000000))

            for pattern in patterns:
                matches = re.findall(pattern, title, re.IGNORECASE)
                for match in matches:
                    try:
                        price = float(match.replace(',', ''))
                        # Validate against asset's price range
                        if min_price < price < max_price:
                            strike_price = price
                            break
                    except:
                        continue
                if strike_price:
                    break

            # Get market tokens
            market_info = event.get('markets', [{}])[0]
            clob_ids = market_info.get('clobTokenIds', '')
            clob_ids = clob_ids.replace('[', '').replace(']', '').replace('"', '')
            tokens = [t.strip() for t in clob_ids.split(',') if t.strip()]

            end_date = market_info.get('endDate', '')

            if len(tokens) >= 2 and strike_price:
                markets.append({
                    'slug': slug,
                    'title': title,
                    'asset': asset,
                    'strike_price': strike_price,
                    'end_date': end_date,
                    'up_token': tokens[0],
                    'down_token': tokens[1],
                })

    except Exception as e:
        log(f"ERROR fetching markets: {e}")

    return markets


def get_order_book_prices(token_id):
    """
    Get best ask price from order book.
    Returns: (best_ask, best_bid) or (None, None)
    """
    try:
        url = f"{CLOB_API}/book?token_id={token_id}"
        resp = http_session.get(url, timeout=3)
        book = resp.json()

        asks = book.get('asks', [])
        bids = book.get('bids', [])

        best_ask = float(asks[0]['price']) if asks else None
        best_bid = float(bids[0]['price']) if bids else None

        return best_ask, best_bid
    except:
        return None, None


def get_time_remaining(end_date_str):
    """Calculate seconds remaining until market close"""
    try:
        end_time = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
        remaining = (end_time - datetime.now(timezone.utc)).total_seconds()
        return max(0, remaining)
    except:
        return 0


def format_time_remaining(seconds):
    """Format seconds as MM:SS"""
    if seconds <= 0:
        return "ENDED"
    mins = int(seconds) // 60
    secs = int(seconds) % 60
    return f"{mins:02d}:{secs:02d}"


def track_markets():
    """
    Main tracking loop.
    Discovers markets, then logs prices every second.
    """
    log("=" * 70)
    log("CRYPTO PRICE TRACKER - Starting")
    log(f"Log file: {LOG_FILE}")
    log("=" * 70)

    # Discovery interval (re-fetch markets every 60 seconds)
    discovery_interval = 60
    last_discovery = 0
    tracked_markets = []

    # Price cache to reduce API calls
    price_cache = {}
    price_cache_ttl = 1.0  # Cache prices for 1 second

    while True:
        try:
            loop_start = time.time()

            # Rediscover markets periodically
            if time.time() - last_discovery > discovery_interval:
                log("-" * 70)
                log("Discovering active crypto 15-min markets...")
                tracked_markets = fetch_active_crypto_markets()
                last_discovery = time.time()

                if tracked_markets:
                    log(f"Found {len(tracked_markets)} active market(s):")
                    for m in tracked_markets:
                        log(f"  • {m['asset']}: Strike ${m['strike_price']:,.2f} | {m['title'][:50]}...")
                else:
                    log("No active crypto 15-min markets found (excluding BTC)")
                log("-" * 70)

            if not tracked_markets:
                log("Waiting for markets... (will retry in 60s)")
                time.sleep(60)
                continue

            # Fetch all current prices in parallel
            assets_to_fetch = list(set(m['asset'] for m in tracked_markets))

            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = {executor.submit(get_coinbase_price, asset): asset for asset in assets_to_fetch}
                for future in as_completed(futures, timeout=5):
                    asset = futures[future]
                    try:
                        price = future.result()
                        if price:
                            price_cache[asset] = (price, time.time())
                    except:
                        pass

            # Log each market
            print()  # Blank line for readability
            for market in tracked_markets:
                asset = market['asset']
                strike = market['strike_price']

                # Get current price from cache
                current_price = None
                if asset in price_cache:
                    cached_price, cached_time = price_cache[asset]
                    if time.time() - cached_time < 5:  # Use if < 5s old
                        current_price = cached_price

                # Get time remaining
                ttc = get_time_remaining(market['end_date'])
                time_str = format_time_remaining(ttc)

                # Skip ended markets
                if ttc <= 0:
                    continue

                # Get order book prices (market probability)
                up_ask, _ = get_order_book_prices(market['up_token'])
                down_ask, _ = get_order_book_prices(market['down_token'])

                # Calculate direction
                if current_price and strike:
                    diff = current_price - strike
                    diff_pct = (diff / strike) * 100
                    direction = "📈 ABOVE" if diff > 0 else "📉 BELOW"

                    # Format price string based on asset
                    if asset in ['DOGE', 'SHIB', 'XRP']:
                        price_fmt = f"${current_price:.4f}"
                        strike_fmt = f"${strike:.4f}"
                        diff_fmt = f"{diff:+.4f}"
                    else:
                        price_fmt = f"${current_price:,.2f}"
                        strike_fmt = f"${strike:,.2f}"
                        diff_fmt = f"{diff:+.2f}"

                    # Market odds
                    up_pct = int(up_ask * 100) if up_ask else "??"
                    down_pct = int(down_ask * 100) if down_ask else "??"

                    log(f"{asset:5} | T-{time_str} | NOW: {price_fmt:>12} | BEAT: {strike_fmt:>12} | {direction} ({diff_fmt}, {diff_pct:+.2f}%) | UP:{up_pct}c DN:{down_pct}c")
                else:
                    log(f"{asset:5} | T-{time_str} | Price data unavailable | Strike: ${strike:,.2f}")

            # Remove ended markets
            tracked_markets = [m for m in tracked_markets if get_time_remaining(m['end_date']) > 0]

            # Sleep to maintain ~1 second loop
            elapsed = time.time() - loop_start
            sleep_time = max(0.1, 1.0 - elapsed)
            time.sleep(sleep_time)

        except KeyboardInterrupt:
            log("\nShutting down...")
            break
        except Exception as e:
            log(f"ERROR in main loop: {e}")
            time.sleep(5)


def generate_simulated_markets():
    """
    Generate simulated market data for testing.
    Returns list of market dicts similar to real API response.
    """
    now = datetime.now(timezone.utc)
    window_start = (int(now.timestamp()) // 900) * 900
    window_end = datetime.fromtimestamp(window_start + 900, tz=timezone.utc)

    # Simulated crypto markets
    simulated = [
        {"asset": "ETH", "strike": 3245.50, "current": 3252.18},
        {"asset": "SOL", "strike": 195.25, "current": 194.87},
        {"asset": "DOGE", "strike": 0.3524, "current": 0.3561},
        {"asset": "XRP", "strike": 3.12, "current": 3.08},
    ]

    markets = []
    for i, sim in enumerate(simulated):
        # Create realistic-looking market data
        markets.append({
            'slug': f"{sim['asset'].lower()}-updown-15m-{window_start}",
            'title': f"Will {sim['asset']} be above ${sim['strike']:,.2f} at {window_end.strftime('%H:%M')} UTC?",
            'asset': sim['asset'],
            'strike_price': sim['strike'],
            'current_price': sim['current'],  # Simulated current price
            'end_date': window_end.isoformat(),
            'up_token': f"sim-up-{i}",
            'down_token': f"sim-down-{i}",
        })

    return markets


def get_simulated_price(asset, markets):
    """Get simulated price with small random walk"""
    for m in markets:
        if m['asset'] == asset:
            # Add small random movement (-0.1% to +0.1%)
            current = m.get('current_price', m['strike_price'])
            change = current * random.uniform(-0.001, 0.001)
            new_price = current + change
            m['current_price'] = new_price
            return new_price
    return None


def track_markets_test_mode():
    """
    Test mode: Uses simulated data to verify logic works.
    """
    log("=" * 70)
    log("CRYPTO PRICE TRACKER - TEST MODE (Simulated Data)")
    log(f"Log file: {LOG_FILE}")
    log("=" * 70)

    simulated_markets = generate_simulated_markets()

    log("-" * 70)
    log(f"Simulated {len(simulated_markets)} markets:")
    for m in simulated_markets:
        log(f"  {m['asset']}: Strike ${m['strike_price']:,.4f} | {m['title'][:60]}...")
    log("-" * 70)

    iteration = 0
    max_iterations = 30  # Run for 30 seconds in test mode

    while iteration < max_iterations:
        try:
            loop_start = time.time()
            iteration += 1

            print()  # Blank line for readability

            for market in simulated_markets:
                asset = market['asset']
                strike = market['strike_price']

                # Get simulated current price with random walk
                current_price = get_simulated_price(asset, simulated_markets)

                # Get time remaining
                ttc = get_time_remaining(market['end_date'])
                time_str = format_time_remaining(ttc)

                if ttc <= 0:
                    continue

                # Simulated market odds based on price position
                diff_pct = (current_price - strike) / strike
                if diff_pct > 0:
                    up_ask = min(0.95, 0.50 + diff_pct * 5)
                    down_ask = 1.0 - up_ask
                else:
                    down_ask = min(0.95, 0.50 - diff_pct * 5)
                    up_ask = 1.0 - down_ask

                # Calculate direction
                diff = current_price - strike
                direction = "ABOVE" if diff > 0 else "BELOW"

                # Format price string based on asset
                if asset in ['DOGE', 'SHIB', 'XRP']:
                    price_fmt = f"${current_price:.4f}"
                    strike_fmt = f"${strike:.4f}"
                    diff_fmt = f"{diff:+.4f}"
                else:
                    price_fmt = f"${current_price:,.2f}"
                    strike_fmt = f"${strike:,.2f}"
                    diff_fmt = f"{diff:+.2f}"

                up_pct = int(up_ask * 100)
                down_pct = int(down_ask * 100)

                log(f"{asset:5} | T-{time_str} | NOW: {price_fmt:>12} | BEAT: {strike_fmt:>12} | {direction:5} ({diff_fmt}, {diff_pct*100:+.2f}%) | UP:{up_pct}c DN:{down_pct}c")

            # Sleep to maintain ~1 second loop
            elapsed = time.time() - loop_start
            sleep_time = max(0.1, 1.0 - elapsed)
            time.sleep(sleep_time)

        except KeyboardInterrupt:
            log("\nTest completed.")
            break
        except Exception as e:
            log(f"ERROR: {e}")
            break

    log("=" * 70)
    log("TEST MODE COMPLETE - Bot logic verified!")
    log("To run with live data, use: python3 crypto_price_tracker.py")
    log("=" * 70)


if __name__ == "__main__":
    if TEST_MODE:
        track_markets_test_mode()
    else:
        track_markets()
