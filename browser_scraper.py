#!/usr/bin/env python3
"""
Browser Scraper Module - Scrapes Polymarket data using Playwright

Extracts displayed values from the Polymarket website for verification
against API data.
"""

import re
import time
import asyncio
from datetime import datetime, timezone


class BrowserScraper:
    """Playwright-based scraper for Polymarket data"""

    def __init__(self):
        self.browser = None
        self.context = None
        self.page = None
        self.playwright = None
        self._initialized = False

    async def initialize(self):
        """Initialize Playwright browser"""
        if self._initialized:
            return

        from playwright.async_api import async_playwright

        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox']
        )
        self.context = await self.browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        )
        self.page = await self.context.new_page()
        self._initialized = True

    async def close(self):
        """Clean up browser resources"""
        if self.page:
            await self.page.close()
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        self._initialized = False

    async def navigate_to_market(self, slug):
        """Navigate to the Polymarket event page"""
        url = f"https://polymarket.com/event/{slug}"
        try:
            await self.page.goto(url, wait_until='networkidle', timeout=15000)
            # Wait a bit for dynamic content
            await asyncio.sleep(1)
            return True
        except Exception as e:
            print(f"[BROWSER] Error navigating to {url}: {e}")
            return False

    async def get_page_text(self):
        """Get all visible text from page"""
        try:
            return await self.page.inner_text('body')
        except:
            return ""

    async def extract_price_to_beat(self):
        """
        Extract the price-to-beat from the page.
        Looks for patterns like "$42,500" or "above $42,500"
        """
        try:
            # Get page content
            text = await self.get_page_text()

            # Look for price patterns
            patterns = [
                r'above\s+\$([0-9,]+(?:\.[0-9]+)?)',
                r'below\s+\$([0-9,]+(?:\.[0-9]+)?)',
                r'Will BTC.*?\$([0-9,]+(?:\.[0-9]+)?)',
                r'price.*?\$([0-9,]+(?:\.[0-9]+)?)',
            ]

            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    price_str = match.group(1).replace(',', '')
                    return float(price_str)

            # Also try to find it in specific elements
            selectors = [
                '[data-testid="market-title"]',
                '.market-title',
                'h1',
                'h2',
            ]

            for selector in selectors:
                try:
                    element = await self.page.query_selector(selector)
                    if element:
                        text = await element.inner_text()
                        match = re.search(r'\$([0-9,]+(?:\.[0-9]+)?)', text)
                        if match:
                            return float(match.group(1).replace(',', ''))
                except:
                    continue

        except Exception as e:
            print(f"[BROWSER] Error extracting price-to-beat: {e}")

        return None

    async def extract_share_prices(self):
        """
        Extract UP and DOWN share prices from the page.
        Returns (up_price, down_price) as floats (0-1 scale).
        """
        up_price = None
        down_price = None

        try:
            text = await self.get_page_text()

            # Look for price patterns like "45¢" or "45c" or "0.45"
            # Common patterns: "Yes 45¢" "No 55¢" or "Up 45¢" "Down 55¢"

            # Pattern for cents notation
            up_patterns = [
                r'(?:Yes|Up|UP)\s*[:.]?\s*(\d+(?:\.\d+)?)\s*[¢c%]',
                r'(?:Yes|Up|UP)\s*[:.]?\s*\$?0?\.(\d+)',
            ]

            down_patterns = [
                r'(?:No|Down|DOWN)\s*[:.]?\s*(\d+(?:\.\d+)?)\s*[¢c%]',
                r'(?:No|Down|DOWN)\s*[:.]?\s*\$?0?\.(\d+)',
            ]

            for pattern in up_patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    val = float(match.group(1))
                    up_price = val / 100 if val > 1 else val
                    break

            for pattern in down_patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    val = float(match.group(1))
                    down_price = val / 100 if val > 1 else val
                    break

            # Try to extract from order book elements if available
            try:
                # Look for price elements in the order book section
                price_elements = await self.page.query_selector_all('[class*="price"], [class*="Price"]')
                for elem in price_elements[:10]:  # Check first 10 price elements
                    text = await elem.inner_text()
                    # Parse price values
                    pass  # TODO: More specific selectors needed
            except:
                pass

        except Exception as e:
            print(f"[BROWSER] Error extracting share prices: {e}")

        return up_price, down_price

    async def extract_btc_price(self):
        """
        Extract current BTC price if displayed on page.
        """
        try:
            text = await self.get_page_text()

            # Look for BTC price patterns
            patterns = [
                r'BTC[:/]?\s*\$([0-9,]+(?:\.[0-9]+)?)',
                r'Bitcoin[:/]?\s*\$([0-9,]+(?:\.[0-9]+)?)',
                r'Current[:/]?\s*\$([0-9,]+(?:\.[0-9]+)?)',
            ]

            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    return float(match.group(1).replace(',', ''))

        except Exception as e:
            print(f"[BROWSER] Error extracting BTC price: {e}")

        return None

    async def extract_time_remaining(self):
        """
        Extract time remaining from countdown timer.
        Returns seconds remaining.
        """
        try:
            text = await self.get_page_text()

            # Look for time patterns like "14:30" or "5:45" or "2m 30s"
            patterns = [
                r'(\d+):(\d+):(\d+)',  # HH:MM:SS
                r'(\d+):(\d+)',  # MM:SS
                r'(\d+)m\s*(\d+)s',  # Xm Xs
                r'(\d+)\s*min',  # X min
            ]

            for i, pattern in enumerate(patterns):
                match = re.search(pattern, text)
                if match:
                    groups = match.groups()
                    if i == 0:  # HH:MM:SS
                        return int(groups[0]) * 3600 + int(groups[1]) * 60 + int(groups[2])
                    elif i == 1:  # MM:SS
                        return int(groups[0]) * 60 + int(groups[1])
                    elif i == 2:  # Xm Xs
                        return int(groups[0]) * 60 + int(groups[1])
                    elif i == 3:  # X min
                        return int(groups[0]) * 60

        except Exception as e:
            print(f"[BROWSER] Error extracting time remaining: {e}")

        return None

    async def fetch_all_browser_data(self, slug):
        """
        Fetch all data points from browser.
        Returns dict with: price_to_beat, up_ask, down_ask, btc_price, time_remaining
        """
        if not self._initialized:
            await self.initialize()

        # Navigate to page
        success = await self.navigate_to_market(slug)
        if not success:
            return {
                'window_id': slug,
                'price_to_beat': None,
                'up_ask': None,
                'down_ask': None,
                'btc_price': None,
                'time_remaining': None,
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'source': 'browser',
                'error': 'Failed to navigate'
            }

        # Extract all data
        price_to_beat = await self.extract_price_to_beat()
        up_ask, down_ask = await self.extract_share_prices()
        btc_price = await self.extract_btc_price()
        time_remaining = await self.extract_time_remaining()

        return {
            'window_id': slug,
            'price_to_beat': price_to_beat,
            'up_ask': up_ask,
            'down_ask': down_ask,
            'btc_price': btc_price,
            'time_remaining': time_remaining,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'source': 'browser'
        }


