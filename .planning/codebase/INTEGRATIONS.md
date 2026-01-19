# External Integrations

**Analysis Date:** 2026-01-19

## APIs & External Services

**Polymarket CLOB API:**
- Purpose: Place and manage orders on prediction markets
- Base URL: `https://clob.polymarket.com`
- SDK/Client: `py-clob-client` package
- Auth: Private key (`PRIVATE_KEY` env var)
- Used in: `trading_bot_smart.py` (line 413)
- Endpoints:
  - Order placement via `ClobClient`
  - Order book: `GET /book?token_id={token_id}`
  - Markets: `GET /markets/{condition_id}`

**Polymarket Gamma API:**
- Purpose: Market discovery and metadata
- Base URL: `https://gamma-api.polymarket.com`
- Auth: None (public API)
- Used in: `orderbook_analyzer.py`, `imbalance_tracker.py`
- Endpoints:
  - `GET /events?slug={slug}` - Get market by slug
  - `GET /events?active=true&limit=50` - List active markets

**Polymarket Data API:**
- Purpose: User position data
- Base URL: `https://data-api.polymarket.com`
- Auth: None (public API)
- Used in: `auto_redeem.py` (line 172)
- Endpoints:
  - `GET /positions?user={wallet}&sizeThreshold=0.01` - Get user positions

**Coinbase API:**
- Purpose: BTC price fallback when Chainlink fails
- Base URL: `https://api.coinbase.com`
- Auth: None (public API)
- Used in: `trading_bot_smart.py` (fallback)

## Blockchain Integrations

**Chainlink Price Oracle:**
- Purpose: Get authoritative BTC/USD price (same source as Polymarket settlement)
- Contract: `0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c` (Ethereum Mainnet)
- Used in: `chainlink_feed.py`
- RPC Endpoints (free, no API key):
  - `https://eth.llamarpc.com` (primary)
  - `https://rpc.ankr.com/eth` (fallback)
  - `https://ethereum.publicnode.com` (fallback)
  - `https://1rpc.io/eth` (fallback)

**Polygon Network:**
- Purpose: Execute redemption transactions
- Chain ID: 137
- RPC: Configurable via `POLYGON_RPC` env var
- Default: `https://polygon-rpc.com`
- Used in: `auto_redeem.py`, `trading_bot_smart.py`

**Smart Contracts:**
- CTF (Conditional Token Framework): `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045`
  - Used for: Redeeming winning positions
  - Method: `redeemPositions()`
- USDC on Polygon: `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174`
  - Used as: Collateral token for redemptions
- Gnosis Safe (Proxy Wallet): User-configured via `WALLET_ADDRESS`
  - Used for: Executing transactions from proxy wallet

## Data Storage

**Databases:**
- None - No traditional database

**File Storage:**
- Local filesystem only
- `trades_smart.json` - Trade history
- `imbalance_data.json` - Historical order book imbalance
- `~/polybot/bot.log` - Application logs
- `~/activity_log.jsonl` - Real-time activity log (JSONL format)

**Caching:**
- In-memory only via `collections.deque`
- `api_latencies` - Last 10 API latencies
- Order book history - 60 readings

## Google Sheets Integration

**Purpose:** Trade logging and analysis

**Service:**
- Google Sheets API via `gspread`
- Google Drive API (for access)

**Authentication:**
- Service Account: `polybot-sheets@polymarket-bot-logging.iam.gserviceaccount.com`
- Google Cloud Project: `polymarket-bot-logging`
- Credentials: `~/.google_sheets_credentials.json`
- Env var: `GOOGLE_SHEETS_CREDENTIALS_FILE`

**Spreadsheet:**
- ID: `1fxGKxKxj2RAL0hwtqjaOWdmnwqg6RcKseYYP-cCKp74`
- Env var: `GOOGLE_SHEETS_SPREADSHEET_ID`

**Sheets/Tabs:**
| Sheet | Purpose | Update Frequency |
|-------|---------|------------------|
| `Events` | Key trading events | On occurrence |
| `Windows` | Window summaries | At window end |
| `Ticks` | Per-second price data | Batched every 30s |

**Implementation:** `sheets_logger.py`
- Retry logic with exponential backoff (3 attempts)
- Tick buffering to reduce API calls
- Graceful degradation if unavailable

## Notifications

**Telegram Bot:**
- Purpose: Real-time trade notifications
- Config file: `~/.telegram-bot.json`
- Format:
  ```json
  {
    "token": "YOUR_BOT_TOKEN",
    "chat_id": "YOUR_CHAT_ID"
  }
  ```
- Used in: `trading_bot_smart.py`, `auto_redeem.py`

**Notification Types:**
| Event | Description |
|-------|-------------|
| `PROFIT_PAIR` | Successful arbitrage with guaranteed profit |
| `LOSS_AVOID_PAIR` | Completed at loss but risk eliminated |
| `HARD_FLATTEN` | Emergency position close |
| Claimable positions | Winning positions ready to redeem |

## Environment Configuration

**Required env vars:**
```bash
# Trading (required)
PRIVATE_KEY=<ethereum_private_key>
WALLET_ADDRESS=<proxy_wallet_address>

# Google Sheets (optional but recommended)
GOOGLE_SHEETS_SPREADSHEET_ID=<spreadsheet_id>
GOOGLE_SHEETS_CREDENTIALS_FILE=<path_to_credentials.json>

# Telegram (optional)
TELEGRAM_BOT_TOKEN=<bot_token>
TELEGRAM_CHAT_ID=<chat_id>

# Blockchain (optional, has defaults)
POLYGON_RPC=<polygon_rpc_url>
```

**Secrets location:**
- `~/.env` - Main credentials (NOT in project folder)
- `~/.google_sheets_credentials.json` - Google service account
- `~/.telegram-bot.json` - Telegram bot config

## Webhooks & Callbacks

**Incoming:**
- None - Bot polls APIs, no incoming webhooks

**Outgoing:**
- Telegram notifications via HTTP POST
- Google Sheets updates via API

## Google Apps Script Integration

**File:** `PolymarketSummary.gs`

**Purpose:** Automated trade summary and P&L calculation

**Features:**
- Aggregates trades by WindowID
- Fetches market resolution from Polymarket API
- Calculates profit/loss per window
- Updates Summary sheet with deduplication
- Runs on 15-minute trigger

**Sheets Used:**
| Sheet | Purpose |
|-------|---------|
| `Activity Log` | Raw trade data input |
| `Summary` | Aggregated P&L output |

## API Rate Limits & Considerations

**Polymarket APIs:**
- No documented rate limits
- Bot uses 1-second polling interval
- HTTP session with connection pooling

**Google Sheets API:**
- Ticks batched every 30 seconds to avoid quota
- Retry with exponential backoff on failure

**Chainlink RPC:**
- Free public endpoints
- Automatic failover to backup RPCs

---

*Integration audit: 2026-01-19*
