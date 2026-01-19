# Roadmap: Smart Hedge System

## Overview

This enhancement adds a multi-signal danger scoring system to the existing 99c capture strategy. The system tracks position state, calculates a weighted danger score from 5 signals, triggers hedges when danger exceeds threshold, and logs all decisions for analysis. Four phases build incrementally: tracking infrastructure, scoring engine, hedge execution, and observability.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3, 4): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

- [x] **Phase 1: Tracking Infrastructure** - State tracking for peak confidence and price velocity
- [x] **Phase 2: Danger Scoring Engine** - Calculate composite danger score from 5 weighted signals
- [x] **Phase 3: Hedge Execution** - Trigger and place hedge orders when danger threshold exceeded
- [ ] **Phase 4: Observability** - Logging to Google Sheets and console visibility

## Phase Details

### Phase 1: Tracking Infrastructure
**Goal**: Bot tracks the state needed for danger score calculation
**Depends on**: Nothing (first phase)
**Requirements**: TRACK-01, TRACK-02, TRACK-03, CFG-03
**Success Criteria** (what must be TRUE):
  1. When 99c capture fills, bot records the confidence at fill as peak confidence
  2. Bot maintains rolling 5-second window of BTC prices for velocity calculation
  3. window_state contains danger_score field (initially 0) when position is held
  4. Velocity window size is configurable via constant (default 5 seconds)
**Plans**: 1 plan

Plans:
- [x] 01-01-PLAN.md - Add tracking infrastructure (constants, state fields, tracking logic)

### Phase 2: Danger Scoring Engine
**Goal**: Bot calculates danger score every tick when holding 99c position
**Depends on**: Phase 1
**Requirements**: SCORE-01, SCORE-02, SCORE-03, SCORE-04, SCORE-05, SCORE-06, CFG-01, CFG-02
**Success Criteria** (what must be TRUE):
  1. Every tick while holding 99c position, a danger score (0.0-1.0) is calculated
  2. Danger score incorporates all 5 signals: confidence drop, order book imbalance, price velocity, opponent ask, time decay
  3. Each signal weight is configurable via constants
  4. Danger threshold is configurable (default 0.40)
**Plans**: 1 plan

Plans:
- [x] 02-01-PLAN.md - Add danger scoring constants, functions (get_price_velocity, calculate_danger_score), and main loop integration

### Phase 3: Hedge Execution
**Goal**: Bot executes hedge orders when danger threshold is exceeded
**Depends on**: Phase 2
**Requirements**: HEDGE-01, HEDGE-02, HEDGE-03, HEDGE-04, HEDGE-05
**Success Criteria** (what must be TRUE):
  1. When danger score >= 0.40, bot places order to buy opposite side at market
  2. Hedge shares equal original 99c capture fill shares
  3. Hedge only triggers once per position (no double-hedging)
  4. Hedge respects max price limit (opposite side ask < 50c)
  5. Hedge uses existing place_and_verify_order() with retries
**Plans**: 1 plan

Plans:
- [x] 03-01-PLAN.md - Replace confidence threshold with danger score trigger in check_99c_capture_hedge()

### Phase 4: Observability
**Goal**: All hedge decisions are logged with full signal breakdown
**Depends on**: Phase 3
**Requirements**: LOG-01, LOG-02, LOG-03
**Success Criteria** (what must be TRUE):
  1. Google Sheets Ticks tab includes danger_score column
  2. Hedge events logged to Events tab with all 5 signal values
  3. Console output shows danger score when holding 99c position
**Plans**: 1 plan

Plans:
- [ ] 04-01-PLAN.md - Add danger score to Ticks, signal breakdown to hedge events, console D:X.XX display

## Progress

**Execution Order:**
Phases execute in numeric order: 1 -> 2 -> 3 -> 4

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Tracking Infrastructure | 1/1 | Complete | 2026-01-19 |
| 2. Danger Scoring Engine | 1/1 | Complete | 2026-01-19 |
| 3. Hedge Execution | 1/1 | Complete | 2026-01-19 |
| 4. Observability | 0/1 | Planned | - |

---
*Roadmap created: 2026-01-19*
