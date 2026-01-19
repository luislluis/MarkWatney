# Phase 4: Observability - Research

**Researched:** 2026-01-19
**Domain:** Logging infrastructure for danger scoring system
**Confidence:** HIGH

## Summary

Phase 4 adds observability for the danger scoring system by extending the existing Google Sheets logging infrastructure. The bot already has a mature logging system (`sheets_logger.py`) with three sheets: Events, Ticks, and Windows. This phase adds:

1. **Danger score to Ticks** - New column in the per-second tick data (LOG-01)
2. **Signal breakdown to hedge events** - Full 5-component breakdown logged when hedge triggers (LOG-02)
3. **Console output** - Display danger score in status line when holding 99c position (LOG-03)

The implementation is straightforward because:
- `buffer_tick()` already accepts arbitrary parameters - just add `danger_score`
- `sheets_log_event()` already logs `danger_score` for hedge events - extend with components
- `log_state()` already has access to danger score via `window_state`

**Primary recommendation:** Extend existing logging functions with additional parameters. Add danger score column to TICKS_HEADERS. Pass danger_result components to hedge event logging.

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| gspread | existing | Google Sheets API | Already integrated in sheets_logger.py |
| google-auth | existing | Authentication | Already integrated |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| None | - | All changes are pure Python | Extends existing infrastructure |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Google Sheets | Local CSV | Sheets enables real-time monitoring from any device |
| Per-tick danger score | Sample every N seconds | Full data better for backtesting threshold tuning |
| JSON in Details column | Separate columns per signal | Separate columns easier to chart in Sheets |

**Installation:**
No new packages needed - uses existing bot infrastructure.

## Architecture Patterns

### Current Logging Architecture
```
sheets_logger.py
  |
  +-- TICKS_HEADERS (12 columns currently)
  |     Timestamp, Window ID, TTL, Status, UP Ask, DN Ask,
  |     UP Pos, DN Pos, BTC, UP Imb, DN Imb, Reason
  |
  +-- buffer_tick() - adds tick to buffer
  +-- flush_ticks() - batch writes to Ticks sheet
  |
  +-- EVENTS_HEADERS (8 columns)
  |     Timestamp, Event, Window ID, Side, Shares, Price, PnL, Details
  |
  +-- log_event() - immediate write to Events sheet
```

### Extended Architecture (Phase 4)
```
sheets_logger.py
  |
  +-- TICKS_HEADERS (13 columns - add danger_score)
  |     Timestamp, Window ID, TTL, Status, UP Ask, DN Ask,
  |     UP Pos, DN Pos, BTC, UP Imb, DN Imb, Danger, Reason
  |
  +-- buffer_tick(..., danger_score=None) - add parameter
  +-- flush_ticks() - unchanged (just more columns)
  |
  +-- EVENTS_HEADERS (8 columns - unchanged, use Details for components)
  +-- log_event() - unchanged (components go in Details JSON)
```

### Pattern 1: Adding Column to Ticks Sheet
**What:** Add danger_score column to tick data
**When to use:** LOG-01 implementation
**Example:**
```python
# sheets_logger.py line 72-85
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
    "Danger",  # NEW - danger score column
    "Reason"
]
```

### Pattern 2: Extending buffer_tick Function
**What:** Add danger_score parameter to buffer_tick
**When to use:** LOG-01 implementation
**Example:**
```python
# sheets_logger.py - buffer_tick function
def buffer_tick(window_id: str, ttc: float, status: str,
                ask_up: float, ask_down: float, up_shares: float, down_shares: float,
                btc_price: float = None, up_imb: float = None, down_imb: float = None,
                danger_score: float = None,  # NEW parameter
                reason: str = "") -> None:
```

