# Testing Patterns

**Analysis Date:** 2026-01-19

## Test Framework

**Runner:**
- No formal test framework configured (no pytest, unittest setup)
- No `pytest.ini`, `setup.cfg`, or `pyproject.toml` test configuration

**Assertion Library:**
- Not applicable (no formal testing framework)

**Run Commands:**
```bash
# No standardized test commands
# Individual module tests via __main__ blocks
python3 sheets_logger.py       # Run sheets logger test
python3 chainlink_feed.py      # Run chainlink feed test
python3 orderbook_analyzer.py  # Run order book analyzer test
python3 auto_redeem.py --test  # Run auto-redeem detection test (no transactions)
```

## Test File Organization

**Location:**
- No separate test files
- Tests embedded in module `__main__` blocks

**Naming:**
- Test functions named `test_*()`: `test_logger()`, `test_redeem_detection()`

**Structure:**
```
project/
├── trading_bot_smart.py     # Main bot (no tests)
├── sheets_logger.py         # Has test_logger() in __main__
├── chainlink_feed.py        # Has test in __main__
├── orderbook_analyzer.py    # Has test in __main__
├── auto_redeem.py           # Has test_redeem_detection() + --test flag
└── imbalance_tracker.py     # Standalone tracker script
```

## Test Structure

**Suite Organization:**
- Tests are standalone functions invoked via `if __name__ == "__main__":`
- No test fixtures or setup/teardown patterns

**Pattern from `sheets_logger.py`:**
```python
def test_logger():
    """Test the Google Sheets logger."""
    print("=" * 50)
    print("GOOGLE SHEETS LOGGER TEST")
    print("=" * 50)

    print(f"\nConfiguration:")
    print(f"  gspread installed: {GSPREAD_AVAILABLE}")
    print(f"  Credentials file: {CREDENTIALS_FILE}")
    print(f"  Credentials exists: {os.path.exists(CREDENTIALS_FILE)}")
    print(f"  Spreadsheet ID: {SPREADSHEET_ID[:20]}..." if SPREADSHEET_ID else "  Spreadsheet ID: NOT SET")

    logger = init_sheets_logger()

    if not logger.enabled:
        print("\n[SHEETS] Logger is DISABLED. Check configuration above.")
        return False

    print("\nAttempting to connect...")
    if not logger._ensure_initialized():
        print("[SHEETS] Failed to initialize connection.")
        return False

    print("[SHEETS] Connected successfully!")

    # Test event logging
    print("\nLogging test event...")
    success = sheets_log_event(
        "TEST_EVENT",
        "test-window-123",
        side="UP",
        shares=5.0,
        price=0.45,
        pnl=0.05,
        confidence=95,
        note="This is a test event"
    )

    if success:
        print("[SHEETS] Test event logged successfully!")
    else:
        print("[SHEETS] Failed to log test event.")

    return True


if __name__ == "__main__":
    test_logger()
```

**Pattern from `chainlink_feed.py`:**
```python
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
```

**Pattern from `auto_redeem.py`:**
```python
def test_redeem_detection():
    """Test: Find claimable positions without actually redeeming"""
    print("=" * 60)
    print("REDEEM TEST - Detection Only (No Transactions)")
    print("=" * 60)

    eoa_wallet = get_wallet_address()
    proxy_wallet = PROXY_WALLET

    print(f"EOA (signer): {eoa_wallet}")
    print(f"Proxy (positions): {proxy_wallet}")

    # ... validation and testing logic ...

    claimable = check_claimable_positions(include_already_processed=True)

    if not claimable:
        print("\nNo claimable positions found.")
        return

    print(f"\nFOUND {len(claimable)} CLAIMABLE POSITION(S):")
    # ... display results ...


if __name__ == "__main__":
    if "--test" in sys.argv:
        test_redeem_detection()
    else:
        run_loop(interval=60)
```

## Mocking

**Framework:** None

**Patterns:**
- No mocking infrastructure
- Tests run against live APIs (Google Sheets, Chainlink, Polymarket)
- Graceful degradation pattern used instead:
  ```python
  try:
      import gspread
      GSPREAD_AVAILABLE = True
  except ImportError:
      GSPREAD_AVAILABLE = False
  ```

**What to Mock (if adding tests):**
- External API calls: `requests.get()`, `requests.post()`
- CLOB client interactions: `clob_client.create_and_post_order()`
- Web3 contract calls: `contract.functions.latestRoundData().call()`
- Time functions for deterministic testing: `time.time()`, `time.sleep()`

