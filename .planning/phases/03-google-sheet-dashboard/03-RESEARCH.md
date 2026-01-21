# Phase 3: Google Sheet Dashboard - Research

**Researched:** 2026-01-20
**Domain:** Google Sheets API / gspread library / cell formatting
**Confidence:** HIGH

## Summary

Phase 3 adds a beautiful, color-coded Google Sheet dashboard to the Performance Tracker bot. The existing `sheets_logger.py` already provides working patterns for gspread authentication, connection, and basic operations. This phase extends that foundation with cell formatting (colors), emoji indicators, and a live summary row.

The gspread library (v6.1.4) provides all required functionality natively:
- Creating new spreadsheets via `gc.create()`
- Cell formatting via `worksheet.format()` with backgroundColor
- Batch formatting via `worksheet.batch_format()` for efficiency
- Inserting rows at specific positions via `worksheet.insert_row(values, index)`
- Updating specific cell ranges via `worksheet.update()`

**Primary recommendation:** Use gspread's native formatting (no need for gspread-formatting package). Create the sheet structure once at startup, append data rows after each window, and update the summary row in-place using `worksheet.update()`.

## Standard Stack

The established libraries/tools for this domain:

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| gspread | 6.1.4 | Google Sheets Python API | Already used in sheets_logger.py, standard choice |
| google-auth | 2.x | Service account authentication | Required by gspread, already installed |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| gspread-formatting | 1.2.1 | Advanced formatting | NOT NEEDED - gspread has native format() |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| gspread | Google Sheets API directly | gspread is simpler, already in use |
| gspread-formatting | gspread native format() | Native is sufficient, fewer dependencies |

**Installation:**
```bash
# Already installed - no new dependencies needed
pip3 install gspread google-auth
```

## Architecture Patterns

### Recommended Project Structure
```
performance_tracker.py     # Main bot (add sheets integration)
sheets_dashboard.py        # NEW: Dashboard-specific sheets module
```

### Pattern 1: Separate Dashboard Module
**What:** Create `sheets_dashboard.py` as a dedicated module for the performance dashboard, similar to how `sheets_logger.py` handles trading bot logging.
**When to use:** Clean separation, reusable patterns.
**Example:**
```python
# sheets_dashboard.py
class DashboardLogger:
    def __init__(self):
        self.spreadsheet = None
        self.worksheet = None
        self._initialized = False

    def _ensure_initialized(self) -> bool:
        """Create or connect to dashboard spreadsheet."""
        if self._initialized:
            return True
        # ... connection logic
```

### Pattern 2: Summary Row at Row 2 (Below Headers)
**What:** Keep summary row at a fixed position (row 2, below headers), data rows start at row 3. Update summary in-place rather than moving it.
**When to use:** Simpler than maintaining a "top row" that shifts down.
**Example:**
```
Row 1: Headers (frozen)
Row 2: Summary row (frozen, updated in-place)
Row 3+: Data rows (appended after each window)
```

### Pattern 3: Batch Format After Write
**What:** Write data first, then apply formatting in a batch call.
**When to use:** More efficient than format-per-cell, reduces API calls.
**Example:**
```python
# Source: gspread docs
# First, write the data row
worksheet.append_row(row_data, value_input_option='USER_ENTERED')

# Then format specific cells based on values
formats = [
    {"range": "E5", "format": {"backgroundColor": GREEN}},
    {"range": "H5", "format": {"backgroundColor": RED}},
]
worksheet.batch_format(formats)
```

### Pattern 4: Color Constants
**What:** Define color constants using 0-1 RGB scale for Google Sheets API.
**When to use:** Consistent coloring across all operations.
**Example:**
```python
# Source: Google Sheets API v4 reference
# Colors use 0.0-1.0 scale (not 0-255)
GREEN_BG = {"red": 0.85, "green": 0.92, "blue": 0.83}  # Light green
RED_BG = {"red": 0.96, "green": 0.80, "blue": 0.80}    # Light red
GRAY_BG = {"red": 0.95, "green": 0.95, "blue": 0.95}   # Light gray (no trade)
```

### Anti-Patterns to Avoid
- **Format every cell individually:** Use batch_format() instead
- **Moving summary row:** Update in-place at fixed position
- **Polling for spreadsheet changes:** This is write-only, no need to read back
- **Creating new spreadsheet on every startup:** Check if exists first

## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Cell coloring | Custom API calls | `worksheet.format()` | Built into gspread |
| Batch updates | Loop of single calls | `worksheet.batch_format()` | 1 API call vs many |
| Row insertion | Manual shifting | `worksheet.insert_row()` | Handles sheet expansion |
| Spreadsheet creation | Raw API | `gc.create()` | Handles permissions |
| Frozen rows | Manual settings | `worksheet.freeze(rows=2)` | One-liner |

