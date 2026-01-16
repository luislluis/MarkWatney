"""
Order Book Imbalance Analyzer
=============================
Analyzes Polymarket order books to detect momentum/trends
based on bid vs ask imbalance.

Strategy: When there's heavy buying pressure (more bids than asks),
ride that momentum and buy that side.
"""

import time
import requests
from collections import deque


class OrderBookAnalyzer:
    """Analyze order book imbalance to detect momentum."""

    def __init__(self, history_size=60, imbalance_threshold=0.3):
        """
        Initialize analyzer.

        Args:
            history_size: Number of readings to keep for trend detection
            imbalance_threshold: Minimum imbalance score to trigger signal (0.3 = 30% more buyers)
        """
        self.history = deque(maxlen=history_size)
        self.threshold = imbalance_threshold

    def calculate_imbalance(self, bids, asks):
        """
        Calculate order book imbalance score.

        Args:
            bids: List of bid orders [{'price': '0.45', 'size': '100'}, ...]
            asks: List of ask orders [{'price': '0.46', 'size': '50'}, ...]

        Returns:
            float: Imbalance score from -1.0 (all sellers) to +1.0 (all buyers)
        """
        # Calculate total value on each side
        bid_depth = sum(float(b.get('size', 0)) * float(b.get('price', 0)) for b in bids)
        ask_depth = sum(float(a.get('size', 0)) * float(a.get('price', 0)) for a in asks)

        total = bid_depth + ask_depth
        if total == 0:
            return 0.0

        imbalance = (bid_depth - ask_depth) / total
        return round(imbalance, 3)

    def analyze(self, up_bids, up_asks, down_bids, down_asks):
        """
        Analyze both UP and DOWN order books.

        Returns:
            dict with imbalance scores and trading signal
        """
        up_imbalance = self.calculate_imbalance(up_bids, up_asks)
        down_imbalance = self.calculate_imbalance(down_bids, down_asks)

        # Store in history
        reading = {
            'time': time.time(),
            'up_imb': up_imbalance,
            'down_imb': down_imbalance
        }
        self.history.append(reading)

        # Get signal
        signal = self._get_signal(up_imbalance, down_imbalance)
        trend = self._get_trend()
        strength = self._get_strength(up_imbalance, down_imbalance)

        return {
            'up_imbalance': up_imbalance,
            'down_imbalance': down_imbalance,
            'signal': signal,
            'trend': trend,
            'strength': strength,
            'history_size': len(self.history)
        }

    def _get_signal(self, up_imb, down_imb):
        """Get trading signal based on current imbalance."""
        if up_imb > self.threshold and up_imb > down_imb:
            return "BUY_UP"
        elif down_imb > self.threshold and down_imb > up_imb:
            return "BUY_DOWN"
        return None

    def _get_strength(self, up_imb, down_imb):
        """Get signal strength: STRONG, MODERATE, or WEAK."""
        max_imb = max(abs(up_imb), abs(down_imb))
        if max_imb > 0.5:
            return "STRONG"
        elif max_imb > 0.3:
            return "MODERATE"
        elif max_imb > 0.15:
            return "WEAK"
        return None

    def _get_trend(self, min_readings=10, consistency=0.6):
        """
        Check if imbalance trend is consistent over time.

        Args:
            min_readings: Minimum readings needed to confirm trend
            consistency: Fraction of readings that must agree (0.6 = 60%)

        Returns:
            str: "TREND_UP", "TREND_DOWN", or None
        """
        if len(self.history) < min_readings:
            return None

        recent = list(self.history)[-min_readings:]

        up_bullish = sum(1 for r in recent if r['up_imb'] > 0.15)
        down_bullish = sum(1 for r in recent if r['down_imb'] > 0.15)

        if up_bullish / min_readings >= consistency:
            return "TREND_UP"
        elif down_bullish / min_readings >= consistency:
            return "TREND_DOWN"

        return None

    def get_summary(self):
        """Get a summary string for logging."""
        if not self.history:
            return "No data yet"

        latest = self.history[-1]
        up = latest['up_imb']
        down = latest['down_imb']

        signal = self._get_signal(up, down)
        strength = self._get_strength(up, down)
        trend = self._get_trend()

        parts = [f"UP:{up:+.2f}", f"DN:{down:+.2f}"]
        if signal:
            parts.append(f"SIG:{signal}")
        if strength:
            parts.append(f"({strength})")
        if trend:
            parts.append(f"[{trend}]")

        return " | ".join(parts)


