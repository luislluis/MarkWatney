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


def parse_pnl(pnl_str: str) -> float:
    """
    Parse P/L string to float value.

    Handles formats like '$+0.05', '$-0.10', '-', em dash, etc.

    Args:
        pnl_str: P/L string from sheet cell

    Returns:
        Float value (0.0 for non-numeric values like '-' or em dash)
    """
    if not pnl_str or pnl_str in ('-', EMOJI_NONE, ''):
        return 0.0

    try:
        # Remove $ and any whitespace
        cleaned = pnl_str.replace('$', '').replace(' ', '')
        return float(cleaned)
    except (ValueError, AttributeError):
        return 0.0


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

    def _apply_row_formatting(self, row_number: int, arb_result: Optional[str],
                               arb_pnl: float, capture_result: Optional[str],
                               capture_pnl: float) -> None:
        """
        Apply color formatting to a data row based on results and P/L values.

        Uses batch_format for efficiency (single API call for all formatting).

        Args:
            row_number: The row number to format (1-indexed)
            arb_result: ARB result string ('PAIRED', 'BAIL', 'LOPSIDED', None)
            arb_pnl: ARB P/L in dollars
            capture_result: 99c capture result ('WIN', 'LOSS', None)
            capture_pnl: 99c capture P/L in dollars
        """
        formats = []

        # ARB Result column (D) - green for PAIRED, red for BAIL/LOPSIDED
        if arb_result == 'PAIRED':
            formats.append({
                "range": f"D{row_number}",
                "format": {"backgroundColor": GREEN_BG}
            })
        elif arb_result in ('BAIL', 'LOPSIDED'):
            formats.append({
                "range": f"D{row_number}",
                "format": {"backgroundColor": RED_BG}
            })

        # ARB P/L column (E) - green if positive, red if negative
        if arb_pnl > 0:
            formats.append({
                "range": f"E{row_number}",
                "format": {"backgroundColor": GREEN_BG}
            })
        elif arb_pnl < 0:
            formats.append({
                "range": f"E{row_number}",
                "format": {"backgroundColor": RED_BG}
            })

        # 99c Result column (G) - green for WIN, red for LOSS
        if capture_result == 'WIN':
            formats.append({
                "range": f"G{row_number}",
                "format": {"backgroundColor": GREEN_BG}
            })
        elif capture_result == 'LOSS':
            formats.append({
                "range": f"G{row_number}",
                "format": {"backgroundColor": RED_BG}
            })

        # 99c P/L column (H) - green if positive, red if negative
        if capture_pnl > 0:
            formats.append({
                "range": f"H{row_number}",
                "format": {"backgroundColor": GREEN_BG}
            })
        elif capture_pnl < 0:
            formats.append({
                "range": f"H{row_number}",
                "format": {"backgroundColor": RED_BG}
            })

        # Total P/L column (I) - green if positive, red if negative
        total_pnl = arb_pnl + capture_pnl
        if total_pnl > 0:
            formats.append({
                "range": f"I{row_number}",
                "format": {"backgroundColor": GREEN_BG}
            })
        elif total_pnl < 0:
            formats.append({
                "range": f"I{row_number}",
                "format": {"backgroundColor": RED_BG}
            })

        # Apply all formats in one batch call
        if formats:
            self.worksheet.batch_format(formats)

    def update_summary(self) -> bool:
        """
        Update the summary row (row 2) with running totals and win rates.

        Reads all data rows, calculates totals and win rates, updates row 2.

        Returns:
            True on success, False on error
        """
        if not self._ensure_initialized():
            return False

        try:
            # Get all values from the worksheet
            all_values = self.worksheet.get_all_values()

            # Skip header (row 0) and summary (row 1), process data rows (row 2+)
            if len(all_values) <= 2:
                # No data rows yet
                return True

            data_rows = all_values[2:]  # Skip header and summary

            # Calculate ARB totals
            arb_trades = 0
            arb_wins = 0
            total_arb_pnl = 0.0

            for row in data_rows:
                if len(row) < 5:
                    continue

                # Column C (index 2) = ARB Entry ('Yes' or em dash)
                if row[2] == 'Yes':
                    arb_trades += 1
                    # Column D (index 3) = ARB Result (check for checkmark = win)
                    if EMOJI_WIN in row[3]:
                        arb_wins += 1

                # Column E (index 4) = ARB P/L
                total_arb_pnl += parse_pnl(row[4])

            # Calculate 99c capture totals
            capture_trades = 0
            capture_wins = 0
            total_99c_pnl = 0.0

            for row in data_rows:
                if len(row) < 8:
                    continue

                # Column F (index 5) = 99c Entry ('Yes' or em dash)
                if row[5] == 'Yes':
                    capture_trades += 1
                    # Column G (index 6) = 99c Result (check for checkmark = win)
                    if EMOJI_WIN in row[6]:
                        capture_wins += 1

                # Column H (index 7) = 99c P/L
                total_99c_pnl += parse_pnl(row[7])

            # Calculate total P/L
            total_pnl = total_arb_pnl + total_99c_pnl

            # Build win rate strings
            arb_rate = f"{arb_wins}/{arb_trades}" if arb_trades else "-"
            capture_rate = f"{capture_wins}/{capture_trades}" if capture_trades else "-"

            # Build summary row
            summary = [
                'SUMMARY',
                '-',                                  # Time (not applicable)
                arb_rate,                             # ARB Entry shows win rate
                '-',                                  # ARB Result
                f'${total_arb_pnl:+.2f}',            # ARB P/L total
                capture_rate,                         # 99c Entry shows win rate
                '-',                                  # 99c Result
                f'${total_99c_pnl:+.2f}',            # 99c P/L total
                f'${total_pnl:+.2f}'                 # Total P/L
            ]

            # Update row 2 in place
            self.worksheet.update('A2:I2', [summary], value_input_option='USER_ENTERED')

            # Apply coloring to summary P/L cells based on positive/negative
            summary_formats = []

            # ARB P/L (E2)
            if total_arb_pnl > 0:
                summary_formats.append({
                    "range": "E2",
                    "format": {"backgroundColor": GREEN_BG}
                })
            elif total_arb_pnl < 0:
                summary_formats.append({
                    "range": "E2",
                    "format": {"backgroundColor": RED_BG}
                })
            else:
                summary_formats.append({
                    "range": "E2",
                    "format": {"backgroundColor": GRAY_BG}
                })

            # 99c P/L (H2)
            if total_99c_pnl > 0:
                summary_formats.append({
                    "range": "H2",
                    "format": {"backgroundColor": GREEN_BG}
                })
            elif total_99c_pnl < 0:
                summary_formats.append({
                    "range": "H2",
                    "format": {"backgroundColor": RED_BG}
                })
            else:
                summary_formats.append({
                    "range": "H2",
                    "format": {"backgroundColor": GRAY_BG}
                })

            # Total P/L (I2)
            if total_pnl > 0:
                summary_formats.append({
                    "range": "I2",
                    "format": {"backgroundColor": GREEN_BG}
                })
            elif total_pnl < 0:
                summary_formats.append({
                    "range": "I2",
                    "format": {"backgroundColor": RED_BG}
                })
            else:
                summary_formats.append({
                    "range": "I2",
                    "format": {"backgroundColor": GRAY_BG}
                })

            # Apply formatting (keep summary row bold)
            self.worksheet.batch_format(summary_formats)

            print(f"[DASHBOARD] Summary updated: ARB {arb_rate} (${total_arb_pnl:+.2f}), 99c {capture_rate} (${total_99c_pnl:+.2f}), Total ${total_pnl:+.2f}")
            return True

        except Exception as e:
            print(f"[DASHBOARD] Summary update failed: {e}")
            return False

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

                # Apply color formatting to result and P/L cells
                try:
                    self._apply_row_formatting(row_number, arb_result, arb_pnl, capture_result, capture_pnl)
                except Exception as fmt_err:
                    # Graceful degradation - row logged but colors failed
                    print(f"[DASHBOARD] Color formatting failed: {fmt_err}")

                # Update summary row with new totals
                try:
                    self.update_summary()
                except Exception as sum_err:
                    # Graceful degradation - row logged but summary failed
                    print(f"[DASHBOARD] Summary update failed: {sum_err}")

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


def update_dashboard_summary() -> bool:
    """
    Convenience function to update the summary row.
    Returns False silently if dashboard not enabled (graceful degradation).

    Returns:
        True if summary was updated successfully, False otherwise
    """
    if _dashboard is None or not _dashboard.enabled:
        return False
    return _dashboard.update_summary()


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
