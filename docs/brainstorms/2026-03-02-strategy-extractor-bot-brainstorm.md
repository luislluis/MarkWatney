---
title: Strategy Extractor Bot — Reverse-Engineer Uncommon-Oat
topic: strategy-extraction
date: 2026-03-02
---

# Strategy Extractor Bot

## What We're Building

A bot that **observes** Uncommon-Oat's public trading activity on Polymarket BTC 15-minute markets, **analyzes** the data to reverse-engineer their trading strategy, and **produces a ruleset** that an independent bot can follow — without needing to watch Oat in real-time.

This is NOT a copy-trader. We don't mirror Oat's trades. We study them to understand WHY they trade, WHEN they enter, HOW they size, and WHAT conditions trigger their moves. Then we deploy our own bot using those extracted rules.

## Why This Approach

**Copy-trading doesn't work here because:**
- Latency: If Oat buys at 7c, it might be 15c by the time we buy
- Scale: Oat trades $1,600+ per window; we'd trade $20-40. Their fills move the market.
- Dependency: Copy-trading requires Oat to be active. If they stop, we stop.

**Strategy extraction is better because:**
- We learn the rules once, then run independently forever
- We can adapt the rules to our own scale and risk tolerance
- We understand the "why" — not just blindly following
- If the market evolves, we can re-observe and update rules

## Key Decisions

### 1. Full Redesign (Not Iterating on Copycat Bot)
The existing `copycat_bot.py` was built around copy-trading (detect Oat buy → we buy same side). That premise is wrong. Start fresh with a purpose-built observer and analysis engine.

### 2. Full Strategy Profile Extraction
Not just "which side does Oat buy" — extract ALL dimensions:
- **Entry timing**: When in the 15-min window? How many seconds in? Does timing correlate with outcomes?
- **Side selection**: What market conditions (OB state, prices, imbalance) predict which side Oat buys?
- **Pricing**: What price levels do they target? Do they use limit orders (maker) or market orders (taker)?
- **Sizing**: How do they size positions? Fixed dollar? Percentage? Varies by confidence?
- **Arb structure**: Do they always buy both sides? What's the gap between legs? Combined cost target?
- **Exit behavior**: Do they ever sell before settlement? Under what conditions?
- **Inter-window patterns**: Do they change behavior across the day? After wins vs losses?

### 3. Confidence-Score-Driven Readiness
No fixed observation period. Each strategy dimension gets a confidence score based on sample size and consistency. The dashboard shows when each dimension has enough data to be actionable. Example: "Side selection: 87% confidence (based on 200 observations)" vs "Exit behavior: 23% confidence (only 5 exits observed)".

### 4. Three-Layer Architecture
1. **Observer** — Pure data collection. Every fill, every OB snapshot, every market condition. No trading logic.
2. **Analyzer** — Continuous analysis engine. Queries collected data, computes strategy dimensions, updates confidence scores. Runs periodically or on-demand.
3. **Strategy Generator** — Once confidence thresholds are met, produces a human-readable strategy report AND a machine-readable ruleset that plugs into a new trading bot.

### 5. Web Dashboard for Live Insights
A hosted web page showing strategy insights evolving in real-time:
- Strategy dimensions with confidence scores
- Charts: entry timing distribution, side selection vs market conditions, pricing patterns
- Raw stats: Oat's win rate, trade frequency, average position size
- "Strategy readiness" score — overall confidence that we've cracked it

### 6. Hypothesis: Arb Strategy
Working hypothesis is that Oat buys both sides (UP + DOWN) at a combined cost < $1.00 for guaranteed profit. The data should confirm or refute this. Stay open to discovering something unexpected.

## What This Replaces

The current `copycat_bot.py` (copy-trader) gets replaced entirely. The `copycat_db.py` schema also gets redesigned for richer data capture (fill sequences, inter-window patterns, analysis results storage).

## Open Questions

1. **Dashboard hosting**: Run on the existing DigitalOcean server (174.138.5.183) or locally?
2. **Analysis frequency**: Run analysis after every window? Every N windows? On-demand only?
3. **Historical data**: Can we backfill Oat's historical activity from the API, or only capture forward?
4. **Multiple targets**: Should the architecture support watching other successful accounts later?
5. **Strategy bot**: When rules are extracted, do we build a new bot from scratch or modify the existing 99c sniper?
