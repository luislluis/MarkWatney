#!/usr/bin/env python3
"""
Sync 99c trades to daily performance tabs.
Each day gets its own tab with 96 pre-built windows (00:00-23:45 ET).

Usage:
    python3 sync_daily_dashboard.py           # Sync today
    python3 sync_daily_dashboard.py 2026-01-24  # Sync specific date
    python3 sync_daily_dashboard.py --all     # Sync all days with trades
"""

import os
import sys
import time
import json
import re
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

# Load environment variables from ~/.env
load_dotenv(os.path.expanduser("~/.env"))

EST = ZoneInfo("America/New_York")
PST = ZoneInfo("America/Los_Angeles")

# Spreadsheet IDs
EVENTS_SHEET_ID = "1fxGKxKxj2RAL0hwtqjaOWdmnwqg6RcKseYYP-cCKp74"
DASHBOARD_SHEET_ID = "18bCu_op6oGVVQ9DFGW6oJjbZj_HdJYQ5UlW87iVKbCU"
CREDENTIALS_FILE = os.path.expanduser("~/.google_sheets_credentials.json")

# Colors
GREEN_BG = {"red": 0.85, "green": 0.92, "blue": 0.83}
RED_BG = {"red": 0.96, "green": 0.80, "blue": 0.80}
GRAY_BG = {"red": 0.95, "green": 0.95, "blue": 0.95}
DARK_GRAY_BG = {"red": 0.85, "green": 0.85, "blue": 0.85}
WHITE_BG = {"red": 1.0, "green": 1.0, "blue": 1.0}

HEADERS = ["Window (ET)", "Side", "Shares", "Entry", "Result", "P&L", "Entry Δ", "Entry T", "Entry %", "Exit Why", "Exit %", "Notes"]

# Cache for market outcomes to avoid repeated API calls
_outcome_cache = {}

# Polymarket Data API for verified fill prices
POLYMARKET_DATA_API = "https://data-api.polymarket.com/trades"


def fetch_all_trades(wallet_address: str) -> dict:
    """
    Fetch all trades from Polymarket Data API and build lookup by (slug, outcome).

    Returns dict mapping (slug, outcome) -> trade data with actual fill price/size.
    On API failure, returns empty dict (caller should use logged values as fallback).
    """
    try:
        resp = requests.get(
            POLYMARKET_DATA_API,
            params={"user": wallet_address, "limit": 500, "side": "BUY"},
            timeout=10
        )
        resp.raise_for_status()

        lookup = {}
        for t in resp.json():
            slug = t.get("slug", "")
            outcome = t.get("outcome", "")  # "Up", "Down", "Yes", "No"
            if slug and outcome:
                # Normalize outcome to match our Side format (UP/DOWN)
                normalized_outcome = outcome.upper()
                key = (slug, normalized_outcome)
                # Keep most recent trade for each key (by timestamp)
                if key not in lookup or t.get("timestamp", 0) > lookup[key].get("timestamp", 0):
                    lookup[key] = {
                        "price": float(t.get("price", 0)),
                        "size": float(t.get("size", 0)),
                        "timestamp": t.get("timestamp"),
                        "tx_hash": t.get("transactionHash"),
                        "conditionId": t.get("conditionId"),
                    }
        print(f"[SYNC] Fetched {len(lookup)} verified trades from Polymarket API")
        return lookup

    except requests.RequestException as e:
        print(f"[SYNC] Warning: Failed to fetch trades from Polymarket API: {e}")
        return {}

def get_market_outcome(window_id):
    """Query Polymarket API to get actual market outcome (UP or DOWN won)"""
    if window_id in _outcome_cache:
        return _outcome_cache[window_id]

    # Extract timestamp from window_id (e.g., "btc-updown-15m-1769277600" -> "1769277600")
    if "-" in window_id:
        slug = window_id
    else:
        slug = f"btc-updown-15m-{window_id}"

    try:
        url = f"https://gamma-api.polymarket.com/events?slug={slug}"
        resp = requests.get(url, timeout=10)
        data = resp.json()

        if not data:
            _outcome_cache[window_id] = None
            return None

        market = data[0].get('markets', [{}])[0]

        if not market.get('closed'):
            # Market not closed yet
            _outcome_cache[window_id] = "PENDING"
            return "PENDING"

        outcome_prices_raw = market.get('outcomePrices', '[]')
        # Parse JSON string if needed
        if isinstance(outcome_prices_raw, str):
            outcome_prices = json.loads(outcome_prices_raw)
        else:
            outcome_prices = outcome_prices_raw

        if len(outcome_prices) >= 2:
            # outcomePrices: ["1", "0"] = UP won, ["0", "1"] = DOWN won
            if outcome_prices[0] == "1":
                _outcome_cache[window_id] = "UP"
                return "UP"
            elif outcome_prices[1] == "1":
                _outcome_cache[window_id] = "DOWN"
                return "DOWN"

        _outcome_cache[window_id] = None
        return None
    except Exception as e:
        print(f"[SYNC] Warning: Could not fetch outcome for {window_id}: {e}")
        _outcome_cache[window_id] = None
        return None


