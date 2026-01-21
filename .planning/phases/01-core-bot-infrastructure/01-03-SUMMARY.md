---
phase: 01-core-bot-infrastructure
plan: 03
subsystem: monitoring
tags: [python, window-grading, state-management, window-transitions]

# Dependency graph
requires: [01-01, 01-02]
provides:
  - Window state management via reset_window_state()
  - Graded row output via grade_window()
  - Window transition detection triggers grading
  - Immediate grading at window close (T-0)
affects: [phase-2, sheet-dashboard]

# Tech tracking
tech-stack:
  added: []
  patterns: [window state dictionary, graded flag to prevent double-grading, slug-based time extraction]

key-files:
  created: []
  modified: [performance_tracker.py]

key-decisions:
  - "Window state as dict with all tracking fields (arb_entry, arb_result, arb_pnl, capture_entry, capture_result, capture_pnl)"
  - "Grade immediately at T-0 with 3-second delay, not waiting for slug change"
  - "graded flag prevents double-grading on window transition"

patterns-established:
  - "reset_window_state(slug) initializes fresh state for new window"
  - "grade_window(state) outputs formatted summary to console"
  - "WINDOW GRADED banner with all P/L columns"

# Metrics
duration: 2min
completed: 2026-01-20
---

# Phase 01 Plan 03: Grading Logic Summary

**Window transition detection with graded row output skeleton - grades at T-0 with placeholder data ready for Phase 2 position detection**

## Performance

- **Duration:** 2 min
- **Started:** 2026-01-21T03:36:31Z
- **Completed:** 2026-01-21T03:38:45Z
- **Tasks:** 3
- **Files modified:** 1

## Accomplishments
- Added window state management with all tracking fields for Phase 2
- Implemented grade_window() function with formatted console output
- Window transitions now trigger grading of completed window
- Immediate grading at T-0 with 3-second settlement delay
- graded flag prevents double-grading

## Task Commits

Each task was committed atomically:

1. **Task 1: Add window state management** - `bc64de2` (feat)
2. **Task 2: Add grade_window and transition handling** - `c3cd0fd` (feat)
3. **Task 3: Add immediate window end grading** - `4ec4e83` (feat)

## Files Created/Modified
- `performance_tracker.py` - Added window state management, grade_window(), and transition handling

## Key Functions Added

| Function | Purpose |
|----------|---------|
| `reset_window_state(slug)` | Initialize fresh window state dict |
| `grade_window(state)` | Output formatted graded row to console |

## Window State Structure

```python
{
    'slug': 'btc-updown-15m-XXXXXXXXXX',
    'started_at': datetime,
    'arb_entry': None,       # {'up_shares': X, 'down_shares': X, 'cost': X}
    'arb_result': None,      # 'PAIRED', 'BAIL', 'LOPSIDED'
    'arb_pnl': 0.0,
    'capture_entry': None,   # {'side': 'UP'/'DOWN', 'shares': X, 'cost': X}
    'capture_result': None,  # 'WIN', 'LOSS'
    'capture_pnl': 0.0,
    'window_end_price': None,
    'outcome': None,         # 'UP' or 'DOWN'
    'graded': False,
}
```

## Graded Row Format

```
============================================================
WINDOW GRADED: btc-updown-15m-1737417600
============================================================
  Time:        16:00
  ARB Entry:   -
  ARB Result:  None
  ARB P/L:     $+0.00
  99c Entry:   -
  99c Result:  None
  99c P/L:     $+0.00
  -----------
  TOTAL P/L:   $+0.00
============================================================
```

## Decisions Made
- Grade immediately at T-0 (not waiting for slug change) for responsive output
- 3-second delay before grading to allow for settlement
- Global window_state for tracking across main loop iterations
- graded flag in state dict prevents double-grading on transition

## Deviations from Plan
None - plan executed exactly as written.

## Issues Encountered
None.

## Verification Results
1. Window state tracking: PASS - all fields present
2. Graded row columns: PASS - all columns displayed
3. Placeholder values: PASS - all values are - or $0.00
4. No double-grading: PASS - graded flag works
5. grade_window function: PASS - outputs formatted row

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Core bot infrastructure complete (CORE-01 through CORE-04)
- Phase 2 (Position Detection) can now populate window state
- State structure ready for ARB and 99c capture tracking

---
*Phase: 01-core-bot-infrastructure*
*Plan: 03*
*Completed: 2026-01-20*
