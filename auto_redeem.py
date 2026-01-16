#!/usr/bin/env python3
"""
Auto-Redeem Module for Polymarket
=================================
Detects resolved positions and either:
1. Notifies user to claim manually (default)
2. Auto-redeems if Builder API credentials are configured

Usage:
    # As standalone
    python auto_redeem.py

    # Integrated in bot
    from auto_redeem import check_claimable_positions
"""

import os
import time
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# Configuration
POLYGON_RPC = os.getenv("POLYGON_RPC", "https://polygon-rpc.com")
PRIVATE_KEY = os.getenv("PK") or os.getenv("PRIVATE_KEY") or os.getenv("POLYMARKET_PRIVATE_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Builder API credentials (optional - for auto-redeem)
BUILDER_API_KEY = os.getenv("BUILDER_API_KEY")
BUILDER_SECRET = os.getenv("BUILDER_SECRET")
BUILDER_PASSPHRASE = os.getenv("BUILDER_PASS_PHRASE")

# Contract addresses (Polygon)
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

# Track already-notified positions to avoid spam
notified_positions = set()


def send_telegram(message):
    """Send notification via Telegram"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[REDEEM] {message}")
        return

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }
        requests.post(url, json=data, timeout=5)
    except Exception as e:
        print(f"[REDEEM] Telegram error: {e}")


def get_wallet_address():
    """Derive wallet address from private key"""
    if not PRIVATE_KEY:
        return None
    try:
        from eth_account import Account
        account = Account.from_key(PRIVATE_KEY)
        return account.address
    except ImportError:
        print("[REDEEM] Warning: eth_account not installed, can't derive address")
        return None
    except Exception as e:
        print(f"[REDEEM] Error deriving address: {e}")
        return None


def get_user_positions(wallet_address):
    """Fetch user's positions from Polymarket data API"""
    if not wallet_address:
        return []

    try:
        # Use the data API to get positions
        url = f"https://data-api.polymarket.com/positions"
        params = {
            "user": wallet_address.lower(),
            "sizeThreshold": 0.01  # Minimum position size
        }
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"[REDEEM] Error fetching positions: {e}")

    return []


def get_market_resolution(condition_id):
    """Check if a market has resolved and who won"""
    try:
        url = f"https://clob.polymarket.com/markets/{condition_id}"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            # Check tokens for winner
            tokens = data.get('tokens', [])
            for token in tokens:
                if token.get('winner') == True:
                    return {
                        'resolved': True,
                        'winner': token.get('outcome'),
                        'token_id': token.get('token_id')
                    }
            # Market not resolved yet
            return {'resolved': False}
    except Exception as e:
        print(f"[REDEEM] Error checking resolution: {e}")

    return {'resolved': False}


def check_claimable_positions():
    """
    Check for resolved positions that can be claimed.
    Returns list of claimable positions with details.
    """
    wallet = get_wallet_address()
    if not wallet:
        print("[REDEEM] No wallet address available")
        return []

    positions = get_user_positions(wallet)
    claimable = []

    for pos in positions:
        condition_id = pos.get('conditionId') or pos.get('condition_id')
        if not condition_id:
            continue

        # Check if already notified
        pos_key = f"{condition_id}_{pos.get('outcome')}"
        if pos_key in notified_positions:
            continue

        # Check resolution
        resolution = get_market_resolution(condition_id)
        if not resolution.get('resolved'):
            continue

        # Check if user has winning position
        user_outcome = pos.get('outcome')
        winning_outcome = resolution.get('winner')

        if user_outcome == winning_outcome:
            size = float(pos.get('size', 0))
            # Winning shares worth $1 each
            claimable_amount = size

            claimable.append({
                'condition_id': condition_id,
                'market': pos.get('title', pos.get('slug', 'Unknown')),
                'outcome': user_outcome,
                'shares': size,
                'claimable_usdc': claimable_amount,
                'token_id': resolution.get('token_id')
            })

            notified_positions.add(pos_key)

    return claimable


def notify_claimable(positions):
    """Send notification about claimable positions"""
    if not positions:
        return

    total = sum(p['claimable_usdc'] for p in positions)

    message = f"💰 <b>CLAIMABLE WINNINGS</b>\n\n"
    for p in positions:
        message += f"• {p['market']}\n"
        message += f"  Won: {p['outcome']} ({p['shares']:.2f} shares)\n"
        message += f"  Claim: ${p['claimable_usdc']:.2f}\n\n"

    message += f"<b>Total: ${total:.2f}</b>\n\n"
    message += "Go to polymarket.com to claim!"

    send_telegram(message)
    print(f"[REDEEM] Notified about ${total:.2f} claimable")


def has_builder_credentials():
    """Check if Builder API credentials are configured"""
    return all([BUILDER_API_KEY, BUILDER_SECRET, BUILDER_PASSPHRASE])


def auto_redeem_position(condition_id, index_sets):
    """
    Auto-redeem a position using Builder API.
    Requires: pip install polymarket-trade-executor[relayer]
    """
    if not has_builder_credentials():
        print("[REDEEM] Builder credentials not configured - manual claim required")
        return False

    try:
        from polymarket_trade_executor import TradeExecutor

        executor = TradeExecutor(
            host="https://clob.polymarket.com",
            private_key=PRIVATE_KEY,
            builder_api_key=BUILDER_API_KEY,
            builder_secret=BUILDER_SECRET,
            builder_passphrase=BUILDER_PASSPHRASE
        )

        # Execute redeem
        # Note: This is a placeholder - actual implementation may vary
        # based on the polymarket-trade-executor API
        result = executor.redeem(condition_id=condition_id, index_sets=index_sets)
        print(f"[REDEEM] Auto-redeemed: {result}")
        return True

    except ImportError:
        print("[REDEEM] polymarket-trade-executor not installed")
        print("[REDEEM] Install with: pip install polymarket-trade-executor[relayer]")
        return False
    except Exception as e:
        print(f"[REDEEM] Auto-redeem failed: {e}")
        return False


def check_and_claim():
    """Main function: check for claimable positions and process them"""
    claimable = check_claimable_positions()

    if not claimable:
        return []

    print(f"[REDEEM] Found {len(claimable)} claimable position(s)")

    if has_builder_credentials():
        # Try auto-redeem
        for pos in claimable:
            success = auto_redeem_position(
                pos['condition_id'],
                [1, 2]  # Standard index sets for binary markets
            )
            if success:
                send_telegram(f"✅ Auto-redeemed ${pos['claimable_usdc']:.2f} from {pos['market']}")
    else:
        # Just notify
        notify_claimable(claimable)

    return claimable


def run_loop(interval=60):
    """Run continuous checking loop"""
    print("=" * 60)
    print("AUTO-REDEEM MONITOR")
    print("=" * 60)
    print(f"Wallet: {get_wallet_address()}")
    print(f"Builder API: {'Configured' if has_builder_credentials() else 'Not configured (notify only)'}")
    print(f"Check interval: {interval}s")
    print("=" * 60)

    while True:
        try:
            check_and_claim()
        except Exception as e:
            print(f"[REDEEM] Error: {e}")

        time.sleep(interval)


if __name__ == "__main__":
    # Run standalone
    run_loop(interval=60)