**What NOT to Mock:**
- Core business logic (price calculations, imbalance detection)
- State management functions
- Local utility functions

## Fixtures and Factories

**Test Data:**
- Hardcoded test values in test functions:
  ```python
  test_window = {
      "window_id": "test-window-123",
      "filled_up_shares": 5,
      "filled_down_shares": 5,
      "avg_up_price_paid": 0.40,
      "avg_down_price_paid": 0.58,
      "realized_pnl_usd": 0.10,
      "capture_99c_used": True,
      "capture_99c_side": "DOWN",
      "capture_99c_filled_up": 0,
      "capture_99c_filled_down": 5,
  }
  ```

- Sample order book data:
  ```python
  sample_up_bids = [{'price': '0.45', 'size': '500'}, {'price': '0.44', 'size': '300'}]
  sample_up_asks = [{'price': '0.46', 'size': '100'}, {'price': '0.47', 'size': '50'}]
  sample_down_bids = [{'price': '0.54', 'size': '200'}]
  sample_down_asks = [{'price': '0.55', 'size': '400'}]
  ```

**Location:**
- Inline within test functions (no separate fixtures directory)

## Coverage

**Requirements:** None enforced

**View Coverage:**
```bash
# No coverage tooling configured
```

## Test Types

**Unit Tests:**
- Not present in traditional form
- Manual validation via `__main__` test functions

**Integration Tests:**
- `test_logger()` - Tests Google Sheets API integration
- `chainlink_feed.py __main__` - Tests Ethereum RPC connection
- `test_redeem_detection()` - Tests Polymarket API integration

**E2E Tests:**
- Not implemented
- Main bot (`trading_bot_smart.py`) is tested by running in production

**Manual Testing Pattern:**
- Run module directly to verify functionality
- Check external systems (Google Sheets, Polygonscan) for results
- Review bot logs for correct behavior

## Common Patterns

**Async Testing:**
- Not applicable (codebase is synchronous)

**Error Testing:**
- Tests print errors but don't assert on them:
  ```python
  if not feed.is_connected():
      print("ERROR: Not connected to Ethereum RPC")
      exit(1)
  ```

**Retry Testing:**
- Built into production code with exponential backoff:
  ```python
  for attempt in range(3):
      try:
          # operation
          return True
      except Exception as e:
          print(f"Failed (attempt {attempt+1}/3): {e}")
          if attempt < 2:
              time.sleep(2 ** attempt)
  ```

## Recommendations for Adding Tests

**If implementing formal testing:**

1. **Add pytest:**
   ```bash
   pip install pytest pytest-mock pytest-cov
   ```

2. **Create test directory:**
   ```
   tests/
   ├── __init__.py
   ├── conftest.py              # Shared fixtures
   ├── test_trading_bot.py      # Main bot tests
   ├── test_sheets_logger.py    # Sheets logger tests
   ├── test_chainlink_feed.py   # Chainlink tests
   └── test_orderbook_analyzer.py
   ```

3. **Example test structure:**
   ```python
   # tests/test_orderbook_analyzer.py
   import pytest
   from orderbook_analyzer import OrderBookAnalyzer

   @pytest.fixture
   def analyzer():
       return OrderBookAnalyzer(history_size=10, imbalance_threshold=0.3)

   @pytest.fixture
   def sample_books():
       return {
           'up_bids': [{'price': '0.45', 'size': '500'}],
           'up_asks': [{'price': '0.46', 'size': '100'}],
           'down_bids': [{'price': '0.54', 'size': '200'}],
           'down_asks': [{'price': '0.55', 'size': '400'}],
       }

   def test_calculate_imbalance_bullish(analyzer, sample_books):
       result = analyzer.analyze(
           sample_books['up_bids'],
           sample_books['up_asks'],
           sample_books['down_bids'],
           sample_books['down_asks']
       )
       assert result['up_imbalance'] > 0
       assert 'signal' in result
   ```

4. **Mock external dependencies:**
   ```python
   # tests/test_sheets_logger.py
   from unittest.mock import Mock, patch

   @patch('sheets_logger.gspread')
   def test_log_event_success(mock_gspread):
       mock_sheet = Mock()
       mock_gspread.authorize.return_value.open_by_key.return_value.worksheet.return_value = mock_sheet

       logger = SheetsLogger()
       result = logger.log_event("TEST", "window-1", side="UP")

       assert result == True
       mock_sheet.append_row.assert_called_once()
   ```

---

*Testing analysis: 2026-01-19*
