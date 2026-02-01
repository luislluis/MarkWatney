---
title: "Fix Google Sheets API Quota Exceeded"
date: 2026-02-01
status: decided
decision: Increase tick flush interval from 30s to 60s to reduce API calls
---

# Fix Google Sheets API Quota Exceeded

## What We're Solving

The bot is hitting Google Sheets API rate limits (429 errors), causing CAPTURE_FILL and other events to fail logging. This results in daily dashboard tabs (like 2026-02-01) showing "NO TRADES" despite actual trades occurring.

Error message:
> Quota exceeded for quota metric 'Write requests' and limit 'Write requests per minute per user' of service 'sheets.googleapis.com'

## Root Cause

- **Google Sheets limit**: 60 write requests per minute per user
- **Current tick flush**: Every 30 seconds (~2 writes/min just for ticks)
- **Events**: Each event is an immediate `append_row()` call
- **Combined**: Tick batches + multiple events per window exceed quota

## Decision: Increase Tick Flush Interval

Change `TICK_FLUSH_INTERVAL` from 30 seconds to 60 seconds.

### Why This Approach

| Alternative | Rejected Because |
|-------------|------------------|
| Buffer events like ticks | More complex, delays event visibility |
| Prioritize events over ticks | Complex logic, might lose tick data |
| **Reduce tick frequency** | **Simple one-line change, leaves quota for events** |

### Impact

- Tick data slightly less granular (60s batches vs 30s)
- More headroom for event writes
- Events remain real-time

## Implementation

Single change in `sheets_logger.py`:

```python
# Line 111
TICK_FLUSH_INTERVAL = 60  # Was 30, now 60 to reduce API quota usage
```

## Future Consideration

If quota issues persist, consider buffering events like ticks (batch multiple events into single write).
