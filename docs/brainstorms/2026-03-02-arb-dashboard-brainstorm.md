---
title: Arbitrage Dashboard for Maker ARB Bot
type: feat
date: 2026-03-02
---

# Arbitrage Dashboard for Maker ARB Bot

## What We're Building

A new standalone dashboard page (`arb-dashboard.html`) at `https://luislluis.github.io/MarkWatney/arb-dashboard.html` that serves as the **main dashboard for the maker arb bot**. It displays daily trades with arbitrage detection, showing:

- All trades from the arb bot (distinguished from the 95c bot by price: arb buys are < 95c)
- Arbitrage pair matching: when equal shares of UP and DOWN are bought at a combined cost < $1.00
- Partial arb detection: if 15 UP + 10 DOWN, the 10+10 is arb, the remaining 5 UP is standalone
- Cash icon (💰) for locked-in arb profits
- Dollar profit, ROI %, win rate stats
- Same dark terminal design system as existing `dashboard.html`

## Why This Approach

### New standalone file vs modifying existing dashboard

**Decision: Create new `arb-dashboard.html`** — The existing `dashboard.html` is the 95c bot's home. The arb bot gets its own dedicated page. This avoids adding complexity to an already 2,700-line file and keeps each bot's analytics separate. The user said "make it the main page for this arbitration bot."

### Data source

**Decision: Polymarket Activity API** — Same source as the existing dashboard. Both bots use the same funder wallet (`0x636796704404959f5Ae9BEfEb2B3880eadf6960a`). We differentiate trades by price:
- **Arb bot trades**: Buys where price < 0.95 (arb bot buys both sides at cheap prices, typically 0.40-0.60)
- **95c bot trades**: Buys where price >= 0.95 (these are already filtered out in the existing dashboard)

This is the inverse filter of what `dashboard.html` already does (it skips buys < 0.95).

### Arb pair matching logic

**Decision: Match by slug + window, then pair UP/DOWN by minimum shares**

1. Fetch all trades from Activity API
2. Filter to arb bot trades only (buy price < 0.95)
3. Group by slug (market/window)
4. For each slug, find UP buys and DOWN buys
5. Arb pair = min(total UP shares, total DOWN shares) on each side
6. Combined cost = (arb UP shares × avg UP price) + (arb DOWN shares × avg DN price)
7. If combined per-share cost < $1.00 → **locked arb profit** (💰)
8. Remaining unpaired shares are "standalone" trades (shown separately, with their own P&L from redemption)

### Arb outcome classification

| Scenario | Icon | Label | P&L |
|----------|------|-------|-----|
| Arb locked: combined < $1 | 💰 | ARB WIN | $(1.00 - combined) × shares |
| Arb locked: combined >= $1 | ⚠️ | ARB LOSS | $(1.00 - combined) × shares (negative) |
| Standalone shares: redeemed | ✓ | WIN | $1.00 × shares - cost |
| Standalone shares: not redeemed | ✗ | LOSS | -cost |
| Pending (window not resolved) | ◷ | PENDING | -- |

### Stats to display

**Top cards:**
- Today's Arb P&L (total profit from arb pairs + standalone P&L)
- Win Rate (arb wins / total arb pairs)
- Total Arb Locked (lifetime arb profit)

**Trade list (per window):**
- Arb pair row: `💰 ARB 10+10 UP@0.49 DN@0.47 = 0.96` → `+$0.40`
- Standalone row: `✓ UP 5@0.49` → `+$2.55` or `✗ UP 5@0.49` → `-$2.45`
- Date separators, daily history, same layout patterns

### Color scheme

Exact same CSS variables as existing dashboard:
- `--bg-primary: #0a0a0b`, `--bg-secondary: #111113`
- `--green-primary: #34d399` for wins/profit
- `--red-primary: #f87171` for losses
- `--yellow-primary: #fbbf24` for pending
- `--gold-accent: #d4af37` for arb-specific highlights
- Fonts: JetBrains Mono (data), Outfit (body)
- Glassmorphism cards, subtle noise texture, gradient mesh background

## Key Decisions

1. **New file `arb-dashboard.html`** at repo root — GitHub Pages serves it directly
2. **Data source: Polymarket Activity API** — no Supabase needed, same wallet, filter by price < 0.95
3. **Arb matching: min(UP shares, DOWN shares) per slug** — handles unequal fills cleanly
4. **Arb P&L = (1.00 - combined_cost) × paired_shares** — guaranteed profit calculation
5. **Standalone shares tracked separately** — win/loss determined by redemption status
6. **Same design system** — reuse all CSS variables, fonts, card styles from existing dashboard
7. **Gold accent for arb-specific elements** — `--gold-accent: #d4af37` to distinguish arb wins from regular wins
8. **Tab structure**: Single view (no tabs needed yet) — just Arb Trades as the main/only view. Can add tabs later if needed.

## Open Questions

None — requirements are well-defined. Ready for planning.
