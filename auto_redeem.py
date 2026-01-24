#!/usr/bin/env python3
"""
Auto-Redeem Module for Polymarket
=================================
Detects resolved positions and redeems them directly via CTF contract.

Usage:
    # Test detection only (no transactions)
    python auto_redeem.py --test

    # Run continuous monitoring with auto-redeem
    python auto_redeem.py

    # Integrated in bot
    from auto_redeem import check_claimable_positions, redeem_position

Configuration (in ~/.env):
    POLYGON_RPC=https://polygon-mainnet.g.alchemy.com/v2/YOUR_KEY
    PRIVATE_KEY=your_private_key
    WALLET_ADDRESS=your_proxy_wallet_address

Notes:
    - Use Alchemy/Infura RPC to avoid rate limits (polygon-rpc.com gets throttled)
    - EOA needs MATIC for gas (~0.002-0.005 per redeem)
    - Retry logic handles temporary RPC rate limits with exponential backoff
"""

import os
import sys
import time
import requests
from datetime import datetime
from dotenv import load_dotenv

# Load from home directory
load_dotenv(os.path.expanduser("~/.env"))

# Configuration
POLYGON_RPC = os.getenv("POLYGON_RPC", "https://polygon-rpc.com")
PRIVATE_KEY = os.getenv("PK") or os.getenv("PRIVATE_KEY") or os.getenv("POLYMARKET_PRIVATE_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Contract addresses (Polygon)
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

# Safe/Proxy wallet (where positions are held)
PROXY_WALLET = os.getenv("WALLET_ADDRESS")

# CTF Contract ABI (minimal - just redeemPositions)
CTF_ABI = [
    {
        "inputs": [
            {"name": "collateralToken", "type": "address"},
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId", "type": "bytes32"},
            {"name": "indexSets", "type": "uint256[]"}
        ],
        "name": "redeemPositions",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    }
]

# Gnosis Safe ABI (for execTransaction)
SAFE_ABI = [
    {
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "data", "type": "bytes"},
            {"name": "operation", "type": "uint8"},
            {"name": "safeTxGas", "type": "uint256"},
            {"name": "baseGas", "type": "uint256"},
            {"name": "gasPrice", "type": "uint256"},
            {"name": "gasToken", "type": "address"},
            {"name": "refundReceiver", "type": "address"},
            {"name": "signatures", "type": "bytes"}
        ],
        "name": "execTransaction",
        "outputs": [{"name": "success", "type": "bool"}],
        "stateMutability": "payable",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "nonce",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "getOwners",
        "outputs": [{"name": "", "type": "address[]"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "data", "type": "bytes"},
            {"name": "operation", "type": "uint8"},
            {"name": "safeTxGas", "type": "uint256"},
            {"name": "baseGas", "type": "uint256"},
            {"name": "gasPrice", "type": "uint256"},
            {"name": "gasToken", "type": "address"},
            {"name": "refundReceiver", "type": "address"},
            {"name": "_nonce", "type": "uint256"}
        ],
        "name": "getTransactionHash",
        "outputs": [{"name": "", "type": "bytes32"}],
        "stateMutability": "view",
        "type": "function"
    }
]

# Track already-processed positions to avoid duplicate redemptions
redeemed_positions = set()

# Web3 setup (lazy loaded)
_w3 = None

def get_web3():
    """Get or create Web3 instance"""
    global _w3
    if _w3 is None:
        try:
            from web3 import Web3
            _w3 = Web3(Web3.HTTPProvider(POLYGON_RPC))
            if not _w3.is_connected():
                print(f"[REDEEM] Warning: Web3 not connected to {POLYGON_RPC}")
        except ImportError:
            print("[REDEEM] Error: web3 not installed. Run: pip install web3")
            return None
    return _w3


def retry_on_rate_limit(func, max_retries=3, initial_delay=10):
    """
    Decorator/wrapper to retry RPC calls on rate limit errors.
    Handles error code -32090 (rate limit) with exponential backoff.
    """
    def wrapper(*args, **kwargs):
        delay = initial_delay
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                error_str = str(e)
                # Check for rate limit error
                if '-32090' in error_str or 'rate limit' in error_str.lower() or 'too many requests' in error_str.lower():
                    if attempt < max_retries - 1:
                        print(f"[REDEEM] Rate limited, waiting {delay}s before retry {attempt + 2}/{max_retries}...")
                        time.sleep(delay)
                        delay *= 2  # Exponential backoff
                        continue
                raise  # Re-raise if not rate limit or max retries exceeded
        return None
    return wrapper


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
        print("[REDEEM] Warning: eth_account not installed")
        return None
    except Exception as e:
        print(f"[REDEEM] Error deriving address: {e}")
        return None


