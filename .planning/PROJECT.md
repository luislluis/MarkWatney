# Polymarket Bot: Smart Hedge System

## What This Is

Enhancement to the existing Polymarket BTC 15-minute trading bot. Adds a multi-signal danger scoring system for 99c capture positions that triggers hedges earlier, based on weighted analysis of confidence erosion, order book imbalance, price velocity, opponent strength, and time decay.

## Core Value

**Preserve 99c capture profits by limiting losses to small controlled amounts instead of total loss.**

The 99c capture strategy wins 97-98% of the time. When it loses, we currently lose 100% of the position. A well-tuned hedge system that triggers early enough can convert devastating losses into small, acceptable ones — preserving the strategy's profitability.

## Requirements

### Validated

These capabilities already exist in the bot:

- ✓ BTC 15-minute Up/Down arbitrage trading — existing
- ✓ 99c capture strategy (confidence-based single-side bets) — existing
- ✓ Basic hedge system (triggers at 85% confidence drop) — existing
- ✓ Google Sheets logging (per-second ticks, events, windows) — existing
- ✓ Order book imbalance tracking — existing
- ✓ Chainlink BTC price feed — existing

### Active

New capabilities for this milestone:

- [ ] Multi-signal danger scoring system
- [ ] Configurable danger threshold (default 0.40)
- [ ] Price velocity tracking (5-second rolling window)
- [ ] Peak confidence tracking per position
- [ ] Danger score logging to Google Sheets
- [ ] Hedge decision logging with signal breakdown

### Out of Scope

- Machine learning / adaptive thresholds — complexity not justified yet, tune manually first
- Backtesting framework — would be nice but not required for v1
- Multiple hedge levels (partial hedges) — keep it simple: hedge or don't
- Alternative hedge mechanisms (selling position vs buying opposite) — buying opposite is simpler and guaranteed

## Context

**The Problem:**
Analysis of 4 loss windows revealed that market flips happen in 13-15 seconds. The current 85% confidence threshold triggers too late — by the time confidence drops that far, only 5-10 seconds remain and the opposite side is too expensive to hedge effectively.

**Key Findings from Loss Analysis:**
- Order book imbalance showed heavy sell pressure (-0.9) even before price moved
- Price velocity accelerated rapidly during flips (>10c/sec)
- Opponent side surged past 30c before our confidence dropped significantly

**Data Sources Available:**
- Per-second tick data in Google Sheets (since Jan 15)
- Order book imbalance already calculated by `orderbook_analyzer.py`
- BTC price from Chainlink feed

## Constraints

- **Integration**: Must integrate cleanly with existing `check_99c_capture_hedge()` function
- **Performance**: Cannot add latency to the main loop (already runs per-second)
- **Reliability**: Hedge orders must use existing `place_and_verify_order()` with retries
- **Observability**: All hedge decisions must be logged with full signal breakdown

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Weighted score over multi-trigger | More nuanced, avoids unnecessary hedges from single false signals | — Pending |
| 0.40 threshold (cautious) | User preference; with 97-98% win rate, can afford extra hedges | — Pending |
| Buy opposite side to hedge | Simpler than selling; guarantees locked-in loss amount | — Pending |
| 5-second velocity window | Long enough to smooth noise, short enough to catch rapid flips | — Pending |

---
*Last updated: 2026-01-19 after initialization*
