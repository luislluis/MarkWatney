# Polymarket Bot: Smart Hedge System

## What This Is

Enhancement to the existing Polymarket BTC 15-minute trading bot. Adds a multi-signal danger scoring system for 99c capture positions that triggers hedges earlier, based on weighted analysis of confidence erosion, order book imbalance, price velocity, opponent strength, and time decay.

**Current Version:** v1.7 "Watchful Owl"

## Core Value

**Preserve 99c capture profits by limiting losses to small controlled amounts instead of total loss.**

The 99c capture strategy wins 97-98% of the time. When it loses, we currently lose 100% of the position. A well-tuned hedge system that triggers early enough can convert devastating losses into small, acceptable ones — preserving the strategy's profitability.

## Requirements

### Validated

These capabilities exist in the bot:

- ✓ BTC 15-minute Up/Down arbitrage trading — existing
- ✓ 99c capture strategy (confidence-based single-side bets) — existing
- ✓ Basic hedge system (triggers at 85% confidence drop) — existing
- ✓ Google Sheets logging (per-second ticks, events, windows) — existing
- ✓ Order book imbalance tracking — existing
- ✓ Chainlink BTC price feed — existing
- ✓ Multi-signal danger scoring system — v1.0
- ✓ Configurable danger threshold (default 0.40) — v1.0
- ✓ Price velocity tracking (5-second rolling window) — v1.0
- ✓ Peak confidence tracking per position — v1.0
- ✓ Danger score logging to Google Sheets — v1.0
- ✓ Hedge decision logging with signal breakdown — v1.0

### Active

(None — planning next milestone)

### Out of Scope

- Machine learning / adaptive thresholds — complexity not justified yet, tune manually first
- Backtesting framework — would be nice but not required for v1
- Multiple hedge levels (partial hedges) — keep it simple: hedge or don't
- Alternative hedge mechanisms (selling position vs buying opposite) — buying opposite is simpler and guaranteed

## Current State

**Shipped:** v1.0 Smart Hedge System (2026-01-19)

**Codebase:**
- 3,176 lines of Python (trading_bot_smart.py, sheets_logger.py)
- Tech stack: py-clob-client, gspread, Chainlink oracle

**What's Working:**
- Danger score calculated every tick when holding 99c position
- 5 weighted signals: confidence drop (3.0), OB imbalance (0.4), velocity (2.0), opponent ask (0.5), time decay (0.3)
- Hedge triggers at danger_score >= 0.40
- Full observability: D:X.XX console display, Sheets Ticks column, signal breakdown on hedge events

**Next:** Monitor hedge effectiveness in production, tune threshold if needed

## Constraints

- **Integration**: Must integrate cleanly with existing `check_99c_capture_hedge()` function ✓
- **Performance**: Cannot add latency to the main loop (already runs per-second) ✓
- **Reliability**: Hedge orders must use existing `place_and_verify_order()` with retries ✓
- **Observability**: All hedge decisions must be logged with full signal breakdown ✓

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Weighted score over multi-trigger | More nuanced, avoids unnecessary hedges from single false signals | ✓ Shipped v1.0 |
| 0.40 threshold (cautious) | User preference; with 97-98% win rate, can afford extra hedges | ✓ Shipped v1.0 |
| Buy opposite side to hedge | Simpler than selling; guarantees locked-in loss amount | ✓ Shipped v1.0 |
| 5-second velocity window | Long enough to smooth noise, short enough to catch rapid flips | ✓ Shipped v1.0 |
| Danger score uncapped | Values >1.0 indicate very dangerous situations, useful information | ✓ Shipped v1.0 |
| Signal component dict return | Returns both raw values and weighted components for logging | ✓ Shipped v1.0 |

---
*Last updated: 2026-01-19 after v1.0 milestone*