# Synchronous wrapper for easier use
class SyncBrowserScraper:
    """Synchronous wrapper around async BrowserScraper"""

    def __init__(self):
        self._scraper = BrowserScraper()
        self._loop = None

    def _get_loop(self):
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
        return self._loop

    def initialize(self):
        loop = self._get_loop()
        loop.run_until_complete(self._scraper.initialize())

    def close(self):
        loop = self._get_loop()
        loop.run_until_complete(self._scraper.close())

    def fetch_all_browser_data(self, slug):
        loop = self._get_loop()
        return loop.run_until_complete(self._scraper.fetch_all_browser_data(slug))


if __name__ == '__main__':
    # Test the module
    import sys
    sys.path.insert(0, '/home/user/MarkWatney')
    from data_fetcher import get_current_window

    print("Testing browser_scraper.py...")
    print("-" * 50)

    slug, _ = get_current_window()
    print(f"Testing with window: {slug}")

    scraper = SyncBrowserScraper()
    try:
        scraper.initialize()
        data = scraper.fetch_all_browser_data(slug)

        print(f"Window ID: {data['window_id']}")
        print(f"Price-to-Beat: ${data['price_to_beat']:,.2f}" if data['price_to_beat'] else "Price-to-Beat: N/A")
        print(f"UP Ask: {data['up_ask']*100:.1f}c" if data['up_ask'] else "UP Ask: N/A")
        print(f"DOWN Ask: {data['down_ask']*100:.1f}c" if data['down_ask'] else "DOWN Ask: N/A")
        print(f"BTC Price: ${data['btc_price']:,.2f}" if data['btc_price'] else "BTC Price: N/A")
        print(f"Time Remaining: {data['time_remaining']:.0f}s" if data['time_remaining'] else "Time Remaining: N/A")
    finally:
        scraper.close()
