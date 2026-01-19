# Technology Stack

**Analysis Date:** 2026-01-19

## Languages

**Primary:**
- Python 3.x - All bot logic, trading, logging, blockchain interaction

**Secondary:**
- Google Apps Script (JavaScript) - `PolymarketSummary.gs` for Google Sheets automation

## Runtime

**Environment:**
- Python 3.x (standard CPython interpreter)
- Runs on Ubuntu Linux server (`174.138.5.183`)
- Local development on macOS

**Package Manager:**
- pip3 - No `requirements.txt` present (dependencies installed ad-hoc)
- Lockfile: Missing
- Uses `--break-system-packages` flag on server

## Frameworks

**Core:**
- None - Pure Python with library dependencies

**Testing:**
- None detected - No test framework configured

**Build/Dev:**
- None - Direct Python execution, no build step

## Key Dependencies

**Critical:**
- `py-clob-client` - Polymarket CLOB trading API client
  - Used in: `trading_bot_smart.py` (lines 409, 823-824)
  - Import: `from py_clob_client.client import ClobClient`
  - Import: `from py_clob_client.clob_types import OrderArgs`
  - Import: `from py_clob_client.order_builder.constants import BUY, SELL`

- `web3` - Ethereum/Polygon blockchain interaction
  - Used in: `chainlink_feed.py`, `auto_redeem.py`
  - Import: `from web3 import Web3`

- `python-dotenv` - Environment variable loading
  - Used in: `trading_bot_smart.py`, `auto_redeem.py`
  - Import: `from dotenv import load_dotenv`

**Infrastructure:**
- `requests` - HTTP requests to APIs
  - Used in: All Python files
  - For: Polymarket APIs, Telegram notifications, Coinbase fallback

- `gspread` - Google Sheets API client
  - Used in: `sheets_logger.py`
  - Import: `import gspread`

- `google-auth` - Google authentication
  - Used in: `sheets_logger.py`
  - Import: `from google.oauth2.service_account import Credentials`

- `eth-account` - Ethereum account management
  - Used in: `auto_redeem.py`
  - Import: `from eth_account import Account`

**Standard Library (notable usage):**
- `concurrent.futures.ThreadPoolExecutor` - Parallel execution
- `collections.deque` - Efficient fixed-size history buffers
- `zoneinfo.ZoneInfo` - Timezone handling (Pacific Time)
- `json` - Configuration and data persistence
- `signal` - Graceful shutdown handling

## Configuration

**Environment:**
- Credentials loaded from `~/.env` (NOT project folder)
- Required variables:
  - `PRIVATE_KEY` - Ethereum private key for trading
  - `WALLET_ADDRESS` - Proxy wallet address
  - `GOOGLE_SHEETS_SPREADSHEET_ID` - Sheets logging target
  - `GOOGLE_SHEETS_CREDENTIALS_FILE` - Path to service account JSON
  - `TELEGRAM_BOT_TOKEN` - Optional, for notifications
  - `TELEGRAM_CHAT_ID` - Optional, for notifications
  - `POLYGON_RPC` - Optional, defaults to `https://polygon-rpc.com`

**Build:**
- No build configuration - direct Python execution
- Log file: `~/polybot/bot.log`
- Google Sheets credentials: `~/.google_sheets_credentials.json`
- Telegram config: `~/.telegram-bot.json`

## Platform Requirements

**Development:**
- Python 3.10+ (uses `zoneinfo` module)
- macOS or Linux
- No IDE-specific requirements

**Production:**
- Ubuntu Linux server
- Python 3.x with pip
- Network access to:
  - Polymarket APIs
  - Ethereum RPC endpoints
  - Polygon RPC
  - Google Sheets API
  - Telegram API

## Install Commands

```bash
# Server installation (Ubuntu)
pip3 install python-dotenv --break-system-packages
pip3 install py-clob-client --break-system-packages
pip3 install gspread google-auth --break-system-packages
pip3 install web3 --break-system-packages
pip3 install eth-account --break-system-packages
```

## Version Management

Bot version tracked in `trading_bot_smart.py`:
```python
BOT_VERSION = {
    "version": "v1.5",
    "codename": "Houdini",
    "date": "2026-01-17",
    "changes": "..."
}
```

Version history maintained in `BOT_REGISTRY.md`.

---

*Stack analysis: 2026-01-19*
