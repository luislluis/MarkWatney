# Requirements: Performance Tracker Bot

**Defined:** 2026-01-20
**Core Value:** See trading performance at a glance with real-time grading of every window

## v1 Requirements

### Core Bot

- [x] **CORE-01**: Bot runs as standalone process on server
- [x] **CORE-02**: Bot monitors BTC 15-min windows in real-time
- [x] **CORE-03**: Bot detects window boundaries (start/end)
- [x] **CORE-04**: Bot writes graded row after each window closes

### Position Detection

- [x] **POS-01**: Detect ARB entries (bought both UP and DOWN)
- [x] **POS-02**: Detect ARB completion status (paired, lopsided, bail)
- [x] **POS-03**: Detect 99c capture entries
- [x] **POS-04**: Detect 99c capture outcomes (win/loss)
- [x] **POS-05**: Calculate P/L for each trade type

### Google Sheet Dashboard

- [ ] **SHEET-01**: Create/connect to dedicated performance sheet
- [ ] **SHEET-02**: Write window rows with all columns (Window, Time, ARB Entry, ARB Result, ARB P/L, 99c Entry, 99c Result, 99c P/L, Total)
- [ ] **SHEET-03**: Color code cells (green for wins/profits, red for losses)
- [ ] **SHEET-04**: Use emoji indicators (✓, ✗, ⚠, —)
- [ ] **SHEET-05**: Maintain summary row at top with totals and win rates

## v2 Requirements

Deferred to future release.

### Analytics

- **ANLYT-01**: Daily summary rows
- **ANLYT-02**: Weekly performance trends
- **ANLYT-03**: Strategy comparison (ARB vs 99c win rates)

### Alerts

- **ALERT-01**: Telegram notification on losing streak
- **ALERT-02**: Daily P/L summary notification

## Out of Scope

| Feature | Reason |
|---------|--------|
| Modifying trading bot | This is a separate observer, no interference |
| Historical backfill | Start fresh, grade going forward |
| Trade recommendations | Pure observation, no trading decisions |
| Real-time charts | Google Sheets is the dashboard |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| CORE-01 | Phase 1 | Complete |
| CORE-02 | Phase 1 | Complete |
| CORE-03 | Phase 1 | Complete |
| CORE-04 | Phase 1 | Complete |
| POS-01 | Phase 2 | Complete |
| POS-02 | Phase 2 | Complete |
| POS-03 | Phase 2 | Complete |
| POS-04 | Phase 2 | Complete |
| POS-05 | Phase 2 | Complete |
| SHEET-01 | Phase 3 | Pending |
| SHEET-02 | Phase 3 | Pending |
| SHEET-03 | Phase 3 | Pending |
| SHEET-04 | Phase 3 | Pending |
| SHEET-05 | Phase 3 | Pending |

**Coverage:**
- v1 requirements: 14 total
- Mapped to phases: 14
- Unmapped: 0 ✓

---
*Requirements defined: 2026-01-20*
*Last updated: 2026-01-20 — Phase 2 requirements complete*
