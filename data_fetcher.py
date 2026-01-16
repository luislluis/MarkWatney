#!/usr/bin/env python3
"""
Data Fetcher Module - Fetches Polymarket BTC 15-minute window data from APIs

Data sources:
- Market data: gamma-api.polymarket.com
- Order books: clob.polymarket.com
- BTC price: Chainlink RPC (via chainlink_feed.py)
"""

import time
import re
import requests
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

# Import existing Chainlink feed
try:
    from chainlink_feed import get_btc_price_with_age
    HAS_CHAINLINK = True
except ImportError:
    HAS_CHAINLINK = False

# HTTP session with connection pooling
# Bypass proxy settings for direct API access
http_session = requests.Session()
http_session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
    'Accept': 'application/json',
})
http_session.trust_env = False  # Ignore proxy environment variables


def get_current_window():
    """Calculate current 15-minute window slug and start time"""
    current = int(time.time())
    window_start = (current // 900) * 900
    slug = f"btc-updown-15m-{window_start}"
    return slug, window_start


def get_market_data(slug):
    """Fetch market data from gamma-api"""
    try:
        url = f"https://gamma-api.polymarket.com/events?slug={slug}"
        resp = http_session.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            return data[0] if data else None
    except Exception as e:
        print(f"[API] Error fetching market data: {e}")
    return None


def parse_price_to_beat(market):
    """
    Extract price-to-beat from market title.
    Example title: "Will BTC be above $42,500.00 at 3:15 PM UTC?"
    """
    if not market:
        return None

    title = market.get('title', '')

    # Try to extract price with regex - matches $XX,XXX.XX or $XX,XXX
    patterns = [
        r'\$([0-9,]+(?:\.[0-9]+)?)',  # $42,500.00 or $42,500
        r'above\s+([0-9,]+(?:\.[0-9]+)?)',  # above 42,500
        r'below\s+([0-9,]+(?:\.[0-9]+)?)',  # below 42,500
    ]

    for pattern in patterns:
        match = re.search(pattern, title, re.IGNORECASE)
        if match:
            price_str = match.group(1).replace(',', '')
            try:
                return float(price_str)
            except ValueError:
                continue

    return None


def get_token_ids(market):
    """Extract UP and DOWN token IDs from market data"""
    if not market:
        return None, None

    try:
        clob_ids = market.get('markets', [{}])[0].get('clobTokenIds', '')
        clob_ids = clob_ids.replace('[', '').replace(']', '').replace('"', '')
        tokens = [t.strip() for t in clob_ids.split(',')]
        if len(tokens) >= 2:
            return tokens[0], tokens[1]  # UP token, DOWN token
    except Exception as e:
        print(f"[API] Error parsing token IDs: {e}")

    return None, None


def get_order_book(token_id):
    """Fetch order book for a single token"""
    try:
        url = f"https://clob.polymarket.com/book?token_id={token_id}"
        resp = http_session.get(url, timeout=3)
        if resp.status_code == 200:
            book = resp.json()
            return book.get('asks', []), book.get('bids', [])
    except Exception as e:
        print(f"[API] Error fetching order book: {e}")
    return [], []


def get_share_prices(market):
    """
    Fetch UP and DOWN share prices from order books.
    Returns best ask prices (lowest sell price = what you'd pay to buy).
    """
    up_token, down_token = get_token_ids(market)
    if not up_token or not down_token:
        return None, None

    # Fetch both order books in parallel
    with ThreadPoolExecutor(max_workers=2) as ex:
        up_future = ex.submit(get_order_book, up_token)
        down_future = ex.submit(get_order_book, down_token)

        up_asks, up_bids = up_future.result(timeout=5)
        down_asks, down_bids = down_future.result(timeout=5)

    # Get best ask (lowest price someone is selling at)
    up_ask = None
    down_ask = None

    if up_asks:
        sorted_asks = sorted(up_asks, key=lambda x: float(x['price']))
        up_ask = float(sorted_asks[0]['price'])

    if down_asks:
        sorted_asks = sorted(down_asks, key=lambda x: float(x['price']))
        down_ask = float(sorted_asks[0]['price'])

    return up_ask, down_ask


def get_time_remaining(market):
    """Calculate time remaining until market closes"""
    if not market:
        return None

    try:
        end_str = market.get('markets', [{}])[0].get('endDate', '')
        end_time = datetime.fromisoformat(end_str.replace('Z', '+00:00'))
        remaining = (end_time - datetime.now(timezone.utc)).total_seconds()
        return max(0, remaining)
    except Exception as e:
        print(f"[API] Error calculating time remaining: {e}")
    return None


def get_btc_price():
    """Get current BTC price from Chainlink (or fallback to Coinbase)"""
    # Try Chainlink first
    if HAS_CHAINLINK:
        try:
            price, age = get_btc_price_with_age()
            if price and age < 60:  # Only use if less than 60 seconds old
                return price
        except:
            pass

    # Fallback to Coinbase
    try:
        resp = http_session.get(
            "https://api.coinbase.com/v2/prices/BTC-USD/spot",
            timeout=3
        )
        if resp.status_code == 200:
            data = resp.json()
            return float(data['data']['amount'])
    except:
        pass

    return None


def fetch_all_api_data():
    """
    Fetch all data points from APIs.
    Returns dict with: price_to_beat, up_ask, down_ask, btc_price, time_remaining, window_id
    """
    slug, window_start = get_current_window()
    market = get_market_data(slug)

    # Get all data points
    price_to_beat = parse_price_to_beat(market)
    up_ask, down_ask = get_share_prices(market)
    btc_price = get_btc_price()
    time_remaining = get_time_remaining(market)

    return {
        'window_id': slug,
        'window_start': window_start,
        'price_to_beat': price_to_beat,
        'up_ask': up_ask,
        'down_ask': down_ask,
        'btc_price': btc_price,
        'time_remaining': time_remaining,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'source': 'api'
    }


if __name__ == '__main__':
    # Test the module
    print("Testing data_fetcher.py...")
    print("-" * 50)

    data = fetch_all_api_data()

    print(f"Window ID: {data['window_id']}")
    print(f"Price-to-Beat: ${data['price_to_beat']:,.2f}" if data['price_to_beat'] else "Price-to-Beat: N/A")
    print(f"UP Ask: {data['up_ask']*100:.1f}c" if data['up_ask'] else "UP Ask: N/A")
    print(f"DOWN Ask: {data['down_ask']*100:.1f}c" if data['down_ask'] else "DOWN Ask: N/A")
    print(f"BTC Price: ${data['btc_price']:,.2f}" if data['btc_price'] else "BTC Price: N/A")
    print(f"Time Remaining: {data['time_remaining']:.0f}s" if data['time_remaining'] else "Time Remaining: N/A")