def get_user_positions(wallet_address):
    """Fetch user's positions from Polymarket data API"""
    if not wallet_address:
        return []

    try:
        url = f"https://data-api.polymarket.com/positions"
        params = {
            "user": wallet_address.lower(),
            "sizeThreshold": 0.01
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
            tokens = data.get('tokens', [])
            for token in tokens:
                if token.get('winner') == True:
                    return {
                        'resolved': True,
                        'winner': token.get('outcome'),
                        'token_id': token.get('token_id')
                    }
            return {'resolved': False}
    except Exception as e:
        print(f"[REDEEM] Error checking resolution: {e}")

    return {'resolved': False}


def check_claimable_positions(include_already_processed=False):
    """
    Check for resolved positions that can be claimed.
    Returns list of claimable positions with details.

    Uses the 'redeemable' field from the data API which indicates
    positions that are ready to claim.
    """
    # Use proxy wallet (where positions are held), not EOA
    wallet = PROXY_WALLET
    if not wallet:
        print("[REDEEM] No WALLET_ADDRESS (proxy) configured")
        return []

    positions = get_user_positions(wallet)
    claimable = []

    for pos in positions:
        # Must be redeemable AND have actual value (currentValue > 0 means winning position)
        if not pos.get('redeemable'):
            continue

        current_value = float(pos.get('currentValue', 0))
        if current_value <= 0:
            continue  # Losing position - nothing to claim

        condition_id = pos.get('conditionId') or pos.get('condition_id')
        if not condition_id:
            continue

        # Check if already processed (unless we want to include them)
        pos_key = f"{condition_id}_{pos.get('outcome')}"
        if not include_already_processed and pos_key in redeemed_positions:
            continue

        claimable.append({
            'condition_id': condition_id,
            'market': pos.get('title', pos.get('slug', 'Unknown')),
            'outcome': pos.get('outcome'),
            'shares': float(pos.get('size', 0)),
            'claimable_usdc': current_value,  # Use actual value from API
            'pos_key': pos_key
        })

    return claimable


def redeem_position(condition_id):
    """
    Redeem a resolved position through the Safe/proxy wallet.

    Flow:
    1. Encode the CTF redeemPositions call
    2. Sign a Safe transaction with our EOA
    3. Execute via Safe's execTransaction

    Returns: (success: bool, tx_hash: str or None)
    """
    if not PRIVATE_KEY:
        print("[REDEEM] No private key configured")
        return False, None

    if not PROXY_WALLET:
        print("[REDEEM] No WALLET_ADDRESS (proxy) configured")
        return False, None

    w3 = get_web3()
    if not w3:
        return False, None

    try:
        from eth_account import Account
        from eth_account.messages import defunct_hash_message

        account = Account.from_key(PRIVATE_KEY)

        # Contract instances
        ctf = w3.eth.contract(
            address=w3.to_checksum_address(CTF_ADDRESS),
            abi=CTF_ABI
        )
        safe = w3.eth.contract(
            address=w3.to_checksum_address(PROXY_WALLET),
            abi=SAFE_ABI
        )

        # Prepare condition_id as bytes32
        if condition_id.startswith('0x'):
            cond_bytes = bytes.fromhex(condition_id[2:])
        else:
            cond_bytes = bytes.fromhex(condition_id)

        if len(cond_bytes) < 32:
            cond_bytes = cond_bytes.rjust(32, b'\x00')

        # Step 1: Encode the CTF redeemPositions call
        redeem_data = ctf.encode_abi(
            'redeemPositions',
            [
                w3.to_checksum_address(USDC_ADDRESS),  # collateralToken
                bytes(32),                              # parentCollectionId (zero)
                cond_bytes,                             # conditionId
                [1, 2]                                  # indexSets for binary market
            ]
        )

        print(f"[REDEEM] Encoded redeem call for condition: {condition_id[:20]}...")

        # Step 2: Get Safe nonce and prepare transaction hash (with retry)
        def get_nonce():
            return safe.functions.nonce().call()
        safe_nonce = retry_on_rate_limit(get_nonce)()
        print(f"[REDEEM] Safe nonce: {safe_nonce}")

        # Safe transaction parameters
        to_addr = w3.to_checksum_address(CTF_ADDRESS)
        value = 0
        data = bytes.fromhex(redeem_data[2:])  # Remove 0x prefix
        operation = 0  # Call (not delegatecall)
        safe_tx_gas = 0
        base_gas = 0
        gas_price_param = 0
        gas_token = "0x0000000000000000000000000000000000000000"
        refund_receiver = "0x0000000000000000000000000000000000000000"

        # Get transaction hash from Safe contract (with retry)
        def get_tx_hash():
            return safe.functions.getTransactionHash(
                to_addr,
                value,
                data,
                operation,
                safe_tx_gas,
                base_gas,
                gas_price_param,
                gas_token,
                refund_receiver,
                safe_nonce
            ).call()
        tx_hash_to_sign = retry_on_rate_limit(get_tx_hash)()

        print(f"[REDEEM] Safe tx hash to sign: {tx_hash_to_sign.hex()}")

        # Step 3: Sign the transaction hash with EOA
        # For Safe, we sign the raw hash (not EIP-191 prefixed)
        signature = account.unsafe_sign_hash(tx_hash_to_sign)

        # Format signature for Safe: r + s + v (v adjusted for Safe)
        # Safe expects v to be 27 or 28 for EOA signatures
        v = signature.v
        if v < 27:
            v += 27

        sig_bytes = (
            signature.r.to_bytes(32, 'big') +
            signature.s.to_bytes(32, 'big') +
            bytes([v])
        )

        print(f"[REDEEM] Signature created, executing Safe transaction...")

        # Step 4: Execute via Safe's execTransaction
        exec_tx = safe.functions.execTransaction(
            to_addr,
            value,
            data,
            operation,
            safe_tx_gas,
            base_gas,
            gas_price_param,
            gas_token,
            refund_receiver,
            sig_bytes
        ).build_transaction({
            'from': account.address,
            'nonce': w3.eth.get_transaction_count(account.address),
            'gas': 500000,
            'gasPrice': int(w3.eth.gas_price * 1.2),  # 20% above current to ensure inclusion
            'chainId': 137
        })

        # Sign and send (with retry)
        signed_tx = account.sign_transaction(exec_tx)

        def send_tx():
            return w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        tx_hash = retry_on_rate_limit(send_tx)()

        print(f"[REDEEM] Transaction sent: {tx_hash.hex()}")
        print(f"[REDEEM] View on Polygonscan: https://polygonscan.com/tx/{tx_hash.hex()}")
        print(f"[REDEEM] Waiting for confirmation...")

        # Wait for confirmation
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

        if receipt['status'] == 1:
            print(f"[REDEEM] SUCCESS! TX: {tx_hash.hex()}")
            print(f"[REDEEM] Gas used: {receipt['gasUsed']}")
            return True, tx_hash.hex()
        else:
            print(f"[REDEEM] Transaction FAILED: {tx_hash.hex()}")
            return False, tx_hash.hex()

    except Exception as e:
        print(f"[REDEEM] Error: {e}")
        import traceback
        traceback.print_exc()
        return False, None


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
    message += "Attempting auto-redeem..."

    send_telegram(message)
    print(f"[REDEEM] Notified about ${total:.2f} claimable")


def check_and_claim(dry_run=False):
    """Main function: check for claimable positions and redeem them"""
    claimable = check_claimable_positions()

    if not claimable:
        return []

    print(f"[REDEEM] Found {len(claimable)} claimable position(s)")
    notify_claimable(claimable)

    if dry_run:
        print("[REDEEM] DRY RUN - Not executing transactions")
        return claimable

    for pos in claimable:
        print(f"\n[REDEEM] Processing: {pos['market']}")
        print(f"[REDEEM] Shares: {pos['shares']:.2f}, Value: ${pos['claimable_usdc']:.2f}")
        print(f"[REDEEM] Condition ID: {pos['condition_id']}")

        success, tx_hash = redeem_position(pos['condition_id'])

        if success:
            redeemed_positions.add(pos['pos_key'])
            send_telegram(
                f"✅ <b>REDEEMED</b>\n"
                f"Market: {pos['market']}\n"
                f"Amount: ${pos['claimable_usdc']:.2f}\n"
                f"TX: <a href='https://polygonscan.com/tx/{tx_hash}'>{tx_hash[:16]}...</a>"
            )
        else:
            send_telegram(
                f"⚠️ <b>REDEEM FAILED</b>\n"
                f"Market: {pos['market']}\n"
                f"Amount: ${pos['claimable_usdc']:.2f}\n"
                f"Manual claim may be required"
            )

    return claimable


def test_redeem_detection():
    """Test: Find claimable positions without actually redeeming"""
    print("=" * 60)
    print("REDEEM TEST - Detection Only (No Transactions)")
    print("=" * 60)

    eoa_wallet = get_wallet_address()
    proxy_wallet = PROXY_WALLET

    print(f"EOA (signer): {eoa_wallet}")
    print(f"Proxy (positions): {proxy_wallet}")

    if not eoa_wallet:
        print("ERROR: Could not derive EOA address")
        print("Check that PRIVATE_KEY is set in ~/.env")
        return

    if not proxy_wallet:
        print("ERROR: No WALLET_ADDRESS (proxy) configured")
        return

    # Check Web3 connection
    w3 = get_web3()
    if w3:
        print(f"Web3 connected: {w3.is_connected()}")
        if w3.is_connected():
            eoa_balance = w3.eth.get_balance(eoa_wallet)
            proxy_balance = w3.eth.get_balance(proxy_wallet)
            print(f"EOA MATIC balance: {w3.from_wei(eoa_balance, 'ether'):.4f}")
            print(f"Proxy MATIC balance: {w3.from_wei(proxy_balance, 'ether'):.4f}")

            # Check if proxy is Safe and EOA is owner
            try:
                safe = w3.eth.contract(address=w3.to_checksum_address(proxy_wallet), abi=SAFE_ABI)
                owners = safe.functions.getOwners().call()
                is_owner = any(o.lower() == eoa_wallet.lower() for o in owners)
                print(f"Safe ownership verified: {is_owner}")
            except:
                print("Could not verify Safe ownership")
    else:
        print("Web3: Not available (install with: pip install web3)")

    print("\nFetching positions...")
    positions = get_user_positions(proxy_wallet)
    print(f"Total positions found: {len(positions)}")

    print("\nChecking for claimable (resolved + winning)...")
    claimable = check_claimable_positions(include_already_processed=True)

    if not claimable:
        print("\nNo claimable positions found.")
        print("(Either no resolved markets, or you didn't win)")
        return

    print(f"\n{'='*60}")
    print(f"FOUND {len(claimable)} CLAIMABLE POSITION(S):")
    print(f"{'='*60}\n")

    total = 0
    for i, pos in enumerate(claimable, 1):
        print(f"{i}. {pos['market']}")
        print(f"   Outcome: {pos['outcome']}")
        print(f"   Shares: {pos['shares']:.2f}")
        print(f"   Value: ${pos['claimable_usdc']:.2f}")
        print(f"   Condition ID: {pos['condition_id']}")
        print()
        total += pos['claimable_usdc']

    print(f"{'='*60}")
    print(f"TOTAL CLAIMABLE: ${total:.2f}")
    print(f"{'='*60}")
    print("\nTo redeem, run: python auto_redeem.py")


def run_loop(interval=60):
    """Run continuous checking loop with auto-redeem"""
    print("=" * 60)
    print("AUTO-REDEEM MONITOR")
    print("=" * 60)
    print(f"Wallet: {get_wallet_address()}")
    print(f"Check interval: {interval}s")
    print(f"Mode: Direct CTF Contract Redemption")
    print("=" * 60)

    while True:
        try:
            check_and_claim(dry_run=False)
        except Exception as e:
            print(f"[REDEEM] Error: {e}")
            import traceback
            traceback.print_exc()

        time.sleep(interval)


if __name__ == "__main__":
    if "--test" in sys.argv:
        test_redeem_detection()
    else:
        run_loop(interval=60)
