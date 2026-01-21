"""
Google Sheets Dashboard Logger for Performance Tracker
=======================================================
Logs window results to a dedicated performance dashboard spreadsheet.

Setup:
1. Same credentials as sheets_logger.py (service account JSON)
2. Optional: Set PERF_TRACKER_SPREADSHEET_ID to use existing spreadsheet
3. Optional: Set SHARE_WITH_EMAIL to automatically share new spreadsheets

Environment variables:
  GOOGLE_SHEETS_CREDENTIALS_FILE - Path to service account JSON (default: ~/.google_sheets_credentials.json)
  PERF_TRACKER_SPREADSHEET_ID - Spreadsheet ID (if not set, creates new)
  SHARE_WITH_EMAIL - Email to share new spreadsheet with
"""

import os
import time
from datetime import datetime
from typing import Optional, Dict, Any
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
CREDENTIALS_FILE = os.getenv(
    "GOOGLE_SHEETS_CREDENTIALS_FILE",
    os.path.expanduser("~/.google_sheets_credentials.json")
)
SPREADSHEET_ID = os.getenv("PERF_TRACKER_SPREADSHEET_ID", "")
SHARE_WITH_EMAIL = os.getenv("SHARE_WITH_EMAIL", "")

# Google Sheets API scopes
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

# ========== Color Constants (0-1 RGB scale for Google Sheets API) ==========
GREEN_BG = {"red": 0.85, "green": 0.92, "blue": 0.83}   # Light green for wins
RED_BG = {"red": 0.96, "green": 0.80, "blue": 0.80}     # Light red for losses
GRAY_BG = {"red": 0.95, "green": 0.95, "blue": 0.95}    # Light gray for no trade

# ========== Emoji Constants ==========
EMOJI_WIN = "\u2713"    # Check mark
EMOJI_LOSS = "\u2717"   # Ballot X
EMOJI_WARN = "\u26A0"   # Warning sign
EMOJI_NONE = "\u2014"   # Em dash

# ========== Headers ==========
HEADERS = [
    'Window',
    'Time',
    'ARB Entry',
    'ARB Result',
    'ARB P/L',
    '99c Entry',
    '99c Result',
    '99c P/L',
    'Total P/L'
]

# Initial summary row values
INITIAL_SUMMARY = ['SUMMARY', '-', '-', '-', '$0.00', '-', '-', '$0.00', '$0.00']


def format_result_with_emoji(result: Optional[str]) -> str:
    """
    Add emoji indicator to result string.

    Args:
        result: The result string (PAIRED, WIN, BAIL, LOPSIDED, LOSS, etc.)

    Returns:
        Formatted string with emoji prefix
    """
    if result is None or result == '-' or result == '':
        return EMOJI_NONE

    if result in ('PAIRED', 'WIN'):
        return f"{EMOJI_WIN} {result}"
    elif result in ('BAIL', 'LOPSIDED', 'LOSS'):
        return f"{EMOJI_LOSS} {result}"
    elif result == 'PARTIAL':
        return f"{EMOJI_WARN} {result}"
    else:
        # Return as-is for unknown results
        return result


def parse_window_time(slug: str) -> str:
    """
    Parse window time from slug.

    Args:
        slug: Window slug like 'btc-updown-15m-1737417600'

    Returns:
        Time string in HH:MM format (PST)
    """
    try:
        # Extract timestamp from slug (last component)
        parts = slug.split('-')
        if len(parts) >= 4:
            timestamp = int(parts[-1])
            dt = datetime.fromtimestamp(timestamp, tz=PST)
            return dt.strftime("%H:%M")
    except (ValueError, IndexError):
        pass
    return "-"


def get_short_window_id(slug: str) -> str:
    """
    Get shortened window ID for display.

    Args:
        slug: Full window slug like 'btc-updown-15m-1737417600'

    Returns:
        Just the timestamp portion or truncated version
    """
    try:
        parts = slug.split('-')
        if len(parts) >= 4:
            # Return the timestamp
            return parts[-1]
    except (ValueError, IndexError):
        pass
    return slug[:20] if len(slug) > 20 else slug


