---
title: "feat: Dynamic trade sizing — 10% of portfolio"
type: feat
date: 2026-02-17
---

# feat: Dynamic trade sizing — 10% of portfolio

Each 99c capture trade uses 10% of total portfolio value. Share count rounds up (ceiling). Checked once per window using the existing `get_portfolio_balance()` function.

## Acceptance Criteria

- [x] Trade size = `ceil(portfolio_total * 0.10 / bid_price)` shares
- [x] Portfolio balance (positions + USDC) fetched at each window start and cached
- [x] Shares rounded up via `math.ceil()`
- [x] `FAILSAFE_MAX_SHARES = 50` still caps the upper bound
- [x] Falls back to `CAPTURE_99C_MAX_SPEND` if balance fetch fails (returns 0)
- [x] Log line shows computed trade size at window start

## Changes

### `trading_bot_smart.py`

**1. Add constant** (~line 346):
```python
TRADE_SIZE_PCT = 0.10  # 10% of portfolio per trade
```

**2. Add module-level cache** (near other globals ~line 416):
```python
cached_portfolio_total = 0.0
```

**3. Fetch balance at window start** (~line 3684, in the `slug != last_slug` block):
```python
# Portfolio balance for trade sizing (every window)
pos_val, usdc_val = get_portfolio_balance()
cached_portfolio_total = pos_val + usdc_val
if cached_portfolio_total > 0:
    print(f"[{ts()}] Portfolio: ${cached_portfolio_total:.2f} → trade size: ${cached_portfolio_total * TRADE_SIZE_PCT:.2f}")
```

**4. Use dynamic sizing in `execute_99c_capture()`** (line 2156):
```python
# Before (hardcoded):
shares = int(CAPTURE_99C_MAX_SPEND / CAPTURE_99C_BID_PRICE)

# After (dynamic):
import math
if cached_portfolio_total > 0:
    trade_budget = cached_portfolio_total * TRADE_SIZE_PCT
    shares = math.ceil(trade_budget / CAPTURE_99C_BID_PRICE)
    shares = min(shares, FAILSAFE_MAX_SHARES)
else:
    shares = int(CAPTURE_99C_MAX_SPEND / CAPTURE_99C_BID_PRICE)  # fallback
```

## Examples

| Portfolio | 10% Budget | Bid Price | Raw Shares | Ceiling | Spend |
|-----------|-----------|-----------|------------|---------|-------|
| $64.61 | $6.46 | $0.95 | 6.8 | 7 | $6.65 |
| $100.00 | $10.00 | $0.95 | 10.5 | 11 | $10.45 |
| $50.00 | $5.00 | $0.95 | 5.3 | 6 | $5.70 |
| $0.00 | $0.00 | $0.95 | — | 6 (fallback) | $5.70 |

## References

- `get_portfolio_balance()`: `trading_bot_smart.py:620`
- `execute_99c_capture()`: `trading_bot_smart.py:2149`
- `CAPTURE_99C_MAX_SPEND`: `trading_bot_smart.py:346`
- Window start block: `trading_bot_smart.py:3642-3690`
- Brainstorm: `docs/brainstorms/2026-02-17-dynamic-trade-sizing-brainstorm.md`
