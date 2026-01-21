---
phase: 02-position-detection
plan: 01
subsystem: api
tags: [polymarket, positions, wallet, data-api]

# Dependency graph
requires:
  - phase: 01-core-infrastructure
    provides: Main loop, window state dict, logging infrastructure
provides:
  - Position fetching via Polymarket data-api
  - Token ID extraction from market data
  - Trade type detection (ARB/99C_CAPTURE/NO_TRADE)
  - Real-time position display in status line
affects: [02-position-detection/02, 02-position-detection/03, 03-google-sheet]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Position polling each second in main loop"
    - "Token ID caching per window (fetch once)"
    - "Trade type detection based on share balance"

key-files:
  created: []
  modified:
    - performance_tracker.py

key-decisions:
  - "Poll positions every second for real-time tracking"
  - "Cache token IDs once per window (minimize API calls)"
  - "Use simple heuristic: both sides = ARB, single side = 99C_CAPTURE"

patterns-established:
  - "Position API: https://data-api.polymarket.com/positions?user={wallet}"
  - "Token ID extraction: market['markets'][0]['clobTokenIds']"
  - "Status line format: [HH:MM:SS] T-XXXs | UP:X.X DN:X.X | slug"

# Metrics
duration: 5min
completed: 2026-01-20
---

# Phase 2 Plan 01: Position Fetching Summary

**Position fetching and trade type detection for real-time ARB and 99c capture tracking**

## Performance

- **Duration:** 5 min
- **Started:** 2026-01-20T20:01:00Z
- **Completed:** 2026-01-20T20:06:00Z
- **Tasks:** 3
- **Files modified:** 1

## Accomplishments
- Added position fetching via Polymarket data-api
- Implemented trade type detection (ARB, 99C_CAPTURE, NO_TRADE)
- Integrated position polling into main loop with real-time status display
- Token IDs cached per window to minimize API calls

## Task Commits

Each task was committed atomically:

1. **Tasks 1-2: Position functions + trade detection** - `4020ccb` (feat)
2. **Task 3: Main loop integration** - `a113eb7` (feat)

## Files Created/Modified
- `performance_tracker.py` - Added get_token_ids(), fetch_positions(), detect_trade_type() and main loop integration

## Decisions Made
- Poll positions every second (consistent with trading bot observation cadence)
- Cache token IDs once per window rather than re-extracting each tick
- Use simple position-based heuristic for trade type: both sides = ARB, single side = 99C_CAPTURE (price not available from position API)

## Deviations from Plan
None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Position detection complete and working
- window_state.arb_entry and window_state.capture_entry populated when positions detected
- Ready for Plan 02: P/L calculation (need entry price tracking)
- Ready for Plan 03: Window outcome detection (need resolution API)

---
*Phase: 02-position-detection*
*Completed: 2026-01-20*
