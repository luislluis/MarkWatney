# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-01-20)

**Core value:** See trading performance at a glance with real-time grading of every window.
**Current focus:** v2.0 Performance Tracker — Phase 3 in progress

## Current Position

Milestone: v2.0 Performance Tracker
Phase: 3 of 3 (Google Sheet Dashboard)
Plan: 2 of 4 complete
Status: In progress
Last activity: 2026-01-20 — Completed 03-02-PLAN.md (Row Formatting and Summary)

Progress: [███████░░░] 78% (7/9 plans)

## Roadmap Overview

| Phase | Name | Requirements | Status |
|-------|------|--------------|--------|
| 1 | Core Bot Infrastructure | CORE-01 to CORE-04 | COMPLETE (3/3 plans) |
| 2 | Position Detection | POS-01 to POS-05 | COMPLETE (2/2 plans) |
| 3 | Google Sheet Dashboard | SHEET-01 to SHEET-05 | IN PROGRESS (2/4 plans) |

## Accumulated Context

### Decisions Made

| Decision | Rationale |
|----------|-----------|
| Separate bot | Clean separation, no risk to trading |
| Own Google Sheet | Fresh dashboard, not cluttering trading logs |
| Watch positions via API | Most reliable way to detect actual trades |
| Skip research | Same tech stack as trading bot |
| TeeLogger pattern from trading bot | Consistency across bots |
| Separate log file (tracker.log) | Avoid mixing with trading bot logs |
| Cache market data per window | Minimize API calls (1 per window, not per second) |
| Slug calculation from Unix timestamp | Consistent window identification |
| Grade at T-0 with 3s delay | More responsive than waiting for slug change |
| graded flag in state dict | Prevents double-grading on window transition |
| Window state as dict | All tracking fields ready for Phase 2 |
| Poll positions every second | Real-time tracking matches trading bot observation cadence |
| Cache token IDs per window | Extract once from market data, minimize parsing |
| Simple trade type heuristic | Both sides = ARB, single side = 99C_CAPTURE |
| Estimated entry prices for P/L | 42c cheap, 57c expensive for ARB; 99c for capture |
| ARB PAIRED threshold < 0.5 | Matches trading bot's MICRO_IMBALANCE_TOLERANCE |
| Combined tasks for cohesive modules | Tasks 1+2 both needed for functional module |
| Same gspread patterns as sheets_logger.py | Consistency across codebase |
| Summary row at fixed row 2 | Simpler updates vs dynamically moving |
| parse_pnl handles multiple formats | Robust parsing of $+0.05, $-0.10, em dash, empty |
| Gray background for zero P/L | Visual distinction for break-even |
| Full recalculation on summary update | Simpler and more reliable than incremental |

### Pending Todos

None.

### Blockers/Concerns

- Trading bot currently stopped (out of gas) — need MATIC to resume trading
- Performance tracker can still be built and tested

## Session Continuity

Last session: 2026-01-20 20:27 PST
Stopped at: Completed 03-02-PLAN.md (Row Formatting and Summary)
Resume: `/gsd:execute-plan 03-03` for testing or `/gsd:execute-plan 03-04` for cleanup