def get_sheets_client():
    """Initialize Google Sheets client"""
    creds = Credentials.from_service_account_file(
        CREDENTIALS_FILE,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return gspread.authorize(creds)


def create_daily_tab(gc, date_str):
    """Create a daily tab with 96 pre-built windows"""
    sheet = gc.open_by_key(DASHBOARD_SHEET_ID)

    # Create or clear tab
    try:
        ws = sheet.worksheet(date_str)
        ws.clear()
        print(f"[SYNC] Cleared existing tab: {date_str}")
    except gspread.WorksheetNotFound:
        ws = sheet.add_worksheet(date_str, rows=100, cols=12)
        print(f"[SYNC] Created new tab: {date_str}")

    # Build rows
    rows = []

    # Row 1: Summary placeholder (12 columns)
    rows.append(["📊 DAILY SUMMARY", "—", "—", "—", "—", "$0.00", "—", "—", "—", "—", "—", "Loading..."])

    # Row 2: Headers
    rows.append(HEADERS)

    # Rows 3-98: 96 windows (00:00-23:45 ET)
    date = datetime.strptime(date_str, "%Y-%m-%d")
    start_of_day = date.replace(hour=0, minute=0, second=0, tzinfo=EST)

    for i in range(96):
        window_start = start_of_day + timedelta(minutes=15 * i)
        window_end = window_start + timedelta(minutes=15)
        window_str = f"{window_start.strftime('%H:%M')}-{window_end.strftime('%H:%M')}"
        rows.append([window_str, "—", "—", "—", "—", "—", "—", "—", "—", "—", "—", ""])

    # Write all rows
    ws.update(values=rows, range_name=f"A1:L{len(rows)}")

    # Format header rows
    ws.format("A1:L1", {
        "textFormat": {"bold": True, "fontSize": 11},
        "backgroundColor": DARK_GRAY_BG
    })
    ws.format("A2:L2", {
        "textFormat": {"bold": True},
        "backgroundColor": GRAY_BG
    })

    # Format empty windows (light gray)
    ws.format("A3:L98", {"backgroundColor": GRAY_BG})

    # Freeze header rows
    ws.freeze(rows=2)

    # Set column widths
    try:
        requests = [
            {"updateDimensionProperties": {
                "range": {"sheetId": ws.id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1},
                "properties": {"pixelSize": 110}, "fields": "pixelSize"}},
            {"updateDimensionProperties": {
                "range": {"sheetId": ws.id, "dimension": "COLUMNS", "startIndex": 1, "endIndex": 2},
                "properties": {"pixelSize": 60}, "fields": "pixelSize"}},
            {"updateDimensionProperties": {
                "range": {"sheetId": ws.id, "dimension": "COLUMNS", "startIndex": 5, "endIndex": 6},
                "properties": {"pixelSize": 120}, "fields": "pixelSize"}},
        ]
        sheet.batch_update({"requests": requests})
    except Exception:
        pass  # Column width is optional

    return ws


def fetch_trades_for_date(gc, date_str):
    """Fetch CAPTURE_FILL events for a specific date and verify prices against Polymarket API"""
    sheet = gc.open_by_key(EVENTS_SHEET_ID)
    events = sheet.worksheet("Events").get_all_records()

    # Build lookup of early exits by window_id
    # These override market-resolution P&L since we exited before resolution
    early_exits = {}
    for e in events:
        event_type = e.get("Event", "")
        if event_type in ("99C_EARLY_EXIT", "99C_PRICE_STOP"):
            window_id = e.get("Window ID", "")
            if not window_id:
                continue
            # Parse P&L (formatted as "$-0.70" or "$0.70")
            pnl_str = e.get("PnL", "")
            if isinstance(pnl_str, str) and pnl_str:
                try:
                    pnl = float(pnl_str.replace('$', ''))
                except ValueError:
                    pnl = 0
            else:
                pnl = float(pnl_str) if pnl_str else 0
            # Parse details JSON for reason and exit price
            details_str = e.get("Details", "")
            reason = "early exit"
            exit_price = e.get("Price", "")
            if details_str:
                try:
                    details = json.loads(details_str) if isinstance(details_str, str) else details_str
                    if details.get("reason") == "ob_reversal":
                        reason = "OB exit"
                    elif details.get("reason") == "price_stop":
                        reason = "price stop"
                except (json.JSONDecodeError, TypeError):
                    pass
            early_exits[window_id] = {"pnl": pnl, "reason": reason, "exit_price": exit_price}

    # Build lookup of CAPTURE_99C events by window_id for entry details
    entry_details = {}
    for e in events:
        if e.get("Event") != "CAPTURE_99C":
            continue
        window_id = e.get("Window ID", "")
        if not window_id:
            continue
        details_str = e.get("Details", "")
        if details_str:
            try:
                details = json.loads(details_str) if isinstance(details_str, str) else details_str
                entry_details[window_id] = {
                    "confidence": details.get("confidence", 0),
                    "ttl": details.get("ttl", 0),
                    "btc_delta": details.get("btc_delta", 0),
                    "ask_price": details.get("ask_price", 0),
                }
            except (json.JSONDecodeError, TypeError):
                pass

    # Fetch verified trades from Polymarket Data API
    wallet_address = os.getenv("WALLET_ADDRESS", "")
    verified_trades = {}
    if wallet_address:
        verified_trades = fetch_all_trades(wallet_address)
    else:
        print("[SYNC] Warning: WALLET_ADDRESS not set, cannot verify fill prices")

    trades = []
    for e in events:
        if e.get("Event") != "CAPTURE_FILL":
            continue

        timestamp = e.get("Timestamp", "")
        if not timestamp:
            continue

        # Parse timestamp (PST) and convert to EST
        try:
            dt_pst = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
            dt_pst = dt_pst.replace(tzinfo=PST)
            dt_est = dt_pst.astimezone(EST)
        except ValueError:
            continue

        # Check if this trade is on the target date (in EST)
        trade_date = dt_est.strftime("%Y-%m-%d")
        if trade_date != date_str:
            continue

        # Get logged shares (will be verified against API)
        logged_shares = e.get("Shares", 5)
        if isinstance(logged_shares, str):
            try:
                logged_shares = int(float(logged_shares))
            except ValueError:
                logged_shares = 5

        # Get logged fill price from Events sheet
        price_str = str(e.get("Price", "")).strip()
        if price_str and price_str not in ("", "-"):
            try:
                logged_price = float(price_str)
            except ValueError:
                logged_price = 0.99  # Default to 99c bid price
        else:
            logged_price = 0.99  # Default to 99c bid price

        side = e.get("Side", "")
        window_id = e.get("Window ID", "")

        # Try to get verified price/shares from Polymarket Data API
        fill_price = logged_price
        shares = logged_shares
        verified_key = (window_id, side)

        if verified_key in verified_trades:
            verified = verified_trades[verified_key]
            verified_price = verified["price"]
            verified_shares = verified["size"]

            # Log discrepancies for debugging
            if abs(verified_price - logged_price) > 0.001:
                print(f"[SYNC] PRICE FIX: {window_id} ${logged_price:.2f} → ${verified_price:.2f}")
            if abs(verified_shares - logged_shares) > 0.1:
                print(f"[SYNC] SHARES FIX: {window_id} {logged_shares} → {verified_shares}")

            fill_price = verified_price
            shares = verified_shares
        elif verified_trades:
            # API was reachable but trade not found - use logged values with warning
            print(f"[SYNC] Warning: No verified trade found for {window_id} {side}, using logged values")

        # Check for early exit FIRST (overrides market resolution)
        notes = ""
        if window_id in early_exits:
            exit_data = early_exits[window_id]
            result = "LOSS"
            pnl = exit_data["pnl"]
            notes = exit_data["reason"]
            print(f"[SYNC] Early exit detected: {window_id} P&L=${pnl:.2f} ({notes})")
        else:
            # Get actual market outcome from Polymarket API
            winning_side = get_market_outcome(window_id)
            time.sleep(0.1)  # Rate limit API calls

            # Determine result based on actual outcome
            if winning_side == "PENDING":
                result = "PENDING"
                pnl = 0
            elif winning_side is None:
                result = "?"
                pnl = 0
            elif winning_side == side:
                result = "WIN"
                pnl = shares * (1.00 - fill_price)  # Win: get $1 per share
            else:
                result = "LOSS"
                pnl = -shares * fill_price  # Loss: lose the cost

        # Get entry details from CAPTURE_99C event
        entry = entry_details.get(window_id, {})
        entry_confidence = entry.get("confidence", 0)
        entry_ttl = entry.get("ttl", 0)
        entry_delta = entry.get("btc_delta", 0)

        # Get exit details from early exit events
        exit_data = early_exits.get(window_id, {})
        exit_reason = exit_data.get("reason", "")
        exit_price = exit_data.get("exit_price", "")

        trades.append({
            "timestamp": timestamp,
            "time_est": dt_est.strftime("%H:%M"),
            "hour": dt_est.hour,
            "minute": dt_est.minute,
            "side": side,
            "shares": shares,
            "fill_price": fill_price,
            "pnl": pnl,
            "result": result,
            "window_id": window_id,
            "notes": notes,
            # Entry details
            "entry_delta": entry_delta,
            "entry_ttl": entry_ttl,
            "entry_confidence": entry_confidence,
            # Exit details
            "exit_reason": exit_reason,
            "exit_price": exit_price,
        })

    return trades


def get_window_row(hour, minute):
    """Calculate the row number for a given time (0-indexed from row 3)"""
    # Row 3 = 00:00-00:15
    # Row 4 = 00:15-00:30
    # etc.
    window_index = hour * 4 + (minute // 15)
    return window_index + 3  # +3 because row 1=summary, row 2=headers


def sync_day(gc, date_str):
    """Sync trades for a single day"""
    print(f"[SYNC] Syncing {date_str}...")

    # Create/clear daily tab
    ws = create_daily_tab(gc, date_str)

    # Fetch trades for this date
    trades = fetch_trades_for_date(gc, date_str)
    print(f"[SYNC] Found {len(trades)} trades for {date_str}")

    if not trades:
        # Calculate next update time
        now_pst = datetime.now(PST)
        minutes_until_next = 15 - (now_pst.minute % 15)
        next_update = now_pst + timedelta(minutes=minutes_until_next)
        next_update_str = next_update.strftime('%H:%M PST')
        # Update summary to show no trades (12 columns)
        ws.update("A1:L1", [["📊 NO TRADES", "—", "—", "—", "—", "$0.00", "—", "—", "—", "—", "—", f"Next: {next_update_str}"]])
        return 0

    # Group trades by window
    window_trades = {}
    for t in trades:
        row = get_window_row(t["hour"], t["minute"])
        if row not in window_trades:
            window_trades[row] = []
        window_trades[row].append(t)

    # Update each window with trade data
    updates = []
    formats = []

    for row, row_trades in window_trades.items():
        # Use first trade for the window (shouldn't have multiple)
        t = row_trades[0]

        # Build row data
        # Calculate P&L percentage based on cost (shares * fill_price)
        cost = t["shares"] * t["fill_price"]
        pnl_pct = (t["pnl"] / cost * 100) if cost > 0 else 0

        # Format entry details
        entry_delta = t.get("entry_delta", 0)
        entry_delta_str = f"${entry_delta:+.0f}" if entry_delta else "—"
        entry_ttl = t.get("entry_ttl", 0)
        entry_ttl_str = f"{entry_ttl:.0f}s" if entry_ttl else "—"
        entry_conf = t.get("entry_confidence", 0)
        entry_conf_str = f"{entry_conf*100:.0f}%" if entry_conf else "—"

        # Format exit details
        exit_reason = t.get("exit_reason", "")
        exit_price = t.get("exit_price", "")
        exit_price_str = f"{float(exit_price)*100:.0f}c" if exit_price else "—"

        row_data = [
            t["side"],                                    # B: Side
            f"{t['shares']:.0f}",                         # C: Shares
            f"{t['fill_price']*100:.0f}c",                # D: Entry (as cents)
            t["result"],                                  # E: Result
            f"${t['pnl']:+.2f}",                          # F: P&L
            entry_delta_str,                              # G: Entry Δ
            entry_ttl_str,                                # H: Entry T
            entry_conf_str,                               # I: Entry %
            exit_reason if exit_reason else "—",          # J: Exit Why
            exit_price_str if exit_reason else "—",       # K: Exit %
            t.get("notes", ""),                           # L: Notes
        ]

        updates.append({
            "range": f"B{row}:L{row}",
            "values": [row_data]
        })

        # Apply colors
        if t["result"] == "WIN":
            formats.append({"range": f"A{row}:L{row}", "format": {"backgroundColor": GREEN_BG}})
        elif t["result"] == "LOSS":
            formats.append({"range": f"A{row}:L{row}", "format": {"backgroundColor": RED_BG}})

    # Batch update values
    if updates:
        ws.batch_update(updates, value_input_option="USER_ENTERED")

    # Batch update formats for trade windows
    if formats:
        ws.batch_format(formats)

    # Format past/future windows (rows 3-98 = 96 windows)
    # Determine which windows are past vs future
    now_et = datetime.now(EST)
    target_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=EST)

    past_formats = []
    future_formats = []

    for i in range(96):
        row = i + 3  # Rows 3-98

        # Skip rows that have trades (already formatted)
        if row in window_trades:
            continue

        # Calculate window start time
        window_hour = i // 4
        window_minute = (i % 4) * 15
        window_start = target_date.replace(hour=window_hour, minute=window_minute, second=0)
        window_end = window_start + timedelta(minutes=15)

        # If window has ended, grey it out; if future, white
        if window_end <= now_et:
            past_formats.append({"range": f"A{row}:L{row}", "format": {"backgroundColor": DARK_GRAY_BG}})
        else:
            future_formats.append({"range": f"A{row}:L{row}", "format": {"backgroundColor": WHITE_BG}})

    # Apply past window formatting (grey)
    if past_formats:
        ws.batch_format(past_formats)

    # Apply future window formatting (white)
    if future_formats:
        ws.batch_format(future_formats)

    # Update summary row
    wins = sum(1 for t in trades if t["result"] == "WIN")
    losses = sum(1 for t in trades if t["result"] == "LOSS")
    total_pnl = sum(t["pnl"] for t in trades)
    total_cost = sum(t["shares"] * t["fill_price"] for t in trades)
    avg_cost = total_cost / len(trades) if trades else 0
    total_pnl_pct = (total_pnl / avg_cost * 100) if avg_cost > 0 else 0
    win_rate = (wins / len(trades) * 100) if trades else 0

    # Calculate next update time (cron runs at :00, :15, :30, :45)
    now_pst = datetime.now(PST)
    minutes_until_next = 15 - (now_pst.minute % 15)
    next_update = now_pst + timedelta(minutes=minutes_until_next)
    next_update_str = next_update.strftime('%H:%M PST')

    summary = [
        f"📊 {len(trades)} Trades",
        f"✓ {wins}",
        f"✗ {losses}",
        f"{win_rate:.0f}%",
        "WIN" if total_pnl > 0 else "LOSS",
        f"${total_pnl:+.2f}",
        "—", "—", "—", "—", "—",
        f"Next: {next_update_str}"
    ]
    ws.update(values=[summary], range_name="A1:L1")

    # Color summary based on P&L
    if total_pnl > 0:
        ws.format("A1:L1", {"backgroundColor": GREEN_BG, "textFormat": {"bold": True}})
    elif total_pnl < 0:
        ws.format("A1:L1", {"backgroundColor": RED_BG, "textFormat": {"bold": True}})

    print(f"[SYNC] Updated {len(window_trades)} windows, P&L: ${total_pnl:+.2f}")
    return len(trades)


def get_all_trade_dates(gc):
    """Get list of all dates that have trades"""
    sheet = gc.open_by_key(EVENTS_SHEET_ID)
    events = sheet.worksheet("Events").get_all_records()

    dates = set()
    for e in events:
        if e.get("Event") != "CAPTURE_FILL":
            continue
        timestamp = e.get("Timestamp", "")
        if not timestamp:
            continue
        try:
            dt_pst = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
            dt_pst = dt_pst.replace(tzinfo=PST)
            dt_est = dt_pst.astimezone(EST)
            dates.add(dt_est.strftime("%Y-%m-%d"))
        except ValueError:
            continue

    return sorted(dates)


def parse_trades_count(cell):
    """Parse '📊 20 Trades' or '📊 NO TRADES' -> number"""
    if not cell or "NO TRADES" in cell:
        return 0
    match = re.search(r'(\d+)', cell)
    return int(match.group(1)) if match else 0


def parse_count(cell):
    """Parse '✓ 20' or '✗ 0' -> number"""
    if not cell:
        return 0
    match = re.search(r'(\d+)', cell)
    return int(match.group(1)) if match else 0


def parse_summary_pnl(cell):
    """Parse '$+2.55 (+52.3%)' -> pnl amount"""
    if not cell:
        return 0.0
    pnl_match = re.search(r'\$([+-]?\d+\.?\d*)', cell)
    return float(pnl_match.group(1)) if pnl_match else 0.0


def parse_entry_price(cell):
    """Parse '$0.98' -> 0.98"""
    if not cell:
        return 0.0
    match = re.search(r'\$?(\d+\.?\d*)', cell)
    return float(match.group(1)) if match else 0.0


def parse_shares(cell):
    """Parse shares from cell (could be '5' or '5.0')"""
    if not cell or cell == "—":
        return 0.0
    try:
        return float(cell)
    except ValueError:
        return 0.0


def sync_summary_tab(gc):
    """Aggregate all daily tabs into Summary tab with one row per day"""
    print("[SYNC] Syncing Summary tab...")

    sheet = gc.open_by_key(DASHBOARD_SHEET_ID)

    # Get all worksheets
    worksheets = sheet.worksheets()

    # Find daily tabs (format: YYYY-MM-DD) and sort by date
    daily_tabs = sorted(
        [ws for ws in worksheets if re.match(r'^\d{4}-\d{2}-\d{2}$', ws.title)],
        key=lambda ws: ws.title
    )

    print(f"[SYNC] Found {len(daily_tabs)} daily tabs")

    # Initialize totals
    total_trades = 0
    total_wins = 0
    total_losses = 0
    total_pnl = 0.0
    total_cost = 0.0

    # Collect data for each day
    day_rows = []

    for ws in daily_tabs:
        try:
            # Get all data from the sheet
            all_data = ws.get_all_values()
            if len(all_data) < 2:
                continue

            summary_row = all_data[0]  # Row 1 is summary
            if len(summary_row) < 6:
                continue

            # Parse values from summary row
            trades = parse_trades_count(summary_row[0])
            wins = parse_count(summary_row[1])
            losses = parse_count(summary_row[2])
            pnl = parse_summary_pnl(summary_row[5])

            # Skip days with no trades
            if trades == 0:
                continue

            # Calculate total cost by reading actual trade rows
            # Row 1 = summary, Row 2 = headers, Rows 3-98 = trade windows
            day_cost = 0.0
            for row in all_data[2:]:  # Skip summary and header rows
                if len(row) >= 4:
                    shares = parse_shares(row[2])  # Column C = Shares
                    entry_price = parse_entry_price(row[3])  # Column D = Entry price
                    if shares > 0 and entry_price > 0:
                        day_cost += shares * entry_price

            # Calculate day stats
            win_rate = (wins / trades * 100) if trades > 0 else 0
            avg_trade_cost = day_cost / trades if trades > 0 else 0

            # Add to totals
            total_trades += trades
            total_wins += wins
            total_losses += losses
            total_pnl += pnl
            total_cost += day_cost

            # Calculate ROI (P&L as % of avg trade cost, matching daily tab formula)
            roi = (pnl / avg_trade_cost * 100) if avg_trade_cost > 0 else 0

            # Build day row: Date, Trades, Wins, Losses, Win%, Result, P&L, Avg/Trade, ROI
            day_rows.append({
                "date": ws.title,
                "trades": trades,
                "wins": wins,
                "losses": losses,
                "win_rate": win_rate,
                "result": "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "—"),
                "pnl": pnl,
                "avg_trade_cost": avg_trade_cost,
                "roi": roi
            })

        except Exception as e:
            print(f"[SYNC] Warning: Could not read {ws.title}: {e}")
            continue

    # Calculate overall stats
    overall_win_rate = (total_wins / total_trades * 100) if total_trades > 0 else 0
    overall_avg_trade_cost = total_cost / total_trades if total_trades > 0 else 0
    overall_roi = (total_pnl / overall_avg_trade_cost * 100) if overall_avg_trade_cost > 0 else 0

    # Calculate next update time
    now_pst = datetime.now(PST)
    minutes_until_next = 15 - (now_pst.minute % 15)
    next_update = now_pst + timedelta(minutes=minutes_until_next)
    next_update_str = next_update.strftime('%H:%M PST')

    # Create/get Summary tab
    try:
        summary_ws = sheet.worksheet("Summary")
        summary_ws.clear()
    except gspread.WorksheetNotFound:
        summary_ws = sheet.add_worksheet("Summary", rows=50, cols=9)
        sheet.reorder_worksheets([summary_ws] + [ws for ws in worksheets])
        print("[SYNC] Created Summary tab")

    # Build all rows
    rows = []

    # Header row
    rows.append(["Date", "Trades", "Wins", "Losses", "Win %", "Result", "P&L", "Avg/Trade", "ROI"])

    # Day rows
    for day in day_rows:
        rows.append([
            day["date"],
            str(day["trades"]),
            str(day["wins"]),
            str(day["losses"]),
            f"{day['win_rate']:.0f}%",
            day["result"],
            f"${day['pnl']:+.2f}",
            f"${day['avg_trade_cost']:.2f}",
            f"{day['roi']:+.1f}%"
        ])

    # Totals row
    rows.append([
        f"📊 TOTAL",
        str(total_trades),
        str(total_wins),
        str(total_losses),
        f"{overall_win_rate:.0f}%",
        "WIN" if total_pnl > 0 else ("LOSS" if total_pnl < 0 else "—"),
        f"${total_pnl:+.2f}",
        f"${overall_avg_trade_cost:.2f}",
        f"{overall_roi:+.1f}%"
    ])

    # Next update row
    rows.append(["", "", "", "", "", "", "", "", f"Next: {next_update_str}"])

    # Write all rows
    summary_ws.update(values=rows, range_name=f"A1:I{len(rows)}")

    # Format header row
    summary_ws.format("A1:I1", {
        "textFormat": {"bold": True},
        "backgroundColor": DARK_GRAY_BG
    })

    # Format day rows based on P&L
    formats = []
    for i, day in enumerate(day_rows):
        row_num = i + 2  # +2 because row 1 is header (1-indexed)
        if day["pnl"] > 0:
            formats.append({"range": f"A{row_num}:I{row_num}", "format": {"backgroundColor": GREEN_BG}})
        elif day["pnl"] < 0:
            formats.append({"range": f"A{row_num}:I{row_num}", "format": {"backgroundColor": RED_BG}})

    # Format totals row
    totals_row = len(day_rows) + 2
    if total_pnl > 0:
        formats.append({"range": f"A{totals_row}:I{totals_row}", "format": {"backgroundColor": GREEN_BG, "textFormat": {"bold": True}}})
    elif total_pnl < 0:
        formats.append({"range": f"A{totals_row}:I{totals_row}", "format": {"backgroundColor": RED_BG, "textFormat": {"bold": True}}})
    else:
        formats.append({"range": f"A{totals_row}:I{totals_row}", "format": {"backgroundColor": GRAY_BG, "textFormat": {"bold": True}}})

    if formats:
        summary_ws.batch_format(formats)

    # Freeze header row
    summary_ws.freeze(rows=1)

    print(f"[SYNC] Summary: {len(day_rows)} days, {total_trades} trades, {total_wins}W/{total_losses}L, ${total_pnl:+.2f}")


def main():
    print("=" * 60)
    print(f"[SYNC] Daily Dashboard Sync - {datetime.now(EST).strftime('%Y-%m-%d %H:%M ET')}")
    print("=" * 60)

    gc = get_sheets_client()

    if len(sys.argv) > 1:
        if sys.argv[1] == "--all":
            # Sync all dates with trades
            dates = get_all_trade_dates(gc)
            print(f"[SYNC] Found {len(dates)} dates with trades")
            for date_str in dates:
                try:
                    sync_day(gc, date_str)
                except Exception as e:
                    print(f"[SYNC] Error syncing {date_str}: {e}")
        else:
            # Sync specific date
            sync_day(gc, sys.argv[1])
    else:
        # Sync today (in EST)
        today = datetime.now(EST).strftime("%Y-%m-%d")
        sync_day(gc, today)

    # Always sync Summary tab after daily syncs
    sync_summary_tab(gc)

    print("=" * 60)
    print(f"[SYNC] Done. Dashboard: https://docs.google.com/spreadsheets/d/{DASHBOARD_SHEET_ID}")
    print("=" * 60)


if __name__ == "__main__":
    main()
