"""
Google Sheets Logger for Polymarket Trading Bot
================================================
Logs trading events to Google Sheets for tracking and analysis.

Setup:
1. Go to Google Cloud Console (console.cloud.google.com)
2. Create project and enable Google Sheets API
3. Create Service Account and download JSON key
4. Share your Google Sheet with the service account email
5. Set environment variables in ~/.env:
   GOOGLE_SHEETS_CREDENTIALS_FILE=/path/to/credentials.json
   GOOGLE_SHEETS_SPREADSHEET_ID=your_spreadsheet_id
"""

import os
import json
import time
from datetime import datetime
from typing import Optional, Dict, Any, List
from zoneinfo import ZoneInfo

# Timezone for logging
PST = ZoneInfo("America/Los_Angeles")

# Try to import gspread
try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSPREAD_AVAILABLE = True
except ImportError:
    GSPREAD_AVAILABLE = False

# Configuration from environment
CREDENTIALS_FILE = os.getenv("GOOGLE_SHEETS_CREDENTIALS_FILE", os.path.expanduser("~/.google_sheets_credentials.json"))
SPREADSHEET_ID = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", "")

# Google Sheets API scopes
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

# Headers for Events sheet
EVENTS_HEADERS = [
    "Timestamp",
    "Event",
    "Window ID",
    "Side",
    "Shares",
    "Price",
    "PnL",
    "Details"
]

# Headers for Windows sheet
WINDOWS_HEADERS = [
    "Window ID",
    "Start Time",
    "End Time",
    "UP Shares",
    "UP Avg Price",
    "DOWN Shares",
    "DOWN Avg Price",
    "Outcome",
    "PnL",
    "99c Capture",
    "Notes"
]

# Headers for Ticks sheet (per-second data)
TICKS_HEADERS = [
    "Timestamp",
    "Window ID",
    "TTL",
    "Status",
    "UP Ask",
    "DN Ask",
    "UP Pos",
    "DN Pos",
    "BTC",
    "UP Imb",
    "DN Imb",
    "Reason"
]

# Tick buffer configuration
TICK_FLUSH_INTERVAL = 30  # Flush every 30 seconds


