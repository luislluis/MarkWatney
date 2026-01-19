---
phase: 02-danger-scoring-engine
plan: 01
subsystem: trading
tags: [danger-score, weighted-signals, velocity, confidence, order-book]

# Dependency graph
requires:
  - phase: 01-tracking-infrastructure
    provides: btc_price_history deque for velocity, capture_99c_peak_confidence tracking
provides:
  - DANGER_THRESHOLD and 5 DANGER_WEIGHT_* constants
  - get_price_velocity() helper function
  - calculate_danger_score() function with 5-signal formula
  - Main loop integration storing danger_score in window_state
affects: [03-hedge-logic, 04-logging-observability]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Pure function danger scoring with dict return for logging components"
    - "Weighted signal combination with configurable constants"

key-files:
  created: []
  modified:
    - trading_bot_smart.py

key-decisions:
  - "Danger score uncapped - values >1.0 indicate very dangerous situations"
  - "Default imbalance=0 when analyzer unavailable (neutral)"
  - "Default opponent_ask=0.50 when no asks available (neutral)"

patterns-established:
  - "Signal component pattern: each signal returns raw value and weighted component separately"
  - "Velocity direction normalization: positive always means danger regardless of bet side"

# Metrics
duration: 2min
completed: 2026-01-19
---

# Phase 2 Plan 1: Danger Scoring Engine Summary

**Weighted 5-signal danger scoring with configurable threshold (0.40) and weights for confidence drop (3.0), order book imbalance (0.4), BTC velocity (2.0), opponent ask (0.5), and time decay (0.3)**

## Performance

- **Duration:** 2 min
- **Started:** 2026-01-19T20:46:37Z
- **Completed:** 2026-01-19T20:48:36Z
- **Tasks:** 3
- **Files modified:** 1

## Accomplishments
- Added 6 danger scoring constants (threshold + 5 weights)
- Created get_price_velocity() helper for BTC movement detection
- Created calculate_danger_score() function combining 5 weighted signals
- Integrated danger scoring into main loop for every tick while holding 99c position

## Task Commits

Each task was committed atomically:

1. **Task 1: Add danger scoring constants and helper function** - `d203e23` (feat)
2. **Task 2: Create calculate_danger_score() function** - `3f66bb3` (feat)
3. **Task 3: Integrate danger scoring into main loop** - `d404c99` (feat)

## Files Created/Modified
- `trading_bot_smart.py` - Added danger scoring constants, get_price_velocity(), calculate_danger_score(), and main loop integration

## Decisions Made
None - followed plan as specified

## Deviations from Plan
None - plan executed exactly as written

## Issues Encountered
None

## User Setup Required
None - no external service configuration required

## Next Phase Readiness
- Danger score calculated every tick when holding 99c position
- Score stored in window_state['danger_score']
- Ready for Phase 3 (hedge logic) to use danger_score to trigger protective hedges
- Ready for Phase 4 (logging) to log danger score components

---
*Phase: 02-danger-scoring-engine*
*Completed: 2026-01-19*
