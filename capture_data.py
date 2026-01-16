#!/usr/bin/env python3
"""
Polymarket BTC 15-minute Window Data Capture

Captures data every 0.5 seconds:
- Price-to-beat (window open price)
- UP/DOWN share prices
- Current BTC price
- Time remaining

Usage: python3 capture_data.py
"""

import sys
import time
import signal
from datetime import datetime

sys.path.insert(0, '/home/user/MarkWatney')
from data_fetcher import fetch_all_api_data, get_current_window

# ANSI colors
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
CYAN = '\033[96m'
RESET = '\033[0m'
BOLD = '\033[1m'
DIM = '\033[2m'


def format_time(seconds):
    """Format seconds as MM:SS"""
    if seconds is None:
        return "??:??"
    mins = int(seconds) // 60
    secs = int(seconds) % 60
    return f"{mins:02d}:{secs:02d}"


def print_header():
    print(f"\n{BOLD}{'='*75}{RESET}")
    print(f"{BOLD}Polymarket BTC 15-min Data Capture{RESET} (0.5s interval)")
    print(f"{'='*75}")
    print(f"{DIM}{'Time':<12} {'Window':<28} {'TTL':>6} {'PTB':>12} {'UP':>7} {'DN':>7} {'BTC':>12} {'Diff':>10}{RESET}")
    print(f"{'-'*75}")


def print_data(data, last_window):
    """Print a single data row"""
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-4]
    window = data['window_id'].replace('btc-updown-15m-', '')
    ttl = format_time(data['time_remaining'])

    ptb = f"${data['price_to_beat']:,.2f}" if data['price_to_beat'] else "N/A"
    up = f"{data['up_ask']*100:.1f}c" if data['up_ask'] else "N/A"
    dn = f"{data['down_ask']*100:.1f}c" if data['down_ask'] else "N/A"
    btc = f"${data['btc_price']:,.2f}" if data['btc_price'] else "N/A"

    # Calculate diff and direction
    if data['price_to_beat'] and data['btc_price']:
        diff = data['btc_price'] - data['price_to_beat']
        if diff >= 0:
            diff_str = f"{GREEN}+${diff:,.0f}{RESET}"
            direction = "UP"
        else:
            diff_str = f"{RED}-${abs(diff):,.0f}{RESET}"
            direction = "DN"
    else:
        diff_str = "N/A"
        direction = "?"

    # Highlight new window
    if data['window_id'] != last_window:
        print(f"\n{CYAN}>>> NEW WINDOW: {data['window_id']}{RESET}")

    print(f"{ts:<12} {window:<28} {ttl:>6} {ptb:>12} {up:>7} {dn:>7} {btc:>12} {diff_str:>10}")

    return data['window_id']


def main():
    print_header()

    running = True
    last_window = None
    iteration = 0

    def signal_handler(sig, frame):
        nonlocal running
        print(f"\n{YELLOW}Stopping capture...{RESET}")
        running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    while running:
        start = time.time()

        try:
            data = fetch_all_api_data()
            last_window = print_data(data, last_window)
        except Exception as e:
            print(f"{RED}Error: {e}{RESET}")

        iteration += 1

        # Sleep for remaining time to hit 0.5s interval
        elapsed = time.time() - start
        sleep_time = max(0, 0.5 - elapsed)
        if sleep_time > 0:
            time.sleep(sleep_time)

    print(f"\n{GREEN}Captured {iteration} data points.{RESET}")


if __name__ == '__main__':
    main()