class SheetsLogger:
    """Google Sheets logger for trading bot events."""

    def __init__(self):
        self.client = None
        self.events_sheet = None
        self.windows_sheet = None
        self.ticks_sheet = None
        self.enabled = False
        self._initialized = False

        # Tick buffer for batched uploads
        self._tick_buffer: List[Dict] = []
        self._last_flush_time = time.time()

        if not GSPREAD_AVAILABLE:
            print("[SHEETS] Disabled - gspread not installed. Run: pip3 install gspread google-auth")
            return

        if not SPREADSHEET_ID:
            print("[SHEETS] Disabled - GOOGLE_SHEETS_SPREADSHEET_ID not set")
            return

        if not os.path.exists(CREDENTIALS_FILE):
            print(f"[SHEETS] Disabled - credentials file not found: {CREDENTIALS_FILE}")
            return

        self.enabled = True

    def _ensure_initialized(self) -> bool:
        """Initialize Google Sheets connection if not already done."""
        if self._initialized:
            return self.events_sheet is not None

        if not self.enabled:
            return False

        try:
            creds = Credentials.from_service_account_file(
                CREDENTIALS_FILE,
                scopes=SCOPES
            )
            self.client = gspread.authorize(creds)
            spreadsheet = self.client.open_by_key(SPREADSHEET_ID)

            # Get or create Events sheet
            try:
                self.events_sheet = spreadsheet.worksheet("Events")
            except gspread.exceptions.WorksheetNotFound:
                self.events_sheet = spreadsheet.add_worksheet(
                    title="Events",
                    rows=5000,
                    cols=len(EVENTS_HEADERS)
                )
                self.events_sheet.update('A1', [EVENTS_HEADERS])
                self.events_sheet.format('A1:H1', {'textFormat': {'bold': True}})

            # Get or create Windows sheet
            try:
                self.windows_sheet = spreadsheet.worksheet("Windows")
            except gspread.exceptions.WorksheetNotFound:
                self.windows_sheet = spreadsheet.add_worksheet(
                    title="Windows",
                    rows=2000,
                    cols=len(WINDOWS_HEADERS)
                )
                self.windows_sheet.update('A1', [WINDOWS_HEADERS])
                self.windows_sheet.format('A1:K1', {'textFormat': {'bold': True}})

            # Get or create Ticks sheet (per-second data)
            try:
                self.ticks_sheet = spreadsheet.worksheet("Ticks")
            except gspread.exceptions.WorksheetNotFound:
                self.ticks_sheet = spreadsheet.add_worksheet(
                    title="Ticks",
                    rows=50000,  # Large - lots of per-second data
                    cols=len(TICKS_HEADERS)
                )
                self.ticks_sheet.update('A1', [TICKS_HEADERS])
                self.ticks_sheet.format('A1:L1', {'textFormat': {'bold': True}})

            self._initialized = True
            print(f"[SHEETS] Connected to Google Sheets")
            return True

        except Exception as e:
            print(f"[SHEETS] Failed to initialize: {e}")
            self.enabled = False
            return False

    def log_event(self, event_type: str, window_id: str, **kwargs) -> bool:
        """
        Log an event to the Events sheet.

        Args:
            event_type: Type of event (WINDOW_START, ARB_ORDER, etc.)
            window_id: Window identifier (slug)
            **kwargs: Additional event data
        """
        if not self._ensure_initialized():
            return False

        # Extract common fields
        side = kwargs.get('side', '')
        shares = kwargs.get('shares', '')
        price = kwargs.get('price', '')
        pnl = kwargs.get('pnl', '')

        # Build details string from remaining kwargs
        detail_fields = {k: v for k, v in kwargs.items()
                        if k not in ('side', 'shares', 'price', 'pnl')}
        details = json.dumps(detail_fields) if detail_fields else ''

        # Format price/pnl nicely
        if isinstance(price, float):
            price = f"{price:.2f}"
        if isinstance(pnl, float):
            pnl = f"${pnl:.2f}"
        if isinstance(shares, float):
            shares = f"{shares:.1f}"

        row = [
            datetime.now(PST).strftime("%Y-%m-%d %H:%M:%S"),
            event_type,
            window_id,
            side,
            shares,
            price,
            pnl,
            details
        ]

        # Retry up to 3 times with exponential backoff
        for attempt in range(3):
            try:
                if attempt > 0 and not self._ensure_initialized():
                    return False
                self.events_sheet.append_row(row, value_input_option='USER_ENTERED')
                return True
            except Exception as e:
                print(f"[SHEETS] Failed to log event (attempt {attempt+1}/3): {e}")
                if attempt < 2:
                    self._initialized = False  # Force reconnection on retry
                    time.sleep(2 ** attempt)  # Backoff: 1s, 2s

        print(f"[SHEETS] Giving up on event: {event_type}")
        return False

    def log_window(self, window_state: Dict[str, Any]) -> bool:
        """
        Log a window summary to the Windows sheet.

        Args:
            window_state: The window_state dictionary from the bot
        """
        if not self._ensure_initialized():
            return False

        if not window_state:
            return False

        window_id = window_state.get('window_id', '')
        up_shares = window_state.get('filled_up_shares', 0)
        down_shares = window_state.get('filled_down_shares', 0)
        avg_up = window_state.get('avg_up_price_paid', 0)
        avg_down = window_state.get('avg_down_price_paid', 0)
        pnl = window_state.get('realized_pnl_usd', 0)

        # Determine outcome
        if up_shares > 0 and down_shares > 0:
            if up_shares == down_shares:
                outcome = "PAIRED"
            else:
                outcome = "PARTIAL"
        elif up_shares > 0 or down_shares > 0:
            outcome = "ONE_LEG"
        else:
            outcome = "NO_TRADE"

        # Check for 99c capture
        capture_info = ""
        if window_state.get('capture_99c_used'):
            side = window_state.get('capture_99c_side', '?')
            filled_up = window_state.get('capture_99c_filled_up', 0)
            filled_down = window_state.get('capture_99c_filled_down', 0)
            filled = filled_up + filled_down
            capture_info = f"{side}:{filled:.0f} shares"

        row = [
            window_id,
            datetime.now(PST).strftime("%Y-%m-%d %H:%M:%S"),  # Start (approx)
            datetime.now(PST).strftime("%Y-%m-%d %H:%M:%S"),  # End
            up_shares,
            f"{avg_up:.2f}" if avg_up else "",
            down_shares,
            f"{avg_down:.2f}" if avg_down else "",
            outcome,
            f"${pnl:.2f}" if pnl else "",
            capture_info,
            ""  # Notes
        ]

        # Retry up to 3 times with exponential backoff
        for attempt in range(3):
            try:
                if attempt > 0 and not self._ensure_initialized():
                    return False
                self.windows_sheet.append_row(row, value_input_option='USER_ENTERED')
                return True
            except Exception as e:
                print(f"[SHEETS] Failed to log window (attempt {attempt+1}/3): {e}")
                if attempt < 2:
                    self._initialized = False  # Force reconnection on retry
                    time.sleep(2 ** attempt)  # Backoff: 1s, 2s

        print(f"[SHEETS] Giving up on window: {window_id}")
        return False

    def buffer_tick(self, window_id: str, ttc: float, status: str,
                    ask_up: float, ask_down: float, up_shares: float, down_shares: float,
                    btc_price: float = None, up_imb: float = None, down_imb: float = None,
                    reason: str = "") -> None:
        """
        Buffer a tick for batch upload to Google Sheets.
        Called every second from log_state().
        """
        self._tick_buffer.append({
            "timestamp": datetime.now(PST).strftime("%Y-%m-%d %H:%M:%S"),
            "window_id": window_id,
            "ttc": ttc,
            "status": status,
            "ask_up": ask_up,
            "ask_down": ask_down,
            "up_shares": up_shares,
            "down_shares": down_shares,
            "btc_price": btc_price,
            "up_imb": up_imb,
            "down_imb": down_imb,
            "reason": reason
        })

    def flush_ticks(self) -> bool:
        """Flush buffered ticks to Google Sheets."""
        if not self._tick_buffer:
            return True

        if not self._ensure_initialized():
            return False

        # Convert buffer to rows (do this once, outside retry loop)
        rows = []
        for t in self._tick_buffer:
            rows.append([
                t["timestamp"],
                t["window_id"],
                f"{t['ttc']:.0f}",
                t["status"],
                f"{t['ask_up']:.2f}",
                f"{t['ask_down']:.2f}",
                f"{t['up_shares']:.0f}",
                f"{t['down_shares']:.0f}",
                f"{t['btc_price']:,.0f}" if t["btc_price"] else "",
                f"{t['up_imb']:.2f}" if t["up_imb"] is not None else "",
                f"{t['down_imb']:.2f}" if t["down_imb"] is not None else "",
                t["reason"]
            ])

        # Retry up to 3 times with exponential backoff
        for attempt in range(3):
            try:
                if attempt > 0 and not self._ensure_initialized():
                    return False
                self.ticks_sheet.append_rows(rows, value_input_option='USER_ENTERED')

                count = len(self._tick_buffer)
                self._tick_buffer = []
                self._last_flush_time = time.time()
                print(f"[SHEETS] Flushed {count} ticks")
                return True
            except Exception as e:
                print(f"[SHEETS] Failed to flush ticks (attempt {attempt+1}/3): {e}")
                if attempt < 2:
                    self._initialized = False  # Force reconnection on retry
                    time.sleep(2 ** attempt)  # Backoff: 1s, 2s

        print(f"[SHEETS] Giving up on {len(rows)} ticks")
        return False

    def maybe_flush_ticks(self) -> bool:
        """Flush ticks if enough time has passed since last flush."""
        if time.time() - self._last_flush_time >= TICK_FLUSH_INTERVAL:
            return self.flush_ticks()
        return True