### Pattern 3: Logging Signal Components in Details JSON
**What:** Include all 5 signal components in hedge event Details field
**When to use:** LOG-02 implementation
**Example:**
```python
# trading_bot_smart.py - hedge event logging
sheets_log_event("99C_HEDGE", window_state.get('window_id', ''),
               bet_side=bet_side, hedge_side=opposite_side,
               hedge_price=opposite_ask, combined=combined, loss=total_loss,
               danger_score=danger_score,
               # Signal breakdown (all 5 components)
               conf_drop=danger_result.get('confidence_drop', 0),
               conf_component=danger_result.get('confidence_component', 0),
               imbalance=danger_result.get('imbalance', 0),
               imb_component=danger_result.get('imbalance_component', 0),
               velocity=danger_result.get('velocity', 0),
               velocity_component=danger_result.get('velocity_component', 0),
               opponent_ask=danger_result.get('opponent_ask', 0),
               opponent_component=danger_result.get('opponent_component', 0),
               time_remaining=danger_result.get('time_remaining', 0),
               time_component=danger_result.get('time_component', 0))
```

### Pattern 4: Console Danger Score Display
**What:** Show danger score in log_state() output when holding 99c position
**When to use:** LOG-03 implementation
**Example:**
```python
# trading_bot_smart.py - log_state() function
# Current format:
# [HH:MM:SS] STATUS | T-XXXs | BTC:$xxx | UP:XXc DN:XXc | OB:+x.xx/-x.xx | pos:X/X | reason

# New format when 99c position held and danger score available:
# [HH:MM:SS] STATUS | T-XXXs | BTC:$xxx | UP:XXc DN:XXc | OB:+x.xx/-x.xx | D:0.XX | pos:X/X | reason
#                                                                          ^^^^^^^
#                                                              danger score indicator
```

### Anti-Patterns to Avoid
- **Modifying TICKS_HEADERS without updating flush_ticks():** Column mismatch causes data misalignment
- **Adding new sheet for danger data:** Use existing Ticks - don't proliferate sheets
- **Logging danger score every tick regardless of position:** Only meaningful when holding 99c position
- **Verbose console output:** Keep compact - just "D:0.XX" not full signal breakdown

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Column alignment | Manual string formatting | Existing TICKS_HEADERS + row list | Headers define columns, rows must match |
| Batch writes | Individual append_row calls | Existing flush_ticks() batching | 30 writes/sec would hit API limits |
| JSON serialization for Details | Manual string building | json.dumps() via existing log_event | Already handles complex kwargs |
| Retry logic | Custom retry | Existing retry in log_event/flush_ticks | Already has exponential backoff |

**Key insight:** sheets_logger.py is mature with retry logic, batching, and reconnection. Phase 4 only adds data fields, not new mechanisms.

## Common Pitfalls

### Pitfall 1: Column Count Mismatch
**What goes wrong:** Data appears in wrong columns or sheets API errors
**Why it happens:** TICKS_HEADERS has 13 items but row list has 12
**How to avoid:** Count columns in TICKS_HEADERS and verify flush_ticks() row construction matches
**Warning signs:** Danger score appears in "Reason" column

**Verification:**
```python
# TICKS_HEADERS count must equal row list length in flush_ticks()
len(TICKS_HEADERS) == len(row)  # Must be True
```

### Pitfall 2: Missing danger_score Parameter Propagation
**What goes wrong:** danger_score always None in sheets
**Why it happens:** Added to buffer_tick but not passed from trading_bot_smart.py
**How to avoid:** Update ALL call sites:
  - sheets_logger.py: buffer_tick() signature
  - sheets_logger.py: buffer_tick() helper function
  - trading_bot_smart.py: buffer_tick() call in log_state()
**Warning signs:** "Danger" column always empty

### Pitfall 3: Danger Score Logged When Not Applicable
**What goes wrong:** Danger=0.00 logged every second even without 99c position
**Why it happens:** Always passing danger_score to buffer_tick
**How to avoid:** Only pass danger_score when:
  - `window_state.get('capture_99c_fill_notified')` is True
  - `window_state.get('capture_99c_hedged')` is False
**Warning signs:** Lots of 0.00 entries in Danger column

### Pitfall 4: danger_result Not Available at Hedge Event
**What goes wrong:** Signal components are 0 in hedge event log
**Why it happens:** check_99c_capture_hedge() doesn't have access to danger_result
**How to avoid:** Store danger_result in window_state alongside danger_score, or pass it as parameter
**Warning signs:** Hedge events show danger_score but all components are 0

