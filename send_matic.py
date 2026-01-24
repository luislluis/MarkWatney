#!/usr/bin/env python3
"""
Send MATIC from Safe (Proxy Wallet) to EOA
==========================================
Use this to fund the EOA with gas for redemptions.

Usage:
    # Edit EOA and AMOUNT at bottom of file, then:
    python send_matic.py

Configuration (in ~/.env):
    POLYGON_RPC=https://polygon-mainnet.g.alchemy.com/v2/YOUR_KEY
    PRIVATE_KEY=your_private_key
    WALLET_ADDRESS=your_proxy_wallet_address
"""
import os
from dotenv import load_dotenv
load_dotenv(os.path.expanduser("~/.env"))

from web3 import Web3
from eth_account import Account

POLYGON_RPC = os.getenv("POLYGON_RPC")
PRIVATE_KEY = os.getenv("PK") or os.getenv("PRIVATE_KEY")
PROXY_WALLET = os.getenv("WALLET_ADDRESS")

# Safe ABI for sending ETH/MATIC
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

def send_matic(to_address, amount_matic):
    w3 = Web3(Web3.HTTPProvider(POLYGON_RPC))
    if not w3.is_connected():
        print("Not connected to RPC")
        return False

    account = Account.from_key(PRIVATE_KEY)
    safe = w3.eth.contract(address=w3.to_checksum_address(PROXY_WALLET), abi=SAFE_ABI)

    to_addr = w3.to_checksum_address(to_address)
    value = w3.to_wei(amount_matic, 'ether')
    data = b''
    operation = 0
    safe_tx_gas = 0
    base_gas = 0
    gas_price_param = 0
    gas_token = "0x0000000000000000000000000000000000000000"
    refund_receiver = "0x0000000000000000000000000000000000000000"

    safe_nonce = safe.functions.nonce().call()
    print(f"Safe nonce: {safe_nonce}")

    tx_hash_to_sign = safe.functions.getTransactionHash(
        to_addr, value, data, operation, safe_tx_gas, base_gas,
        gas_price_param, gas_token, refund_receiver, safe_nonce
    ).call()

    signature = account.unsafe_sign_hash(tx_hash_to_sign)
    v = signature.v if signature.v >= 27 else signature.v + 27
    sig_bytes = signature.r.to_bytes(32, 'big') + signature.s.to_bytes(32, 'big') + bytes([v])

    exec_tx = safe.functions.execTransaction(
        to_addr, value, data, operation, safe_tx_gas, base_gas,
        gas_price_param, gas_token, refund_receiver, sig_bytes
    ).build_transaction({
        'from': account.address,
        'nonce': w3.eth.get_transaction_count(account.address),
        'gas': 150000,
        'gasPrice': int(w3.eth.gas_price * 1.2),
        'chainId': 137
    })

    signed_tx = account.sign_transaction(exec_tx)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    print(f"TX sent: {tx_hash.hex()}")
    print(f"Waiting for confirmation...")

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if receipt['status'] == 1:
        print(f"SUCCESS! Sent {amount_matic} MATIC to {to_address}")
        return True
    else:
        print("Transaction failed")
        return False

if __name__ == "__main__":
    # Edit these values as needed
    EOA = "0xa0bC1d8209B6601B0Ed99cA82a550f53FA3447F7"
    AMOUNT = 0.5  # MATIC

    print(f"Sending {AMOUNT} MATIC from Safe to EOA...")
    send_matic(EOA, AMOUNT)