class DashboardLogger:
    """Google Sheets dashboard logger for performance tracker."""

    def __init__(self):
        self.client = None
        self.spreadsheet = None
        self.worksheet = None
        self.enabled = False
        self._initialized = False

        if not GSPREAD_AVAILABLE:
            print("[DASHBOARD] Disabled - gspread not installed. Run: pip3 install gspread google-auth")
            return

        if not os.path.exists(CREDENTIALS_FILE):
            print(f"[DASHBOARD] Disabled - credentials file not found: {CREDENTIALS_FILE}")
            return

        self.enabled = True

    def _ensure_initialized(self) -> bool:
        """
        Initialize Google Sheets connection if not already done.
        Creates or opens spreadsheet and sets up sheet structure.

        Returns:
            True on success, False on error
        """
        if self._initialized:
            return self.worksheet is not None

        if not self.enabled:
            return False

        # Retry up to 3 times with exponential backoff
        for attempt in range(3):
            try:
                if attempt > 0:
                    time.sleep(2 ** attempt)  # Backoff: 2s, 4s

                return self._do_initialization()

            except Exception as e:
                print(f"[DASHBOARD] Initialization attempt {attempt + 1}/3 failed: {e}")
                if attempt == 2:
                    print("[DASHBOARD] Failed to initialize after 3 attempts")
                    self.enabled = False
                    return False

        return False

    def _do_initialization(self) -> bool:
        """Actual initialization logic (called by _ensure_initialized with retry)."""
        # Load credentials
        creds = Credentials.from_service_account_file(
            CREDENTIALS_FILE,
            scopes=SCOPES
        )
        self.client = gspread.authorize(creds)

        # Open existing or create new spreadsheet
        if SPREADSHEET_ID:
            # Open existing spreadsheet by ID
            print(f"[DASHBOARD] Opening existing spreadsheet: {SPREADSHEET_ID}")
            self.spreadsheet = self.client.open_by_key(SPREADSHEET_ID)
        else:
            # Create new spreadsheet
            print("[DASHBOARD] Creating new spreadsheet: Performance Tracker Dashboard")
            self.spreadsheet = self.client.create('Performance Tracker Dashboard')
            print(f"[DASHBOARD] Created spreadsheet ID: {self.spreadsheet.id}")

            # Share with user email if provided
            if SHARE_WITH_EMAIL:
                self.spreadsheet.share(
                    SHARE_WITH_EMAIL,
                    perm_type='user',
                    role='writer'
                )
                print(f"[DASHBOARD] Shared with: {SHARE_WITH_EMAIL}")

        # Get or create Dashboard worksheet
        try:
            self.worksheet = self.spreadsheet.worksheet("Dashboard")
            print("[DASHBOARD] Found existing Dashboard worksheet")
        except gspread.exceptions.WorksheetNotFound:
            # Rename first sheet to Dashboard or create new one
            try:
                self.worksheet = self.spreadsheet.sheet1
                self.worksheet.update_title("Dashboard")
            except Exception:
                self.worksheet = self.spreadsheet.add_worksheet(
                    title="Dashboard",
                    rows=1000,
                    cols=len(HEADERS)
                )
            print("[DASHBOARD] Created Dashboard worksheet")

        # Check if worksheet needs structure setup
        existing_data = self.worksheet.get_all_values()
        if not existing_data:
            # Set up structure
            self._setup_sheet_structure()

        self._initialized = True
        print("[DASHBOARD] Connected to Google Sheets")
        return True

    def _setup_sheet_structure(self) -> None:
        """Set up the initial sheet structure with headers and summary row."""
        print("[DASHBOARD] Setting up sheet structure...")

        # Write headers to row 1
        self.worksheet.update('A1', [HEADERS], value_input_option='USER_ENTERED')

        # Write initial summary row to row 2
        self.worksheet.update('A2', [INITIAL_SUMMARY], value_input_option='USER_ENTERED')

        # Freeze first 2 rows (headers + summary)
        self.worksheet.freeze(rows=2)

        # Format headers as bold
        self.worksheet.format('A1:I1', {'textFormat': {'bold': True}})

        # Format summary row as bold and with gray background
        self.worksheet.format('A2:I2', {
            'textFormat': {'bold': True},
            'backgroundColor': GRAY_BG
        })

        print("[DASHBOARD] Sheet structure setup complete")

    def log_row(self, window_state: Dict[str, Any]) -> Optional[int]:
        """
        Log a window result row to the dashboard.

        Args:
            window_state: Dictionary containing window data:
                - slug: Window identifier
                - arb_entry: True if ARB trade was made
                - arb_result: 'PAIRED', 'BAIL', 'LOPSIDED', etc.
                - arb_pnl: ARB P/L in dollars
                - capture_entry: True if 99c capture trade was made
                - capture_result: 'WIN', 'LOSS', etc.
                - capture_pnl: 99c capture P/L in dollars

        Returns:
            Row number that was written, or None on failure
        """
        if not self._ensure_initialized():
            return None

        # Extract data from window_state
        slug = window_state.get('slug', '')
        arb_entry = window_state.get('arb_entry', False)
        arb_result = window_state.get('arb_result')
        arb_pnl = window_state.get('arb_pnl', 0.0) or 0.0
        capture_entry = window_state.get('capture_entry', False)
        capture_result = window_state.get('capture_result')
        capture_pnl = window_state.get('capture_pnl', 0.0) or 0.0

        # Build row values
        row_data = [
            get_short_window_id(slug),                                    # Window
            parse_window_time(slug),                                      # Time
            'Yes' if arb_entry else EMOJI_NONE,                          # ARB Entry
            format_result_with_emoji(arb_result) if arb_entry else EMOJI_NONE,  # ARB Result
            f'${arb_pnl:+.2f}' if arb_entry else EMOJI_NONE,            # ARB P/L
            'Yes' if capture_entry else EMOJI_NONE,                      # 99c Entry
            format_result_with_emoji(capture_result) if capture_entry else EMOJI_NONE,  # 99c Result
            f'${capture_pnl:+.2f}' if capture_entry else EMOJI_NONE,    # 99c P/L
            f'${(arb_pnl + capture_pnl):+.2f}'                           # Total P/L
        ]

        # Retry up to 3 times with exponential backoff
        for attempt in range(3):
            try:
                if attempt > 0:
                    self._initialized = False  # Force reconnection on retry
                    if not self._ensure_initialized():
                        return None
                    time.sleep(2 ** attempt)  # Backoff: 2s, 4s

                # Append row to worksheet
                self.worksheet.append_row(row_data, value_input_option='USER_ENTERED')

                # Get the row number that was just appended
                row_number = len(self.worksheet.get_all_values())

                print(f"[DASHBOARD] Logged row {row_number}: {slug}")
                return row_number

            except Exception as e:
                print(f"[DASHBOARD] Failed to log row (attempt {attempt + 1}/3): {e}")
                if attempt == 2:
                    print(f"[DASHBOARD] Giving up on row: {slug}")
                    return None

        return None


