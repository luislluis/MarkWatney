# Codebase Structure

**Analysis Date:** 2026-01-19

## Directory Layout

```
MarkWatney/
├── trading_bot_smart.py      # Main trading bot (2500 lines)
├── sheets_logger.py          # Google Sheets logging module
├── chainlink_feed.py         # Chainlink BTC price oracle
├── orderbook_analyzer.py     # Order book imbalance analysis
├── auto_redeem.py            # Auto-claim winning positions
├── imbalance_tracker.py      # Research: imbalance correlation
├── PolymarketSummary.gs      # Google Apps Script for spreadsheet
├── CLAUDE.md                 # Project documentation
├── BOT_REGISTRY.md           # Version history
├── README.md                 # Basic readme
├── imbalance_summary.txt     # Generated: imbalance analysis results
├── .gitignore                # Git ignore rules
└── .planning/
    └── codebase/             # Architecture documentation
```

## Directory Purposes

**Root Directory:**
- Purpose: All source code lives in project root (flat structure)
- Contains: Python modules, documentation, data files
- Key files: `trading_bot_smart.py` (main), `CLAUDE.md` (docs)

**.planning/codebase/:**
- Purpose: Architecture documentation for AI-assisted development
- Contains: Markdown analysis documents (ARCHITECTURE.md, STRUCTURE.md, etc.)
- Generated: By codebase mapping commands

## Key File Locations

**Entry Points:**
- `trading_bot_smart.py`: Main trading bot - run directly
- `auto_redeem.py`: Position redemption - run standalone or imported
- `imbalance_tracker.py`: Research tool - run standalone
- `sheets_logger.py`: Can run standalone to test Sheets connection

**Configuration:**
- `~/.env`: Private key, wallet address (NOT in repo)
- `~/.telegram-bot.json`: Telegram bot credentials
- `~/.google_sheets_credentials.json`: Google service account

**Core Logic:**
- `trading_bot_smart.py`: All trading strategy, state machine, order management
- `chainlink_feed.py`: BTC price from on-chain oracle
- `orderbook_analyzer.py`: Imbalance signal generation
- `sheets_logger.py`: Event/tick logging to Google Sheets

**Testing:**
- No formal test files - each module has `if __name__ == "__main__"` test blocks
- `sheets_logger.py:test_logger()` - Tests Sheets connectivity
- `chainlink_feed.py:__main__` - Tests price feed
- `orderbook_analyzer.py:__main__` - Tests live analysis
- `auto_redeem.py --test` - Tests position detection

**Data Files (Generated at Runtime):**
- `~/polybot/bot.log`: Main bot log file
- `~/activity_log.jsonl`: JSONL activity log
- `trades_smart.json`: Trade history (created by bot)
- `imbalance_data.json`: Research data from imbalance_tracker
- `imbalance_summary.txt`: Correlation analysis results

## Naming Conventions

**Files:**
- Python modules: `snake_case.py` (e.g., `trading_bot_smart.py`)
- Documentation: `UPPERCASE.md` (e.g., `CLAUDE.md`, `BOT_REGISTRY.md`)
- Config files: Dotfiles in home directory (`~/.env`, `~/.telegram-bot.json`)

**Functions:**
- `snake_case` for all functions (e.g., `get_current_slug()`, `place_limit_order()`)
- Private/internal functions prefixed with underscore (e.g., `_send_pair_outcome_notification()`)

**Classes:**
- `PascalCase` (e.g., `TeeLogger`, `SheetsLogger`, `ChainlinkPriceFeed`, `OrderBookAnalyzer`)

**Constants:**
- `SCREAMING_SNAKE_CASE` at module level
- Grouped by category with section comments
- Examples: `FAILSAFE_MAX_BUY_PRICE`, `STATE_QUOTING`, `CAPTURE_99C_ENABLED`

**State/Variables:**
- `snake_case` for local variables
- Global state: `window_state`, `clob_client`, `session_counters`
- Window state keys: lowercase with underscores (e.g., `filled_up_shares`, `capture_99c_used`)

## Import Organization

**Order (observed in trading_bot_smart.py):**
1. Standard library (`os`, `sys`, `signal`, `time`, `json`, `math`, `requests`)
2. Third-party (`datetime`, `zoneinfo`, `collections.deque`)
3. Environment loading (`dotenv`)
4. Concurrency (`concurrent.futures`)
5. Local modules (conditional imports with try/except)

**Conditional Imports:**
```python
try:
    from sheets_logger import sheets_log_event, ...
    SHEETS_LOGGER_AVAILABLE = True
except ImportError:
    SHEETS_LOGGER_AVAILABLE = False
    sheets_log_event = lambda *args, **kwargs: False
```

**Path Aliases:**
- None used - all imports are direct module names

## Where to Add New Code

**New Trading Strategy Feature:**
- Primary code: Add to `trading_bot_smart.py`
- Add constants near top (lines 140-290)
- Add helper functions before `main()`
- Integrate into state machine in `main()` loop

**New Analysis/Signal Module:**
- Create new module: `{name}_analyzer.py` in root
- Follow pattern from `orderbook_analyzer.py`:
  - Class with `analyze()` method
  - Standalone `if __name__ == "__main__"` test
  - Conditional import in `trading_bot_smart.py`

**New External Integration:**
- Create new module: `{service}_feed.py` or `{service}_client.py`
- Follow pattern from `chainlink_feed.py`:
  - Class with lazy initialization
  - Global instance getter function
  - Fallback behavior on connection failure

**New Notification Channel:**
- Add to `trading_bot_smart.py` near Telegram functions (lines 433-516)
- Create `notify_{event}()` function
- Add config loading in init section

**Utilities/Helpers:**
- Add to the module that uses them
- No separate utils module exists
- Consider creating `utils.py` if helpers become shared

## Special Directories

**.planning/codebase/:**
- Purpose: AI-generated architecture documentation
- Generated: Yes (by codebase mapping)
- Committed: No (typically in .gitignore)

**~/polybot/ (runtime, not in repo):**
- Purpose: Bot logs and runtime data
- Contains: `bot.log`
- Generated: Yes (by running bot)
- Committed: No (external to repo)

**Home directory configs (~/):**
- Purpose: Credentials and secrets
- Contains: `.env`, `.telegram-bot.json`, `.google_sheets_credentials.json`
- Generated: Manual setup
- Committed: Never (secrets)

## File Size Reference

| File | Lines | Purpose |
|------|-------|---------|
| `trading_bot_smart.py` | ~2500 | Main bot - consider splitting if grows |
| `auto_redeem.py` | ~570 | Position redemption |
| `sheets_logger.py` | ~530 | Sheets logging |
| `imbalance_tracker.py` | ~365 | Research tool |
| `orderbook_analyzer.py` | ~255 | Signal analysis |
| `chainlink_feed.py` | ~180 | Price feed |

---

*Structure analysis: 2026-01-19*
