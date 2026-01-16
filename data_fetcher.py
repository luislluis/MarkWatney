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

# Track price-to-beat per window (captured at window start)
_window_open_prices = {}  # {window_id: btc_price}


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


# Cache for page data to avoid fetching too often
_page_cache = {'slug': None, 'data': None, 'timestamp': 0}
PAGE_CACHE_TTL = 2  # seconds


def fetch_prices_from_page(slug):
    """
    Fetch openPrice and currentPrice from Polymarket page's __NEXT_DATA__.
    Returns (open_price, current_price) tuple.
    """
    global _page_cache
    import re
    import json

    # Check cache
    now = time.time()
    if _page_cache['slug'] == slug and (now - _page_cache['timestamp']) < PAGE_CACHE_TTL:
        return _page_cache['data']

    open_price = None
    current_price = None

    try:
        url = f"https://polymarket.com/event/{slug}"
        resp = http_session.get(url, timeout=10)
        if resp.status_code == 200:
            # Try to find currentPrice (real-time BTC price from Chainlink streams)
            current_matches = re.findall(r'"currentPrice":([0-9.]+)', resp.text)
            if current_matches:
                # Get the last (most recent) currentPrice
                for price_str in reversed(current_matches):
                    try:
                        price = float(price_str)
                        if 10000 < price < 500000:
                            current_price = price
                            break
                    except ValueError:
                        continue

            # Try to find __NEXT_DATA__ JSON and parse it properly
            next_data_match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', resp.text, re.DOTALL)
            if next_data_match:
                try:
                    next_data = json.loads(next_data_match.group(1))
                    # Navigate to find the market data for this specific slug
                    queries = next_data.get('props', {}).get('pageProps', {}).get('dehydratedState', {}).get('queries', [])
                    for query in queries:
                        data = query.get('state', {}).get('data', {})
                        # Check if this is the market data (could be dict or list)
                        if isinstance(data, dict):
                            if data.get('slug') == slug:
                                if 'openPrice' in data:
                                    open_price = float(data['openPrice'])
                                if 'currentPrice' in data and not current_price:
                                    current_price = float(data['currentPrice'])
                        elif isinstance(data, list):
                            for item in data:
                                if isinstance(item, dict) and item.get('slug') == slug:
                                    if 'openPrice' in item:
                                        open_price = float(item['openPrice'])
                                    if 'currentPrice' in item and not current_price:
                                        current_price = float(item['currentPrice'])
                except json.JSONDecodeError:
                    pass

            # Fallback: Find openPrice near the slug in the raw text
            if not open_price:
                pattern = rf'"slug":"{re.escape(slug)}"[^{{}}]*?"openPrice":([0-9.]+)'
                match = re.search(pattern, resp.text)
                if match:
                    open_price = float(match.group(1))

            # Last resort for openPrice
            if not open_price:
                matches = re.findall(r'"openPrice":([0-9.]+)', resp.text)
                if matches:
                    for price_str in reversed(matches):
                        try:
                            price = float(price_str)
                            if 10000 < price < 500000:
                                open_price = price
                                break
                        except ValueError:
                            continue

    except Exception as e:
        print(f"[PAGE] Error fetching prices: {e}")

    # Update cache
    _page_cache = {'slug': slug, 'data': (open_price, current_price), 'timestamp': now}

    return open_price, current_price


def fetch_open_price_from_page(slug):
    """Fetch just the openPrice (backward compatibility)"""
    open_price, _ = fetch_prices_from_page(slug)
    return open_price


def get_price_to_beat(window_id):
    """
    Get the price-to-beat (opening price) for a window.
    First tries to fetch from Polymarket page, then falls back to cache.
    """
    global _window_open_prices

    # If we already have it cached, return it
    if window_id in _window_open_prices:
        return _window_open_prices[window_id]

    # Try to fetch from Polymarket page
    open_price = fetch_open_price_from_page(window_id)
    if open_price:
        _window_open_prices[window_id] = open_price
        print(f"[PRICE-TO-BEAT] Fetched from page for {window_id}: ${open_price:,.2f}")

        # Clean up old windows (keep only last 5)
        if len(_window_open_prices) > 5:
            oldest_key = min(_window_open_prices.keys())
            del _window_open_prices[oldest_key]

        return open_price

    # Fallback: capture current BTC price (only accurate at window start)
    btc_price = get_btc_price()
    if btc_price:
        _window_open_prices[window_id] = btc_price
        print(f"[PRICE-TO-BEAT] Fallback to current BTC for {window_id}: ${btc_price:,.2f}")
        return btc_price

    return None


def set_price_to_beat(window_id, price):
    """Manually set the price-to-beat for a window (e.g., from external source)"""
    global _window_open_prices
    _window_open_prices[window_id] = price
    print(f"[PRICE-TO-BEAT] Set open price for {window_id}: ${price:,.2f}")


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

    # Get share prices from order books
    up_ask, down_ask = get_share_prices(market)
    time_remaining = get_time_remaining(market)

    # Get openPrice from Polymarket page (it's fixed per window, so caching is fine)
    open_price, _ = fetch_prices_from_page(slug)

    # Use page openPrice if available, otherwise fallback
    price_to_beat = open_price
    if not price_to_beat:
        price_to_beat = get_price_to_beat(slug)

    # For real-time BTC price, use Chainlink (page data is static)
    # Chainlink updates frequently enough for our needs
    btc_price = get_btc_price()

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


def dump_raw_api_response(slug):
    """Debug function to see raw API response"""
    market = get_market_data(slug)
    if market:
        import json
        print("\n=== RAW API RESPONSE ===")
        print(json.dumps(market, indent=2, default=str)[:3000])
        print("... (truncated)")
    return market


if __name__ == '__main__':
    # Test the module
    print("Testing data_fetcher.py...")
    print("-" * 50)

    data = fetch_all_api_data()

    print(f"\nWindow ID: {data['window_id']}")
    print(f"Price-to-Beat: ${data['price_to_beat']:,.2f}" if data['price_to_beat'] else "Price-to-Beat: N/A (will be captured at window start)")
    print(f"UP Ask: {data['up_ask']*100:.1f}c" if data['up_ask'] else "UP Ask: N/A")
    print(f"DOWN Ask: {data['down_ask']*100:.1f}c" if data['down_ask'] else "DOWN Ask: N/A")
    print(f"BTC Price: ${data['btc_price']:,.2f}" if data['btc_price'] else "BTC Price: N/A")
    print(f"Time Remaining: {data['time_remaining']:.0f}s" if data['time_remaining'] else "Time Remaining: N/A")

    # Show comparison if price-to-beat captured
    if data['price_to_beat'] and data['btc_price']:
        diff = data['btc_price'] - data['price_to_beat']
        direction = "UP" if diff >= 0 else "DOWN"
        print(f"\nCurrent vs Open: ${diff:+,.2f} ({direction})")
