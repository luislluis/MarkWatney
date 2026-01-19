# Requirements: Smart Hedge System

**Defined:** 2026-01-19
**Core Value:** Preserve 99c capture profits by limiting losses to small controlled amounts instead of total loss.

## v1 Requirements

Requirements for the smart hedge system. Each maps to roadmap phases.

### Danger Scoring

- [x] **SCORE-01**: System calculates danger score every tick when 99c position is held
- [x] **SCORE-02**: Score incorporates confidence drop from peak (weight: 3.0)
- [x] **SCORE-03**: Score incorporates order book imbalance on our side (weight: 0.4)
- [x] **SCORE-04**: Score incorporates 5-second price velocity (weight: 2.0)
- [x] **SCORE-05**: Score incorporates opponent ask price (weight: 0.5)
- [x] **SCORE-06**: Score incorporates time decay in final 60 seconds (weight: 0.3)

### Hedge Trigger

- [x] **HEDGE-01**: Hedge triggers when danger score >= 0.40
- [x] **HEDGE-02**: Hedge buys opposite side at market (take the ask)
- [x] **HEDGE-03**: Hedge only triggers once per position (no double-hedging)
- [x] **HEDGE-04**: Hedge respects existing max price limits (opposite side < 50c)
- [x] **HEDGE-05**: Hedge shares must equal original 99c capture fill shares

### Tracking & State

- [x] **TRACK-01**: Track peak confidence for each 99c position
- [x] **TRACK-02**: Track rolling 5-second price history for velocity calculation
- [x] **TRACK-03**: Store danger score in window_state for logging

### Observability

- [x] **LOG-01**: Log danger score to Google Sheets Ticks (new column)
- [x] **LOG-02**: Log hedge events with full signal breakdown (all 5 components)
- [x] **LOG-03**: Console output shows danger score when position held

### Configuration

- [x] **CFG-01**: Danger threshold configurable via constant (default 0.40)
- [x] **CFG-02**: Individual signal weights configurable via constants
- [x] **CFG-03**: Velocity window configurable (default 5 seconds)

## v2 Requirements

Deferred to future release. Not in current roadmap.

### Analytics

- **ANALYTICS-01**: Backtesting framework to test threshold changes against historical data
- **ANALYTICS-02**: Dashboard showing hedge effectiveness over time
- **ANALYTICS-03**: Alerting when hedge rate exceeds expected threshold

### Advanced Hedging

- **ADV-01**: Partial hedges (hedge 50% at score 0.35, 100% at 0.50)
- **ADV-02**: Adaptive thresholds based on market volatility
- **ADV-03**: Alternative hedge via selling position instead of buying opposite

## Out of Scope

| Feature | Reason |
|---------|--------|
| Machine learning thresholds | Complexity not justified; tune manually first |
| Real-time threshold adjustment | Keep it simple for v1; constant threshold |
| Multiple hedge levels | Adds complexity; binary hedge/don't is clearer |
| Selling position to hedge | Buying opposite is simpler and guarantees locked loss |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| TRACK-01 | Phase 1 | Complete |
| TRACK-02 | Phase 1 | Complete |
| TRACK-03 | Phase 1 | Complete |
| CFG-03 | Phase 1 | Complete |
| SCORE-01 | Phase 2 | Complete |
| SCORE-02 | Phase 2 | Complete |
| SCORE-03 | Phase 2 | Complete |
| SCORE-04 | Phase 2 | Complete |
| SCORE-05 | Phase 2 | Complete |
| SCORE-06 | Phase 2 | Complete |
| CFG-01 | Phase 2 | Complete |
| CFG-02 | Phase 2 | Complete |
| HEDGE-01 | Phase 3 | Complete |
| HEDGE-02 | Phase 3 | Complete |
| HEDGE-03 | Phase 3 | Complete |
| HEDGE-04 | Phase 3 | Complete |
| HEDGE-05 | Phase 3 | Complete |
| LOG-01 | Phase 4 | Complete |
| LOG-02 | Phase 4 | Complete |
| LOG-03 | Phase 4 | Complete |

**Coverage:**
- v1 requirements: 18 total
- Mapped to phases: 18
- Unmapped: 0

---
*Requirements defined: 2026-01-19*
*Last updated: 2026-01-19 after Phase 4 completion*
