#!/usr/bin/env python3
"""
Polymarket Data Verification Bot

Compares API data against browser-displayed values every 0.5 seconds
to verify data accuracy.

Usage:
    python3 verify_data.py

Output shows side-by-side comparison with match indicators.
"""

import sys
import time
import signal
from datetime import datetime

# Add project path
sys.path.insert(0, '/home/user/MarkWatney')

from data_fetcher import fetch_all_api_data, get_current_window
from browser_scraper import SyncBrowserScraper

# ANSI colors for terminal output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
CYAN = '\033[96m'
RESET = '\033[0m'
BOLD = '\033[1m'


def format_price(price, prefix='$'):
    """Format price for display"""
    if price is None:
        return 'N/A'.center(12)
    return f"{prefix}{price:,.2f}".center(12)


def format_cents(price):
    """Format share price in cents"""
    if price is None:
        return 'N/A'.center(10)
    return f"{price*100:.1f}c".center(10)


def format_time(seconds):
    """Format time remaining"""
    if seconds is None:
        return 'N/A'
    mins = int(seconds) // 60
    secs = int(seconds) % 60
    return f"{mins}:{secs:02d}"


def compare_values(api_val, browser_val, tolerance=0):
    """
    Compare two values with optional tolerance.
    Returns (match, display_char)
    """
    if api_val is None or browser_val is None:
        return None, f'{YELLOW}?{RESET}'

    if tolerance == 0:
        match = api_val == browser_val
    else:
        match = abs(api_val - browser_val) <= tolerance

    if match:
        return True, f'{GREEN}✓{RESET}'
    else:
        return False, f'{RED}✗{RESET}'


def print_header():
    """Print the comparison table header"""
    print(f"\n{BOLD}{'='*80}{RESET}")
    print(f"{BOLD}Polymarket Data Verification Bot{RESET}")
    print(f"Comparing API vs Browser data every 0.5 seconds")
    print(f"{'='*80}\n")


def print_comparison(api_data, browser_data, first_run=False):
    """Print side-by-side comparison"""

    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    window_id = api_data.get('window_id', 'unknown')
    time_remaining = api_data.get('time_remaining')

    # Calculate matches
    ptb_match, ptb_char = compare_values(
        api_data.get('price_to_beat'),
        browser_data.get('price_to_beat'),
        tolerance=0.01  # Allow 1 cent tolerance for price-to-beat
    )

    up_match, up_char = compare_values(
        api_data.get('up_ask'),
        browser_data.get('up_ask'),
        tolerance=0  # Exact match required
    )

    down_match, down_char = compare_values(
        api_data.get('down_ask'),
        browser_data.get('down_ask'),
        tolerance=0  # Exact match required
    )

    btc_match, btc_char = compare_values(
        api_data.get('btc_price'),
        browser_data.get('btc_price'),
        tolerance=1.0  # Allow $1 tolerance for BTC price
    )

    # Count matches
    matches = [ptb_match, up_match, down_match, btc_match]
    match_count = sum(1 for m in matches if m is True)
    check_count = sum(1 for m in matches if m is not None)
    mismatch_count = sum(1 for m in matches if m is False)

    # Status color
    if mismatch_count > 0:
        status_color = RED
        status = "MISMATCH"
    elif check_count == 0:
        status_color = YELLOW
        status = "NO DATA"
    else:
        status_color = GREEN
        status = "OK"

    # Clear previous line if not first run
    if not first_run:
        # Move cursor up and clear (for single-line updates)
        pass

    # Print compact single-line update
    ttl_str = format_time(time_remaining) if time_remaining else "??:??"

    print(f"[{timestamp}] {CYAN}{window_id}{RESET} | T-{ttl_str} | "
          f"PTB: {ptb_char} | UP: {up_char} | DN: {down_char} | BTC: {btc_char} | "
          f"{status_color}{status}{RESET}")

    # Print detailed view every 10 iterations or on mismatch
    if mismatch_count > 0:
        print(f"\n{RED}{'─'*60}{RESET}")
        print(f"{'Source':<10} | {'Price-to-Beat':^14} | {'UP Ask':^10} | {'DOWN Ask':^10} | {'BTC Price':^12}")
        print(f"{'─'*60}")
        print(f"{'API':<10} | {format_price(api_data.get('price_to_beat'))} | "
              f"{format_cents(api_data.get('up_ask'))} | {format_cents(api_data.get('down_ask'))} | "
              f"{format_price(api_data.get('btc_price'))}")
        print(f"{'Browser':<10} | {format_price(browser_data.get('price_to_beat'))} | "
              f"{format_cents(browser_data.get('up_ask'))} | {format_cents(browser_data.get('down_ask'))} | "
              f"{format_price(browser_data.get('btc_price'))}")
        print(f"{'Match?':<10} | {ptb_char:^14} | {up_char:^10} | {down_char:^10} | {btc_char:^12}")
        print(f"{RED}{'─'*60}{RESET}\n")


def main():
    """Main verification loop"""
    print_header()

    # Initialize browser scraper
    print("Initializing browser scraper (this may take a few seconds)...")
    scraper = SyncBrowserScraper()

    # Handle graceful shutdown
    running = True

    def signal_handler(sig, frame):
        nonlocal running
        print(f"\n{YELLOW}Shutting down...{RESET}")
        running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        scraper.initialize()
        print(f"{GREEN}Browser initialized!{RESET}\n")

        iteration = 0
        current_slug = None

        while running:
            start_time = time.time()

            # Get current window
            slug, _ = get_current_window()

            # If window changed, navigate to new page
            if slug != current_slug:
                print(f"\n{CYAN}New window: {slug}{RESET}")
                current_slug = slug

            # Fetch data from both sources
            api_data = fetch_all_api_data()

            # Only fetch browser data every few iterations to reduce load
            # (browser scraping is slower)
            if iteration % 4 == 0:  # Every 2 seconds
                browser_data = scraper.fetch_all_browser_data(slug)
            else:
                # Use cached browser data with updated timestamp
                browser_data = getattr(main, '_cached_browser_data', {
                    'price_to_beat': None,
                    'up_ask': None,
                    'down_ask': None,
                    'btc_price': None
                })

            main._cached_browser_data = browser_data

            # Print comparison
            print_comparison(api_data, browser_data, first_run=(iteration == 0))

            iteration += 1

            # Wait for remaining time to hit 0.5s interval
            elapsed = time.time() - start_time
            sleep_time = max(0, 0.5 - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

    except KeyboardInterrupt:
        print(f"\n{YELLOW}Interrupted by user{RESET}")
    except Exception as e:
        print(f"\n{RED}Error: {e}{RESET}")
        import traceback
        traceback.print_exc()
    finally:
        print("Closing browser...")
        scraper.close()
        print(f"{GREEN}Done!{RESET}")


if __name__ == '__main__':
    main()
