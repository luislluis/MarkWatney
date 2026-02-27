---
date: 2026-02-27
topic: instant-profit-lock
---

# Instant Profit Lock: Sell at 99c on Fill

## What We're Building

Immediately after a 99c capture order fills (bought at 95c), place a sell limit order at 99c for the entire fill quantity. This locks in 4c/share profit if the sell fills, while eliminating the risk of holding to settlement.

If the market turns and our side's best bid drops below 60c, cancel the 99c sell order (it won't fill anyway) and fall back to the existing 45c hard stop for emergency exit.

## Why This Approach

Today's $277.20 loss (window 1772196300) showed the fatal flaw in our current "hold to settlement" strategy: a last-second BTC reversal took DOWN from 99c to $0 in under 15 seconds. The bot had 29 seconds where it could have sold at 97-98c but had no mechanism to do so.

This approach is simple and covers both scenarios:
- **Normal win**: Sell fills at 99c, pocket 4c/share profit immediately. No settlement risk.
- **Market turns**: Cancel sell, fall back to 45c hard stop. Lose less than holding to $0.

## Key Decisions

- **Sell price**: 99c limit order (not market sell)
- **Trigger**: Immediately on 99c capture fill detection
- **Cancel threshold**: Our side's best bid < 60c
- **After cancel**: Existing 45c hard stop takes over
- **Partial fills**: If sell partially fills, track remaining shares for hard stop protection

## Open Questions

- If the 99c sell is cancelled at 60c and price recovers above 60c, do we re-place the 99c sell?
- Should we monitor for partial fills and adjust the remaining sell quantity?

## Next Steps

-> `/workflows:plan` for implementation details