def fetch_live_orderbook():
    """Fetch live order book from Polymarket for testing."""
    try:
        # Get current market slug
        slug_url = "https://gamma-api.polymarket.com/events?slug=btc-updown-15m"
        # This might not work - need to find active market
        # For now, search active markets
        search_url = "https://gamma-api.polymarket.com/events?active=true&limit=50"
        resp = requests.get(search_url, timeout=5)
        events = resp.json()

        # Find a BTC up/down market
        for event in events:
            title = event.get('title', '').lower()
            if 'btc' in title and ('up' in title or 'above' in title):
                markets = event.get('markets', [{}])
                if markets:
                    clob_ids = markets[0].get('clobTokenIds', '')
                    clob_ids = clob_ids.replace('[', '').replace(']', '').replace('"', '')
                    tokens = [t.strip() for t in clob_ids.split(',')]

                    if len(tokens) >= 2:
                        # Fetch order books
                        up_resp = requests.get(f"https://clob.polymarket.com/book?token_id={tokens[0]}", timeout=3)
                        down_resp = requests.get(f"https://clob.polymarket.com/book?token_id={tokens[1]}", timeout=3)

                        up_book = up_resp.json()
                        down_book = down_resp.json()

                        return {
                            'title': event.get('title'),
                            'up_bids': up_book.get('bids', []),
                            'up_asks': up_book.get('asks', []),
                            'down_bids': down_book.get('bids', []),
                            'down_asks': down_book.get('asks', [])
                        }
        return None
    except Exception as e:
        print(f"Error fetching order book: {e}")
        return None


# Test function
if __name__ == "__main__":
    print("="*60)
    print("ORDER BOOK IMBALANCE ANALYZER TEST")
    print("="*60)

    analyzer = OrderBookAnalyzer()

    # Try to fetch live data
    print("\nFetching live Polymarket data...")
    data = fetch_live_orderbook()

    if data:
        print(f"\nMarket: {data['title'][:60]}...")
        print(f"UP bids: {len(data['up_bids'])} orders")
        print(f"UP asks: {len(data['up_asks'])} orders")
        print(f"DOWN bids: {len(data['down_bids'])} orders")
        print(f"DOWN asks: {len(data['down_asks'])} orders")

        # Analyze
        result = analyzer.analyze(
            data['up_bids'], data['up_asks'],
            data['down_bids'], data['down_asks']
        )

        print(f"\n{'='*40}")
        print("ANALYSIS RESULTS:")
        print(f"{'='*40}")
        print(f"UP Imbalance:   {result['up_imbalance']:+.3f}")
        print(f"DOWN Imbalance: {result['down_imbalance']:+.3f}")
        print(f"Signal:         {result['signal'] or 'None'}")
        print(f"Strength:       {result['strength'] or 'None'}")
        print(f"Trend:          {result['trend'] or 'Need more data'}")

        # Interpret
        print(f"\n{'='*40}")
        print("INTERPRETATION:")
        print(f"{'='*40}")
        if result['up_imbalance'] > 0:
            print(f"UP has {result['up_imbalance']*100:.0f}% more buying pressure")
        else:
            print(f"UP has {-result['up_imbalance']*100:.0f}% more selling pressure")

        if result['down_imbalance'] > 0:
            print(f"DOWN has {result['down_imbalance']*100:.0f}% more buying pressure")
        else:
            print(f"DOWN has {-result['down_imbalance']*100:.0f}% more selling pressure")

        if result['signal']:
            print(f"\nRECOMMENDATION: {result['signal']} ({result['strength']})")
    else:
        print("\nNo active BTC up/down market found. Testing with sample data...")

        # Sample data test
        sample_up_bids = [{'price': '0.45', 'size': '500'}, {'price': '0.44', 'size': '300'}]
        sample_up_asks = [{'price': '0.46', 'size': '100'}, {'price': '0.47', 'size': '50'}]
        sample_down_bids = [{'price': '0.54', 'size': '200'}]
        sample_down_asks = [{'price': '0.55', 'size': '400'}]

        result = analyzer.analyze(sample_up_bids, sample_up_asks, sample_down_bids, sample_down_asks)
        print(f"\nSample Result: {analyzer.get_summary()}")