# ========== Global Instance ==========
_dashboard: Optional[DashboardLogger] = None


def init_dashboard() -> DashboardLogger:
    """Initialize and return the global dashboard logger."""
    global _dashboard
    if _dashboard is None:
        _dashboard = DashboardLogger()
    return _dashboard


def get_dashboard() -> Optional[DashboardLogger]:
    """Get the global dashboard logger (may be None if not initialized)."""
    return _dashboard


def log_dashboard_row(window_state: Dict[str, Any]) -> bool:
    """
    Convenience function to log a window result row.
    Returns False silently if dashboard not enabled (graceful degradation).

    Args:
        window_state: Dictionary containing window data

    Returns:
        True if row was logged successfully, False otherwise
    """
    if _dashboard is None or not _dashboard.enabled:
        return False
    result = _dashboard.log_row(window_state)
    return result is not None


# ========== Test Function ==========

def test_dashboard():
    """Test the dashboard logger."""
    print("=" * 50)
    print("GOOGLE SHEETS DASHBOARD TEST")
    print("=" * 50)

    print(f"\nConfiguration:")
    print(f"  gspread installed: {GSPREAD_AVAILABLE}")
    print(f"  Credentials file: {CREDENTIALS_FILE}")
    print(f"  Credentials exists: {os.path.exists(CREDENTIALS_FILE)}")
    print(f"  Spreadsheet ID: {SPREADSHEET_ID[:20]}..." if SPREADSHEET_ID else "  Spreadsheet ID: NOT SET (will create new)")
    print(f"  Share with email: {SHARE_WITH_EMAIL if SHARE_WITH_EMAIL else 'NOT SET'}")

    dashboard = init_dashboard()

    if not dashboard.enabled:
        print("\n[DASHBOARD] Logger is DISABLED. Check configuration above.")
        print("\nTo enable:")
        print("  1. Install gspread: pip3 install gspread google-auth")
        print("  2. Create credentials file at ~/.google_sheets_credentials.json")
        return False

    print("\nAttempting to connect...")
    if not dashboard._ensure_initialized():
        print("[DASHBOARD] Failed to initialize connection.")
        return False

    print("[DASHBOARD] Connected successfully!")

    # Test logging a row
    print("\nLogging test row...")
    test_window = {
        "slug": "btc-updown-15m-1737417600",
        "arb_entry": True,
        "arb_result": "PAIRED",
        "arb_pnl": 0.05,
        "capture_entry": False,
        "capture_result": None,
        "capture_pnl": 0.0
    }

    row = dashboard.log_row(test_window)
    if row:
        print(f"[DASHBOARD] Test row logged at row {row}!")
    else:
        print("[DASHBOARD] Failed to log test row.")

    print("\n" + "=" * 50)
    print("Check your Google Sheet to verify the test entries!")
    if dashboard.spreadsheet:
        print(f"Spreadsheet URL: https://docs.google.com/spreadsheets/d/{dashboard.spreadsheet.id}")
    print("=" * 50)

    return True


if __name__ == "__main__":
    test_dashboard()
