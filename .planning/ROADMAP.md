# Roadmap: v2.0 Performance Tracker

**Goal:** Build a standalone dashboard bot that grades every trading window in real-time.

**Phases:** 3
**Requirements:** 14

---

## Phase 1: Core Bot Infrastructure

**Goal:** Standalone bot that monitors BTC 15-min windows and runs the main loop.

**Requirements covered:**
- CORE-01: Bot runs as standalone process on server
- CORE-02: Bot monitors BTC 15-min windows in real-time
- CORE-03: Bot detects window boundaries (start/end)
- CORE-04: Bot writes graded row after each window closes

**Success criteria:**
- [ ] Bot starts and runs continuously
- [ ] Correctly identifies current window and time remaining
- [ ] Detects window transitions (old window ends, new begins)
- [ ] Skeleton row written to console on each window close

**Plans:** 3 plans

Plans:
- [ ] 01-01-PLAN.md — Bot skeleton with main loop, TeeLogger, graceful shutdown
- [ ] 01-02-PLAN.md — Window detection (slug calculation, market data, time remaining)
- [ ] 01-03-PLAN.md — Window transitions and graded row output skeleton

**Depends on:** Nothing (first phase)

---

## Phase 2: Position Detection

**Goal:** Detect what trades happened and grade their outcomes.

**Requirements covered:**
- POS-01: Detect ARB entries (bought both UP and DOWN)
- POS-02: Detect ARB completion status (paired, lopsided, bail)
- POS-03: Detect 99c capture entries
- POS-04: Detect 99c capture outcomes (win/loss)
- POS-05: Calculate P/L for each trade type

**Success criteria:**
- [ ] Detects when wallet has positions in current window
- [ ] Distinguishes ARB trades from 99c capture trades
- [ ] Correctly grades ARB outcome (WIN, BAIL, LOSS)
- [ ] Correctly grades 99c outcome (WIN, LOSS)
- [ ] Calculates accurate P/L for each trade

**Depends on:** Phase 1 (window detection)

---

## Phase 3: Google Sheet Dashboard

**Goal:** Beautiful, color-coded Google Sheet with real-time updates.

**Requirements covered:**
- SHEET-01: Create/connect to dedicated performance sheet
- SHEET-02: Write window rows with all columns
- SHEET-03: Color code cells (green/red)
- SHEET-04: Use emoji indicators (✓, ✗, ⚠, —)
- SHEET-05: Maintain summary row at top with totals and win rates

**Success criteria:**
- [ ] New Google Sheet created and connected
- [ ] Each window writes a formatted row
- [ ] Green cells for wins/profits, red for losses
- [ ] Emoji indicators display correctly
- [ ] Summary row updates with running totals and win rates

**Depends on:** Phase 2 (position detection provides the data)

---

## Phase Summary

| Phase | Name | Requirements | Depends On |
|-------|------|--------------|------------|
| 1 | Core Bot Infrastructure | CORE-01 to CORE-04 | — |
| 2 | Position Detection | POS-01 to POS-05 | Phase 1 |
| 3 | Google Sheet Dashboard | SHEET-01 to SHEET-05 | Phase 2 |

**Total:** 3 phases, 14 requirements, linear dependency chain

---
*Roadmap created: 2026-01-20*
*Phase 1 planned: 2026-01-20*
