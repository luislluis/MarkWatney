# Coding Conventions

**Analysis Date:** 2026-01-19

## Naming Patterns

**Files:**
- Use `snake_case` for all Python files: `trading_bot_smart.py`, `sheets_logger.py`, `chainlink_feed.py`
- Descriptive names indicating module purpose: `orderbook_analyzer.py`, `auto_redeem.py`

**Functions:**
- Use `snake_case` for all functions: `get_current_slug()`, `verify_position_from_api()`, `check_99c_capture_opportunity()`
- Prefix with action verb: `get_`, `check_`, `verify_`, `place_`, `cancel_`, `run_`, `execute_`
- Private/internal functions use `_` prefix: `_send_pair_outcome_notification()`, `_ensure_initialized()`

**Variables:**
- Use `snake_case` for local variables: `best_ask`, `time_remaining`, `order_id`
- Use `SCREAMING_SNAKE_CASE` for constants: `FAILSAFE_MAX_BUY_PRICE`, `PAIR_DEADLINE_SECONDS`
- Globals prefixed with `_` when private: `_logger`, `_feed`, `_w3`, `_last_log_time`

**Classes:**
- Use `PascalCase`: `SheetsLogger`, `ChainlinkPriceFeed`, `OrderBookAnalyzer`, `TeeLogger`
- Single-purpose classes named for their function

**Types:**
- Use Python typing module with `Optional`, `Dict`, `Any`, `List`:
  ```python
  def log_event(self, event_type: str, window_id: str, **kwargs) -> bool:
  ```
- Type hints primarily on function signatures, not local variables

## Code Style

**Formatting:**
- No explicit formatter configured (no `.prettierrc`, `pyproject.toml` with formatting config)
- Implicit 4-space indentation (Python standard)
- Line length appears to be ~100-120 characters

**Linting:**
- No explicit linter configuration (no `.flake8`, `.pylintrc`)
- Code follows basic PEP 8 conventions

**Docstrings:**
- Triple-quoted docstrings at module and function level
- Module-level docstrings include setup instructions:
  ```python
  """
  Google Sheets Logger for Polymarket Trading Bot
  ================================================
  Logs trading events to Google Sheets for tracking and analysis.

  Setup:
  1. Go to Google Cloud Console (console.cloud.google.com)
  ...
  """
  ```
- Function docstrings use `Args:` and `Returns:` sections:
  ```python
  def calculate_hedge_price(fill_price, seconds_since_fill):
      """Calculate max acceptable hedge price based on escalating tolerance.

      Returns:
          tuple: (max_hedge_price, tolerance_cents)
          - max_hedge_price: Max price we're willing to pay for hedge (decimal, e.g. 0.42)
          - tolerance_cents: Current tolerance in cents (e.g. 2 for 2c tolerance)
      """
  ```

## Import Organization

**Order:**
1. Standard library imports (os, sys, time, json, math)
2. Third-party imports (requests, dotenv)
3. Local module imports (sheets_logger, chainlink_feed)

**Pattern:**
```python
#!/usr/bin/env python3
"""Module docstring..."""

import os
import sys
import time
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from collections import deque

from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor

try:
    from sheets_logger import (sheets_log_event, sheets_log_window, init_sheets_logger,
                               buffer_tick, maybe_flush_ticks, flush_ticks)
    SHEETS_LOGGER_AVAILABLE = True
except ImportError:
    SHEETS_LOGGER_AVAILABLE = False
    # Provide stub implementations
```

**Path Aliases:**
- No path aliases used; all imports are direct module references
- Local modules imported from project root

## Error Handling

**Patterns:**
- Use try/except blocks with broad Exception catches for external APIs:
  ```python
  try:
      resp = http_session.get(url, timeout=3)
      data = resp.json()
      return data[0] if data else None
  except:
      return None
  ```
- Specific error handling for critical operations with retries:
  ```python
  for attempt in range(3):
      try:
          self.events_sheet.append_row(row, value_input_option='USER_ENTERED')
          return True
      except Exception as e:
          print(f"[SHEETS] Failed to log event (attempt {attempt+1}/3): {e}")
          if attempt < 2:
              self._initialized = False  # Force reconnection on retry
              time.sleep(2 ** attempt)  # Exponential backoff
  ```
- Graceful degradation when optional modules unavailable:
  ```python
  try:
      from chainlink_feed import ChainlinkPriceFeed
      CHAINLINK_AVAILABLE = True
  except ImportError:
      CHAINLINK_AVAILABLE = False
  ```

