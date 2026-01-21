#!/usr/bin/env python3
"""
Test script for Google Sheets dashboard.
Creates sample window data and logs to dashboard.
"""
from sheets_dashboard import init_dashboard, log_dashboard_row

def test_dashboard():
    print("Testing Google Sheets Dashboard...")

    dashboard = init_dashboard()
    if not dashboard or not dashboard.enabled:
        print("Dashboard not enabled. Check:")
        print("  1. GOOGLE_SHEETS_CREDENTIALS_FILE env var")
        print("  2. SHARE_WITH_EMAIL env var (for auto-share)")
        return False

    print("Dashboard connected!")

    # Test 1: ARB PAIRED trade (should be green)
    test_state_1 = {
        'slug': 'btc-updown-15m-1737417600',
        'arb_entry': {'up_shares': 5, 'down_shares': 5},
        'arb_result': 'PAIRED',
        'arb_pnl': 0.05,
        'capture_entry': None,
        'capture_result': None,
        'capture_pnl': 0,
    }
    print("Logging test 1: ARB PAIRED (+$0.05)...")
    log_dashboard_row(test_state_1)

    # Test 2: ARB BAIL trade (should be red)
    test_state_2 = {
        'slug': 'btc-updown-15m-1737418500',
        'arb_entry': {'up_shares': 5, 'down_shares': 0},
        'arb_result': 'BAIL',
        'arb_pnl': -0.15,
        'capture_entry': None,
        'capture_result': None,
        'capture_pnl': 0,
    }
    print("Logging test 2: ARB BAIL (-$0.15)...")
    log_dashboard_row(test_state_2)

    # Test 3: 99c capture WIN (should be green)
    test_state_3 = {
        'slug': 'btc-updown-15m-1737419400',
        'arb_entry': None,
        'arb_result': None,
        'arb_pnl': 0,
        'capture_entry': {'side': 'UP', 'shares': 5},
        'capture_result': 'WIN',
        'capture_pnl': 0.05,
    }
    print("Logging test 3: 99c WIN (+$0.05)...")
    log_dashboard_row(test_state_3)

    # Test 4: 99c capture LOSS (should be red)
    test_state_4 = {
        'slug': 'btc-updown-15m-1737420300',
        'arb_entry': None,
        'arb_result': None,
        'arb_pnl': 0,
        'capture_entry': {'side': 'DOWN', 'shares': 5},
        'capture_result': 'LOSS',
        'capture_pnl': -4.95,
    }
    print("Logging test 4: 99c LOSS (-$4.95)...")
    log_dashboard_row(test_state_4)

    print()
    print("Test complete! Check your Google Sheet:")
    print("  - 4 data rows should appear")
    print("  - PAIRED/WIN cells should be GREEN")
    print("  - BAIL/LOSS cells should be RED")
    print("  - Emoji indicators: check mark for wins, X for losses")
    print("  - Summary row should show totals and win rates")

    return True

if __name__ == "__main__":
    test_dashboard()