**Key insight:** gspread wraps all the Google Sheets API complexity. Use its methods rather than building custom solutions.

## Common Pitfalls

### Pitfall 1: API Rate Limits (429 RESOURCE_EXHAUSTED)
**What goes wrong:** Too many API calls cause 429 errors
**Why it happens:** Google Sheets API has 300 req/60s per project, 60 req/60s per user
**How to avoid:**
- Use batch operations (batch_format, batch_update)
- Don't format cells individually in a loop
- The tracker runs once per 15 minutes, so this is unlikely but be aware
**Warning signs:** APIError 429 in logs

### Pitfall 2: Color Scale Confusion (0-1 vs 0-255)
**What goes wrong:** Colors appear wrong (black instead of expected color)
**Why it happens:** Google Sheets API uses 0.0-1.0 scale, not 0-255
**How to avoid:** Define color constants correctly, verify with test writes
**Warning signs:** All colors appear as black or unexpected shades

### Pitfall 3: Service Account Visibility
**What goes wrong:** Created spreadsheet is invisible to user
**Why it happens:** Service account owns the sheet, not shared with user
**How to avoid:** After creation, call `spreadsheet.share(email, perm_type='user', role='writer')`
**Warning signs:** "Spreadsheet not found" when trying to open in browser

### Pitfall 4: Insert Row vs Append Row Confusion
**What goes wrong:** Rows appear in wrong location
**Why it happens:** `append_row()` adds to end, `insert_row()` adds at index
**How to avoid:**
- Use `append_row()` for new data rows (goes at end)
- Use `worksheet.update('A2:Z2', [summary_values])` to update summary row in-place
**Warning signs:** Summary row gets pushed down, data rows appear at top

### Pitfall 5: Emoji/Unicode Issues
**What goes wrong:** Emojis display as boxes or question marks
**Why it happens:** Font or encoding issues (rare with Google Sheets)
**How to avoid:**
- Python 3 strings handle Unicode natively
- Pass emojis directly as string literals
- Google Sheets handles Unicode well in modern browsers
**Warning signs:** Boxes or `?` characters instead of emojis

## Code Examples

Verified patterns from official sources:

### Create New Spreadsheet and Share
```python
# Source: gspread docs
gc = gspread.service_account(filename=CREDENTIALS_FILE)

# Create new spreadsheet
spreadsheet = gc.create('Performance Tracker Dashboard')

# Share with user email so they can see it
spreadsheet.share('user@example.com', perm_type='user', role='writer')

# Get the default worksheet
worksheet = spreadsheet.sheet1
worksheet.update_title('Dashboard')
```

### Set Up Sheet Structure with Headers and Frozen Rows
```python
# Source: gspread docs
HEADERS = ['Window', 'Time', 'ARB Entry', 'ARB Result', 'ARB P/L',
           '99c Entry', '99c Result', '99c P/L', 'Total P/L']

# Write headers to row 1
worksheet.update('A1', [HEADERS], value_input_option='USER_ENTERED')

# Write initial summary row to row 2
summary = ['SUMMARY', '', '', '', '$0.00', '', '', '$0.00', '$0.00']
worksheet.update('A2', [summary], value_input_option='USER_ENTERED')

# Freeze first 2 rows (headers + summary)
worksheet.freeze(rows=2)

# Format headers as bold
worksheet.format('A1:I1', {'textFormat': {'bold': True}})
```

### Append Data Row with Formatting
```python
# Source: gspread docs
# Write data row (appends at end)
row_data = ['btc-updown-15m-123', '14:00', 'Yes', 'PAIRED', '+$0.05',
            '-', '-', '$0.00', '+$0.05']
worksheet.append_row(row_data, value_input_option='USER_ENTERED')

# Get the row number that was just appended
last_row = len(worksheet.get_all_values())

# Apply conditional formatting based on values
formats = []

# Color ARB Result cell (column D)
arb_result = row_data[3]
if arb_result == 'PAIRED':
    formats.append({
        "range": f"D{last_row}",
        "format": {"backgroundColor": {"red": 0.85, "green": 0.92, "blue": 0.83}}
    })
elif arb_result in ('BAIL', 'LOPSIDED'):
    formats.append({
        "range": f"D{last_row}",
        "format": {"backgroundColor": {"red": 0.96, "green": 0.80, "blue": 0.80}}
    })

# Apply all formats in one batch call
if formats:
    worksheet.batch_format(formats)
```