**Error Logging:**
- Print statements with prefixes: `[SHEETS]`, `[Chainlink]`, `[REDEEM]`
- Timestamp helper for consistent logging: `ts()` returns `HH:MM:SS` format
- Full traceback printing in main loop error handler:
  ```python
  except Exception as e:
      print(f"[{ts()}] Error: {e}")
      import traceback
      traceback.print_exc()
  ```

## Logging

**Framework:** Console logging via print statements with structured prefixes

**Patterns:**
- Timestamp prefix for all operational logs: `[{ts()}]`
- Module-specific prefixes: `[SHEETS]`, `[Chainlink]`, `[REDEEM]`
- Status logging format: `[HH:MM:SS] STATUS | T-XXXs | data | reason`
- Visual separators for important events:
  ```python
  print("=" * 60)
  print(f"NEW WINDOW: {slug}")
  print("=" * 60)
  ```
- Emoji indicators for trade outcomes:
  - Success: `print("💰 BOTH FILLED - PAIRED!")`
  - Warning: `print("⚠️ ONE-LEG FILL!")`
  - Error: `print("🚨 BAIL MODE TRIGGERED")`

## Comments

**When to Comment:**
- Section headers with `# ========` separator lines:
  ```python
  # ============================================================================
  # CONSTANTS
  # ============================================================================
  ```
- Inline comments for constants explaining purpose:
  ```python
  FAILSAFE_MAX_BUY_PRICE = 0.85     # NEVER buy above 85c
  ```
- Bug fix annotations:
  ```python
  # Bug Fix #1, Bug Fix #2, Bug Fix #3, Bug Fix #4
  ```

**JSDoc/TSDoc:**
- Not applicable (Python codebase)

## Function Design

**Size:**
- Functions range from 5-150 lines
- Long functions (like `main()`) are acceptable for main entry points
- Complex logic split into helper functions: `calculate_hedge_price()`, `bail_vs_hedge_decision()`

**Parameters:**
- Positional required parameters first
- Optional parameters with defaults:
  ```python
  def place_limit_order(token_id, price, size, side="BUY", bypass_price_failsafe=False):
  ```
- Use `**kwargs` for flexible data passing:
  ```python
  def log_event(self, event_type: str, window_id: str, **kwargs) -> bool:
  ```

**Return Values:**
- Return tuples for multi-value returns: `return (success, order_id)`
- Return dict for complex results:
  ```python
  return {
      'up_imbalance': up_imbalance,
      'down_imbalance': down_imbalance,
      'signal': signal,
      'trend': trend,
      'strength': strength
  }
  ```
- Return `None` for failure cases, not exceptions

## Module Design

**Exports:**
- No `__all__` declarations; all public functions available
- Global singleton instances with getter functions:
  ```python
  _logger: Optional[SheetsLogger] = None

  def init_sheets_logger() -> SheetsLogger:
      global _logger
      if _logger is None:
          _logger = SheetsLogger()
      return _logger

  def get_sheets_logger() -> Optional[SheetsLogger]:
      return _logger
  ```

**Barrel Files:**
- Not used; direct module imports

**Module Structure Pattern:**
```python
"""Docstring with description and setup instructions"""

# Standard imports
import os
import time

# Third-party imports
import requests

# Constants at module level
SOME_CONSTANT = "value"

# Class definitions
class ModuleClass:
    pass

# Global instance
_instance = None

# Public API functions
def init_module():
    pass

def public_function():
    pass

# Test function at bottom
if __name__ == "__main__":
    test_function()
```

## Global State Management

**Pattern:**
- Use module-level globals for runtime state
- Reset functions for per-window state:
  ```python
  window_state = None

  def reset_window_state(slug):
      return {
          "window_id": slug,
          "filled_up_shares": 0,
          ...
      }
  ```
- Session counters as dictionaries:
  ```python
  session_counters = {
      "profit_pairs": 0,
      "loss_avoid_pairs": 0,
      "hard_flattens": 0,
  }
  ```

## Configuration Pattern

**Constants at top of file:**
```python
# ===========================================
# FAILSAFE PRICE LIMITS - NEVER VIOLATE THESE
# ===========================================
FAILSAFE_MAX_BUY_PRICE = 0.85     # NEVER buy above 85c
FAILSAFE_MIN_BUY_PRICE = 0.05     # NEVER buy below 5c (garbage)
```

**Environment variables:**
```python
from dotenv import load_dotenv
load_dotenv(os.path.expanduser("~/.env"))
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
```

**Version tracking:**
```python
BOT_VERSION = {
    "version": "v1.5",
    "codename": "Houdini",
    "date": "2026-01-17",
    "changes": "Early bail: Compare hedge vs bail after 30s, detect 10c reversals"
}
```

---

*Convention analysis: 2026-01-19*