# Global instance
_logger: Optional[SheetsLogger] = None


def init_sheets_logger() -> SheetsLogger:
    """Initialize and return the global sheets logger."""
    global _logger
    if _logger is None:
        _logger = SheetsLogger()
    return _logger


def get_sheets_logger() -> Optional[SheetsLogger]:
    """Get the global sheets logger (may be None if not initialized)."""
    return _logger


def sheets_log_event(event_type: str, window_id: str, **kwargs) -> bool:
    """
    Convenience function to log an event.
    Returns False silently if logger not enabled (graceful degradation).
    """
    if _logger is None or not _logger.enabled:
        return False
    return _logger.log_event(event_type, window_id, **kwargs)


def sheets_log_window(window_state: Dict[str, Any]) -> bool:
    """
    Convenience function to log a window summary.
    Returns False silently if logger not enabled (graceful degradation).
    """
    if _logger is None or not _logger.enabled:
        return False
    return _logger.log_window(window_state)


def buffer_tick(window_id: str, ttc: float, status: str,
                ask_up: float, ask_down: float, up_shares: float, down_shares: float,
                btc_price: float = None, up_imb: float = None, down_imb: float = None,
                reason: str = "") -> None:
    """
    Buffer a per-second tick for batch upload.
    Called from log_state() every second.
    """
    if _logger is None or not _logger.enabled:
        return
    _logger.buffer_tick(window_id, ttc, status, ask_up, ask_down, up_shares, down_shares,
                        btc_price, up_imb, down_imb, reason)


def maybe_flush_ticks() -> bool:
    """Flush ticks if enough time has passed (called every second)."""
    if _logger is None or not _logger.enabled:
        return False
    return _logger.maybe_flush_ticks()


def flush_ticks() -> bool:
    """Force flush all buffered ticks (called at window end)."""
    if _logger is None or not _logger.enabled:
        return False
    return _logger.flush_ticks()


# ========== Test Function ==========

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
        print("\nTo enable:")
        print("  1. Install gspread: pip3 install gspread google-auth")
        print("  2. Create credentials file at ~/.google_sheets_credentials.json")
        print("  3. Set GOOGLE_SHEETS_SPREADSHEET_ID in ~/.env")
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

    # Test window logging
    print("\nLogging test window...")
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
    success = sheets_log_window(test_window)

    if success:
        print("[SHEETS] Test window logged successfully!")
    else:
        print("[SHEETS] Failed to log test window.")

    print("\n" + "=" * 50)
    print("Check your Google Sheet to verify the test entries!")
    print("=" * 50)

    return True


if __name__ == "__main__":
    test_logger()
