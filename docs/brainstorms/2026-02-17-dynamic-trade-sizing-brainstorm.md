---
title: Dynamic Trade Sizing — 10% of Portfolio
type: feat
date: 2026-02-17
---

# Dynamic Trade Sizing — 10% of Portfolio

## What We're Building

Replace the hardcoded `CAPTURE_99C_MAX_SPEND = $6.00` with dynamic sizing: each 99c capture trade uses 10% of total portfolio value (positions + USDC cash). When the resulting share count isn't a whole number, round up (ceiling).

## Why This Approach

- Current: fixed $6/trade regardless of portfolio size
- Better: as portfolio grows, trade size grows proportionally
- Example: $64.61 portfolio → $6.46 → ceil(6.46 / 0.95) = 7 shares ($6.65)
- Example: $100 portfolio → $10 → ceil(10 / 0.95) = 11 shares ($10.45)

## Key Decisions

- [x] **10% of total portfolio** (positions + USDC), not just USDC
- [x] **Round up** (ceiling) when shares aren't a whole number
- [x] **Check once at window start** — use the already-cached `get_portfolio_balance()` result
- [x] **Keep FAILSAFE_MAX_SHARES = 50** as upper bound
- [x] **Keep CAPTURE_99C_MAX_SPEND as fallback** if balance check fails

## Open Questions

None — requirements are clear.
