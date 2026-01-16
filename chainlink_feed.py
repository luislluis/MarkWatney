"""
Chainlink BTC/USD Price Feed
============================
Reads the BTC/USD price directly from Chainlink's on-chain price feed.
This is the SAME source Polymarket uses for settlement.

Contract: 0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c (Ethereum Mainnet)
Source: https://data.chain.link/feeds/ethereum/mainnet/btc-usd
"""

from web3 import Web3
import time

# Chainlink BTC/USD Aggregator ABI (only the functions we need)
AGGREGATOR_ABI = [
    {
        "inputs": [],
        "name": "latestRoundData",
        "outputs": [
            {"name": "roundId", "type": "uint80"},
            {"name": "answer", "type": "int256"},
            {"name": "startedAt", "type": "uint256"},
            {"name": "updatedAt", "type": "uint256"},
            {"name": "answeredInRound", "type": "uint80"}
        ],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function"
    }
]

# Contract address - Chainlink BTC/USD on Ethereum Mainnet
BTC_USD_ETH_MAINNET = "0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c"

# Free public RPC endpoints (no API key needed)
FREE_RPC_ENDPOINTS = [
    "https://eth.llamarpc.com",
    "https://rpc.ankr.com/eth",
    "https://ethereum.publicnode.com",
    "https://1rpc.io/eth",
]


class ChainlinkPriceFeed:
    """Read BTC/USD price from Chainlink on-chain oracle."""

    def __init__(self, rpc_url=None):
        """
        Initialize Chainlink price feed.

        Args:
            rpc_url: Ethereum RPC endpoint. If None, uses free public endpoint.
        """
        self.rpc_url = rpc_url or FREE_RPC_ENDPOINTS[0]
        self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))
        self.contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(BTC_USD_ETH_MAINNET),
            abi=AGGREGATOR_ABI
        )
        self.decimals = 8  # BTC/USD uses 8 decimal places
        self.last_price = None
        self.last_update = 0
        self.last_fetch_time = 0

    def get_price(self):
        """
        Get latest BTC/USD price from Chainlink.

        Returns:
            tuple: (price_usd, updated_at_timestamp)
        """
        try:
            data = self.contract.functions.latestRoundData().call()
            # data = (roundId, answer, startedAt, updatedAt, answeredInRound)
            raw_price = data[1]
            updated_at = data[3]

            # Convert to USD (divide by 10^8)
            price = raw_price / (10 ** self.decimals)

            self.last_price = price
            self.last_update = updated_at
            self.last_fetch_time = time.time()

            return price, updated_at

        except Exception as e:
            print(f"[Chainlink] Error fetching price: {e}")
            # Try fallback RPC endpoints
            for fallback_rpc in FREE_RPC_ENDPOINTS[1:]:
                try:
                    self.w3 = Web3(Web3.HTTPProvider(fallback_rpc))
                    self.contract = self.w3.eth.contract(
                        address=Web3.to_checksum_address(BTC_USD_ETH_MAINNET),
                        abi=AGGREGATOR_ABI
                    )
                    data = self.contract.functions.latestRoundData().call()
                    raw_price = data[1]
                    updated_at = data[3]
                    price = raw_price / (10 ** self.decimals)
                    self.last_price = price
                    self.last_update = updated_at
                    print(f"[Chainlink] Switched to fallback RPC: {fallback_rpc}")
                    return price, updated_at
                except:
                    continue

            return self.last_price, self.last_update

    def get_price_with_age(self):
        """
        Get price and how many seconds old the on-chain data is.

        Returns:
            tuple: (price_usd, age_seconds)
        """
        price, updated_at = self.get_price()
        age_seconds = int(time.time()) - updated_at if updated_at else 0
        return price, age_seconds

    def is_connected(self):
        """Check if connected to Ethereum RPC."""
        try:
            return self.w3.is_connected()
        except:
            return False


# Global instance for easy access
_feed = None

def get_chainlink_feed():
    """Get or create global Chainlink feed instance."""
    global _feed
    if _feed is None:
        _feed = ChainlinkPriceFeed()
    return _feed

def get_btc_price():
    """Quick function to get BTC price."""
    feed = get_chainlink_feed()
    price, _ = feed.get_price()
    return price

def get_btc_price_with_age():
    """Quick function to get BTC price and age."""
    feed = get_chainlink_feed()
    return feed.get_price_with_age()


# Test function
if __name__ == "__main__":
    print("Testing Chainlink BTC/USD Price Feed")
    print("=" * 50)

    feed = ChainlinkPriceFeed()

    if not feed.is_connected():
        print("ERROR: Not connected to Ethereum RPC")
        exit(1)

    print(f"Connected to: {feed.rpc_url}")
    print(f"Contract: {BTC_USD_ETH_MAINNET}")
    print()

    # Fetch price
    price, age = feed.get_price_with_age()

    if price:
        print(f"BTC/USD Price: ${price:,.2f}")
        print(f"Data Age: {age} seconds")
        print()
        print("Compare to: https://data.chain.link/feeds/ethereum/mainnet/btc-usd")
    else:
        print("ERROR: Failed to fetch price")