### Pitfall 5: Console Output Too Verbose
**What goes wrong:** Log lines wrap or become unreadable
**Why it happens:** Adding full signal breakdown to every status line
**How to avoid:** Only show total danger score (e.g., "D:0.35"), not components
**Warning signs:** Log lines exceed terminal width

## Code Examples

### LOG-01: Add Danger Score to Ticks Sheet

**sheets_logger.py - Update TICKS_HEADERS:**
```python
# Location: sheets_logger.py lines 72-85
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
    "Danger",  # NEW - Add before Reason
    "Reason"
]
```

**sheets_logger.py - Update buffer_tick():**
```python
# Location: sheets_logger.py lines 309-330
def buffer_tick(self, window_id: str, ttc: float, status: str,
                ask_up: float, ask_down: float, up_shares: float, down_shares: float,
                btc_price: float = None, up_imb: float = None, down_imb: float = None,
                danger_score: float = None,  # NEW parameter
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
        "danger_score": danger_score,  # NEW field
        "reason": reason
    })
```

**sheets_logger.py - Update flush_ticks() row construction:**
```python
# Location: sheets_logger.py lines 341-356
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
        f"{t['danger_score']:.2f}" if t["danger_score"] is not None else "",  # NEW
        t["reason"]
    ])
```

**sheets_logger.py - Update helper function:**
```python
# Location: sheets_logger.py lines 423-434
def buffer_tick(window_id: str, ttc: float, status: str,
                ask_up: float, ask_down: float, up_shares: float, down_shares: float,
                btc_price: float = None, up_imb: float = None, down_imb: float = None,
                danger_score: float = None,  # NEW parameter
                reason: str = "") -> None:
    """
    Buffer a per-second tick for batch upload.
    Called from log_state() every second.
    """
    if _logger is None or not _logger.enabled:
        return
    _logger.buffer_tick(window_id, ttc, status, ask_up, ask_down, up_shares, down_shares,
                        btc_price, up_imb, down_imb, danger_score, reason)  # NEW arg
```

**trading_bot_smart.py - Update log_state() call:**
```python
# Location: trading_bot_smart.py lines 803-808
# Get danger score if holding 99c position
danger_for_log = None
if window_state.get('capture_99c_fill_notified') and not window_state.get('capture_99c_hedged'):
    danger_for_log = window_state.get('danger_score')

# Buffer tick for Google Sheets (batched upload)
buffer_tick(
    window_state.get('window_id', ''),
    ttc, status, ask_up, ask_down, up_shares, down_shares,
    btc_price=btc_price, up_imb=up_imb, down_imb=down_imb,
    danger_score=danger_for_log,  # NEW parameter
    reason=reason
)
```

### LOG-02: Log Hedge Events with Signal Breakdown

**trading_bot_smart.py - Store danger_result in window_state:**
```python
# Location: trading_bot_smart.py around line 2526
danger_result = calculate_danger_score(
    current_confidence=current_confidence,
    peak_confidence=window_state.get('capture_99c_peak_confidence', 0),
    our_imbalance=our_imbalance,
    btc_price_history=btc_price_history,
    opponent_ask=opponent_ask,
    time_remaining=remaining_secs,
    bet_side=bet_side
)
window_state['danger_score'] = danger_result['score']
window_state['danger_result'] = danger_result  # NEW - store full result for logging
```

**trading_bot_smart.py - Enhanced hedge event logging:**
```python
# Location: trading_bot_smart.py lines 1392-1395
danger_result = window_state.get('danger_result', {})
sheets_log_event("99C_HEDGE", window_state.get('window_id', ''),
               bet_side=bet_side, hedge_side=opposite_side,
               hedge_price=opposite_ask, combined=combined, loss=total_loss,
               danger_score=danger_score,
               # Signal breakdown - all 5 raw values and weighted components
               conf_drop=danger_result.get('confidence_drop', 0),
               conf_wgt=danger_result.get('confidence_component', 0),
               imb_raw=danger_result.get('imbalance', 0),
               imb_wgt=danger_result.get('imbalance_component', 0),
               vel_raw=danger_result.get('velocity', 0),
               vel_wgt=danger_result.get('velocity_component', 0),
               opp_raw=danger_result.get('opponent_ask', 0),
               opp_wgt=danger_result.get('opponent_component', 0),
               time_raw=danger_result.get('time_remaining', 0),
               time_wgt=danger_result.get('time_component', 0))
```

