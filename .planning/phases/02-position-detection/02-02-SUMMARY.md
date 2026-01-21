---
phase: 02-position-detection
plan: 02
subsystem: api
tags: [polymarket, market-resolution, grading, pnl, clob-api]

# Dependency graph
requires:
  - phase: 02-position-detection/01
    provides: Position fetching, trade type detection, window state with arb_entry/capture_entry
provides:
  - Market resolution via CLOB API
  - ARB trade grading (PAIRED/LOPSIDED/BAIL)
  - 99c capture grading (WIN/LOSS)
  - P/L calculation for both trade types
affects: [02-position-detection/03, 03-google-sheet]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Market resolution via clob.polymarket.com/markets/{conditionId}"
    - "ARB grading based on share balance (PAIRED < 0.5 diff)"
    - "Estimated entry prices for P/L (42c cheap, 57c expensive)"

key-files:
  created: []
  modified:
    - performance_tracker.py

key-decisions:
  - "Use estimated entry prices (42c/57c for ARB, 99c for capture) since position API doesn't provide them"
  - "ARB P/L depends on winning side to determine which was cheap vs expensive"
  - "Grade ARB as PAIRED when share diff < 0.5 (matches trading bot's MICRO_IMBALANCE_TOLERANCE)"

patterns-established:
  - "Resolution API pattern: get_condition_id() -> get_market_resolution()"
  - "Grading functions return dict: {'result': str, 'pnl': float}"
  - "grade_window() accepts market parameter for resolution lookup"

# Metrics
duration: 7min
completed: 2026-01-20
---

# Phase 2 Plan 02: P/L Calculation Summary

**Market resolution checking and outcome grading with P/L calculation for ARB and 99c capture trades**

## Performance

- **Duration:** 7 min
- **Started:** 2026-01-20T20:03:00Z
- **Completed:** 2026-01-20T20:10:00Z
- **Tasks:** 3
- **Files modified:** 1

## Accomplishments
- Added market resolution checking via Polymarket CLOB API
- Implemented ARB trade grading (PAIRED/LOPSIDED/BAIL) with P/L calculation
- Implemented 99c capture grading (WIN/LOSS) with P/L calculation
- Wired grading into grade_window() with actual resolution and results

## Task Commits

Each task was committed atomically:

1. **Task 1: Add market resolution function** - `9f059d2` (feat)
2. **Task 2: Add ARB and 99c grading functions** - `5f61f12` (feat)
3. **Task 3: Wire grading into grade_window** - `524168f` (feat)

## Files Created/Modified
- `performance_tracker.py` - Added get_condition_id(), get_market_resolution(), classify_arb_result(), grade_arb_trade(), grade_99c_trade(), and updated grade_window() to use them

## Decisions Made
- Use estimated entry prices (42c cheap side, 57c expensive side for ARB; 99c for capture) since position API doesn't provide actual entry prices
- ARB P/L calculation determines cheap/expensive side based on winning side (the side that won was expensive, the losing side was cheap)
- PAIRED threshold is < 0.5 share difference (consistent with trading bot's MICRO_IMBALANCE_TOLERANCE)

## Deviations from Plan
None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Market resolution and grading complete
- POS-02 COMPLETE: Detects ARB completion status (PAIRED, LOPSIDED, BAIL)
- POS-04 COMPLETE: Detects 99c capture outcomes (WIN/LOSS)
- POS-05 COMPLETE: Calculates P/L for each trade type
- Phase 2 Position Detection 100% complete (all 5 requirements satisfied)
- Ready for Phase 3 (Google Sheet Dashboard)

---
*Phase: 02-position-detection*
*Completed: 2026-01-20*
