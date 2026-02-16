---
title: "Bankroll Management & Daily ROI Pause"
type: feat
date: 2026-02-16
version: v1.46 "Kelly's Edge"
status: implemented (pending deploy)
---

# Bankroll Management & Daily ROI Pause

## Overview

Two connected features that manage risk and protect profits:

1. **Bankroll Management** — Trade size = 10% of portfolio, locked at start of each day
2. **Daily ROI Pause** — Stop trading when daily profit hits ~50% ROI, resume next day

Both are implemented in v1.46 and ready for deployment.

## Problem Statement

- **Fixed sizing risk**: Trading a fixed $6/trade regardless of bankroll means a losing streak hits harder as bankroll shrinks, and winning streaks don't compound
- **Profit giveback risk**: Bot currently trades 24/7 with no daily cap. A good day can be erased by a bad evening session

## Solution: Bankroll Management

### How It Works

```
At start of each day (midnight EST) or bot startup:
  1. Query portfolio balance (positions + USDC on-chain)
  2. trade_size = bankroll * 10%
  3. Clamp between $2 floor and $20 ceiling
  4. Lock for the entire day (no mid-day changes)
  5. Set CAPTURE_99C_MAX_SPEND = trade_size
```

### Example Scenarios

| Bankroll | 10%  | Clamped | Shares @ 99c |
|----------|------|---------|--------------|
| $61      | $6.10| $6.10   | 6            |
| $80      | $8.00| $8.00   | 8            |
| $40      | $4.00| $4.00   | 4            |
| $15      | $1.50| $2.00   | 2 (floor)    |
| $250     | $25  | $20.00  | 20 (ceiling) |

### Configuration (`trading_bot_smart.py`)

```python
BANKROLL_SIZING_ENABLED = True
BANKROLL_TRADE_PCT = 0.10        # 10% of bankroll per trade
BANKROLL_MIN_TRADE = 2.00        # $2 floor
BANKROLL_MAX_TRADE = 20.00       # $20 ceiling
```

### Implementation Details

- **Function**: `refresh_bankroll_sizing()` — runs once per EST day, uses `_bankroll_date` guard
- **Balance source**: `get_portfolio_balance()` — queries Polymarket positions API + USDC ERC20 balance on Polygon
- **Globals modified**: `CAPTURE_99C_MAX_SPEND`, `DAILY_ROI_AVG_TRADE_VALUE`
- **Logging**: `BANKROLL_SIZING` event to Supabase with bankroll, trade_size, shares
- **Called at**: bot startup (line ~3386) + each new window (line ~3551)

### Why Locked Daily (Not Continuous)

- Prevents mid-day balance fluctuations from changing sizing
- A 99c loss doesn't immediately shrink next trade (avoids tilt-sizing)
- Clean mental model: "today I'm trading $6 per trade"
- Matches how the ROI pause denominator works

## Solution: Daily ROI Pause

### How It Works

```
ROI = daily_profit / avg_trade_value

Each time a trade resolves (win, loss, exit):
  1. Add P&L to _daily_pnl running total
  2. Calculate ROI = _daily_pnl / DAILY_ROI_AVG_TRADE_VALUE
  3. If ROI >= 50%, set _roi_pause_active = True
  4. Bot stops entering new trades (still monitors existing positions)
  5. Sends Telegram notification
  6. Resets at midnight EST
```

### P&L Sources (all hooked into `record_daily_pnl()`)

| Source | Function | Example P&L |
|--------|----------|-------------|
| 99c sniper win | `notify_99c_resolution()` | +$0.06 (6 shares) |
| 99c sniper loss | `notify_99c_resolution()` | -$5.94 (6 shares) |
| ARB profit pair | `_send_pair_outcome_notification()` | +$0.05 |
| ARB loss-avoid | `_send_pair_outcome_notification()` | -$0.10 |
| Hard stop exit | `execute_hard_stop()` | -$2.34 |
| OB early exit | `execute_99c_early_exit()` | -$1.20 |

### Configuration

```python
DAILY_ROI_PAUSE_ENABLED = True
DAILY_ROI_PAUSE_TARGET = 0.50    # 50% ROI triggers pause
DAILY_ROI_AVG_TRADE_VALUE = 5.00 # Overridden by bankroll sizing at runtime
```

### Open Item: "Near 50%" Flexibility