### Update Summary Row In-Place
```python
# Source: gspread docs
# Calculate totals from all data rows
all_values = worksheet.get_all_values()
# Skip header (row 0) and summary (row 1), process data rows (row 2+)
data_rows = all_values[2:]

total_arb_pnl = sum(parse_pnl(row[4]) for row in data_rows if row[4])
total_99c_pnl = sum(parse_pnl(row[7]) for row in data_rows if row[7])
total_pnl = total_arb_pnl + total_99c_pnl

# Count wins for win rate
arb_wins = sum(1 for row in data_rows if row[3] == 'PAIRED')
arb_trades = sum(1 for row in data_rows if row[3] and row[3] != '-')
win_rate = f"{arb_wins}/{arb_trades}" if arb_trades else "-"

# Update summary row (row 2)
summary = ['SUMMARY', win_rate, '', '', f'${total_arb_pnl:+.2f}',
           '', '', f'${total_99c_pnl:+.2f}', f'${total_pnl:+.2f}']
worksheet.update('A2', [summary], value_input_option='USER_ENTERED')
```

### Using Emoji Indicators
```python
# Source: Python 3 unicode handling + Google Sheets unicode support
# Emojis work as direct string literals in Python 3

EMOJI_WIN = "✓"     # U+2713 Check mark
EMOJI_LOSS = "✗"    # U+2717 Ballot X
EMOJI_WARN = "⚠"    # U+26A0 Warning sign
EMOJI_NONE = "—"    # U+2014 Em dash

def format_result_with_emoji(result):
    """Add emoji indicator to result string."""
    if result == 'PAIRED' or result == 'WIN':
        return f"{EMOJI_WIN} {result}"
    elif result in ('BAIL', 'LOPSIDED', 'LOSS'):
        return f"{EMOJI_LOSS} {result}"
    elif result == 'PARTIAL':
        return f"{EMOJI_WARN} {result}"
    else:
        return EMOJI_NONE
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| gspread-formatting package | gspread native format() | gspread 5.0+ | Fewer dependencies |
| Individual cell updates | batch_update / batch_format | gspread 5.0+ | Better performance |
| value_input_option default | Explicit 'USER_ENTERED' | Always | Correct number/date parsing |

**Deprecated/outdated:**
- gspread < 5.0: Use 6.1.4 (latest)
- Using 0-255 RGB values: Must use 0.0-1.0 scale

## Open Questions

Things that couldn't be fully resolved:

1. **Spreadsheet ID vs Name**
   - What we know: Can create by name, open by ID or name
   - What's unclear: Should we use existing ID from env var or always create new?
   - Recommendation: Create new spreadsheet with unique name, store ID for future reference. Allow env var override for existing sheet.

2. **Summary Row Position**
   - What we know: Can freeze rows, can update in-place
   - What's unclear: Should summary be row 2 (below headers) or dynamically at top?
   - Recommendation: Fixed at row 2 for simplicity. Headers at row 1, summary at row 2 (both frozen), data from row 3 onwards.

## Sources

### Primary (HIGH confidence)
- [gspread Official Documentation](https://docs.gspread.org/en/latest/user-guide.html) - Creating spreadsheets, formatting, batch operations
- [gspread Worksheet API](https://docs.gspread.org/en/latest/api/models/worksheet.html) - insert_row, format, batch_format methods
- [Google Sheets API Color Reference](https://developers.google.com/sheets/api/reference/rest/v4/spreadsheets/other) - 0-1 RGB scale

### Secondary (MEDIUM confidence)
- [gspread-formatting docs](https://gspread-formatting.readthedocs.io/) - Confirms native gspread formatting is sufficient
- [Google Sheets API Formatting](https://developers.google.com/sheets/api/samples/formatting) - Basic formatting patterns

### Tertiary (LOW confidence)
- Various blog posts about emoji handling - Verified: Python 3 strings work directly

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - gspread already in use, well documented
- Architecture: HIGH - Patterns verified from official docs
- Pitfalls: HIGH - Rate limits and color scale documented by Google

**Research date:** 2026-01-20
**Valid until:** 2026-02-20 (30 days - gspread is stable)

## Existing Code Reference

The existing `sheets_logger.py` provides these reusable patterns:

1. **Credential loading:** `Credentials.from_service_account_file()`
2. **Lazy initialization:** `_ensure_initialized()` pattern
3. **Retry with backoff:** 3 retries with exponential backoff
4. **Error handling:** Graceful degradation if sheets unavailable
5. **Batch operations:** `append_rows()` for tick data

The new `sheets_dashboard.py` should follow the same patterns for consistency.