### LOG-03: Console Danger Score Display

**trading_bot_smart.py - Update log_state() print statement:**
```python
# Location: trading_bot_smart.py lines 799-800
# Build danger score indicator if applicable
danger_str = ""
if window_state.get('capture_99c_fill_notified') and not window_state.get('capture_99c_hedged'):
    ds = window_state.get('danger_score', 0)
    danger_str = f" | D:{ds:.2f}"

price_str = f"UP:{ask_up*100:2.0f}c DN:{ask_down*100:2.0f}c"
print(f"[{ts()}] {status:7} | T-{ttc:3.0f}s | {btc_str}{price_str}{ob_str}{danger_str} | pos:{up_shares:.0f}/{down_shares:.0f} | {reason}")
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| No danger visibility | Danger score logged to Ticks | This phase | Enables threshold tuning via historical analysis |
| Sparse hedge logging | Full signal breakdown | This phase | Post-mortem analysis of why hedges triggered |
| Blind console during 99c | Danger score visible | This phase | Real-time awareness of position risk |

**Deprecated/outdated:**
- None - this phase adds logging, doesn't replace existing

## Verification Checklist

| Requirement | Verification Command | Expected |
|-------------|---------------------|----------|
| LOG-01 | `grep "Danger" sheets_logger.py` | "Danger" in TICKS_HEADERS |
| LOG-01 | `grep "danger_score" sheets_logger.py` | Parameter in buffer_tick |
| LOG-02 | `grep "conf_drop\|imb_raw\|vel_raw" trading_bot_smart.py` | In 99C_HEDGE log call |
| LOG-03 | `grep "D:{ds:" trading_bot_smart.py` | In log_state print statement |

## Open Questions

1. **Should existing Ticks sheet header row be updated?**
   - Google Sheets won't auto-update existing header
   - Recommendation: Manual update or note in deployment instructions
   - Impact: Minor - data will still write correctly, just column names may not match

2. **What format for danger score in Ticks?**
   - Options: 0.35, 35%, 0.350
   - Recommendation: 0.35 (2 decimal places, consistent with other percentages)

3. **Should all hedge events include breakdown, or only 99C_HEDGE?**
   - EARLY_BAIL, EARLY_HEDGE also exist
   - Recommendation: Only 99C_HEDGE for now - others don't use danger scoring
   - Can extend later if needed

## Sources

### Primary (HIGH confidence)
- `/Users/luislluis/MarkWatney/sheets_logger.py` - Direct code analysis
  - Lines 44-85: EVENTS_HEADERS, WINDOWS_HEADERS, TICKS_HEADERS definitions
  - Lines 309-330: buffer_tick() method
  - Lines 332-377: flush_ticks() method with row construction
  - Lines 423-434: buffer_tick() helper function
- `/Users/luislluis/MarkWatney/trading_bot_smart.py` - Direct code analysis
  - Lines 760-808: log_state() function with buffer_tick call
  - Lines 1392-1395: sheets_log_event for 99C_HEDGE
  - Lines 1477-1489: calculate_danger_score() return structure
  - Lines 2517-2526: Main loop danger score calculation
- `/Users/luislluis/MarkWatney/.planning/REQUIREMENTS.md` - LOG-01, LOG-02, LOG-03 specifications

### Secondary (MEDIUM confidence)
- `/Users/luislluis/MarkWatney/.planning/phases/03-hedge-execution/03-RESEARCH.md` - Prior phase context

### Tertiary (LOW confidence)
- None

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - extends existing, well-understood infrastructure
- Architecture: HIGH - follows established patterns in sheets_logger.py
- Pitfalls: HIGH - derived from direct code analysis of data flow

**Research date:** 2026-01-19
**Valid until:** 90 days (stable logging infrastructure, no external API changes expected)