Current behavior: hard `>= 0.50` check. User wants "near 50%" to count.

**Options:**
1. Lower target to 0.45 (45%) — triggers a bit earlier, locks in gains sooner
2. Lower target to 0.40 (40%) — more conservative, pauses with decent profit
3. Keep 0.50 but the math already provides flexibility: with $6 trades, each win adds ~1% ROI, so the bot will trigger at exactly 50% or the first win past it

**Recommendation:** Set to 0.45 (45%). With $6 trades, each 99c win = ~$0.06 = ~1% ROI. So the trigger will fire somewhere between 45-46%, which is "near 50%" territory while ensuring we lock in gains before risking a loss that could wipe them.

### What Gets Paused vs. What Keeps Running

| Paused (no new entries) | Still Active |
|------------------------|--------------|
| 99c capture orders | Fill detection for open orders |
| ARB orders | Hard stop monitoring (60c exit) |
| | OB early exit checks |
| | Danger score / hedge logic |
| | Position tracking |
| | Tick logging |

### Status Display

When paused, the status line shows:
```
[HH:MM:SS] PAUSED | T-XXXs | UP:XXc DN:XXc | pos:0/0 | ROI +50% (P&L $+3.00)
```

## How Both Features Connect

```
Bot startup / midnight EST:
  1. refresh_bankroll_sizing() → locks trade_size for the day
     - Also sets DAILY_ROI_AVG_TRADE_VALUE = trade_size
  2. reset_daily_roi_tracking() → resets _daily_pnl to $0

During trading:
  3. 99c captures use CAPTURE_99C_MAX_SPEND (= trade_size)
  4. Each resolved trade calls record_daily_pnl(pnl)
  5. ROI = _daily_pnl / trade_size
  6. If ROI >= 45-50% → pause all new entries

Next day:
  7. New bankroll check → new trade_size
  8. Reset daily P&L → resume trading
```

## Acceptance Criteria

- [x] Trade size calculated from portfolio balance at start of day
- [x] Trade size stays fixed for the entire day
- [x] Min $2 / Max $20 trade size bounds
- [x] Daily P&L accumulated from all trade resolution paths
- [x] ROI = daily_profit / avg_trade_value
- [x] Trading pauses when ROI target hit
- [x] Existing positions still monitored while paused
- [x] Telegram notification on pause trigger
- [x] Events logged to Supabase (BANKROLL_SIZING, ROI_PAUSE_TRIGGERED)
- [x] Auto-resume at midnight EST
- [x] Startup banner shows both features' config
- [ ] **Open**: Decide on exact ROI pause target (45% vs 50%)
- [ ] Deploy to server and verify in logs

## Deploy Checklist

```bash
# 1. Commit locally
git add trading_bot_smart.py BOT_REGISTRY.md
git commit -m "v1.46: Bankroll management + daily ROI pause"
git push

# 2. Upload to server
scp trading_bot_smart.py root@174.138.5.183:~/polymarket_bot/

# 3. SSH in and restart
ssh root@174.138.5.183
pkill -f trading_bot_smart.py
cd ~/polymarket_bot && export GOOGLE_SHEETS_SPREADSHEET_ID=1fxGKxKxj2RAL0hwtqjaOWdmnwqg6RcKseYYP-cCKp74 && export GOOGLE_SHEETS_CREDENTIALS_FILE=~/.google_sheets_credentials.json && nohup python3 trading_bot_smart.py > /dev/null 2>&1 &

# 4. Verify
tail -30 ~/polybot/bot.log
# Look for:
#   BANKROLL: $61.36 x 10% = $6.13/trade (6 shares @ 99c) [locked for 2026-02-16]
#   ROI_PAUSE: Daily tracking reset (target: $3.07 profit = 50% of $6.13 avg trade)
```

## References

- `trading_bot_smart.py:383-396` — Config constants
- `trading_bot_smart.py:668-718` — Bankroll sizing state + functions
- `trading_bot_smart.py:720-768` — ROI pause state + functions
- `trading_bot_smart.py:863-870` — 99c sniper P&L hook
- `trading_bot_smart.py:988-1009` — ARB pair P&L hooks
- `trading_bot_smart.py:1539-1542` — Hard stop P&L hook
- `trading_bot_smart.py:2010-2013` — Early exit P&L hook
- `CLAUDE.md:57-72` — Deploy process
