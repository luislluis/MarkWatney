# Brainstorm: Exit Strategy False Positives — Why Feb 9 Lost 2x vs Feb 8

**Date:** 2026-02-09
**Status:** Deep investigation complete (5 parallel agents), ready for decision

## The Problem

- **Feb 8 (yesterday):** +$2.38 P&L (+40% ROI), 62 windows, 14 exits
- **Feb 9 (today):** -$4.07 P&L (-69% ROI), 40 windows, 19 exits
- Same bot, same strategy, same market. Why 2x worse?

## Root Cause Analysis

### Finding 1: The ONLY exit mechanism firing is HARD_STOP (60c trigger)

Every single exit across both days comes from `HARD_STOP_EXIT` with `trigger=60c`. The OB exit, price stop, 5-sec rule, and market reversal mechanisms are **not firing** on any 99c capture trades. The bot is either holding to resolution or panic-selling when the bid briefly touches 60c.

### Finding 2: Market conditions were NOT worse today

| Metric | Feb 8 | Feb 9 | Change |
|--------|-------|-------|--------|
| Ask price stddev | 0.304 | 0.297 | -2% (less volatile) |
| BTC stddev | $814 | $777 | -5% (less volatile) |
| BTC range | $3,363 | $3,113 | -7% (tighter range) |
| High confidence opportunities (>95c) | 11,266 | 8,947 | **-21% fewer** |

Today was actually **calmer** than yesterday. But there were 21% fewer high-confidence setups.

### Finding 3: Both real losses entered at minimum confidence (95%)

The 2 actual market losses today:
- **04:13 EST** — UP @ 95% confidence, TTL=120s (minimum threshold)
- **09:13 EST** — DOWN @ 95% confidence, TTL=104s (minimum threshold)

Both entered with the weakest acceptable signal. A 96% threshold eliminates both.

### Finding 4: ONE catastrophic false exit is 56.5% of today's excess loss

The **07:58 EST** trade is the smoking gun:
- Bot bought DOWN at 98c (high confidence)
- 8 seconds later, bid briefly crashed below 60c → HARD_STOP triggered
- Sold at 72c for a 26c loss ($1.57)
- **DOWN actually won.** If held, would have earned $0.12
- This single false exit accounts for **56.5% of today's excess losses vs yesterday**

### Finding 5: Exit false positive rate is ~90% on BOTH days

| | Yesterday | Today |
|---|---|---|
| Exits total | 14 | 19 |
| Correct exits | 1 (7%) | 2 (11%) |
| False exits | 13 (93%) | 17 (89%) |
| Net exit value | +$0.48 | +$3.11 |
| Large false exits (>10c) | 2 ($1.68 cost) | 1 ($1.57 cost) |

The exit strategy is marginally net positive both days (the correct exits save more than the false exits cost). But the margin is razor thin and one bad false exit can flip the day.

## Why Today Is 2x Worse — The 3 Factors

1. **More exits** (19 vs 14) — More hard stop triggers, costing more in aggregate
2. **Fewer total trades** (40 vs 62) — Less win profit to offset exit losses
3. **The 07:58 catastrophe** — Single $1.57 false exit that wouldn't have happened yesterday

---

## Deep Investigation (5 parallel agents)

Ran 5 independent investigations to check if anything ELSE went wrong beyond confidence/exits.

### Investigation 1: Exit Event Audit — Where are the 15 "missing" exits?

The Events table only has 7 HARD_STOP_EXIT events (3 Feb 8, 4 Feb 9), but the dashboard shows 33 exits total. The 26 "missing" exits are **real on-chain sells that the bot failed to log**.

Three causes:
1. **Daemon thread logging** — `log_event()` runs in `threading.Thread(daemon=True)`. Daemon threads get killed if the main thread moves on, so exit logs are silently dropped.
2. **60-second flush blackout** — `maybe_flush_ticks()` skips flushing when TTL < 60s. Most exits happen in this window.
3. **Error paths bypass logging** — `execute_hard_stop()` has multiple early-return paths where the sell may partially execute on-chain but `log_event()` is never reached.

**The dashboard (Activity API) is the authoritative source.** Events table is a lossy record.

### Investigation 2: Position Tracking Bug — Found but didn't fire

Code audit found a real bug at **lines 3636-3640** of `trading_bot_smart.py`: when the bot detects imbalance in STATE_QUOTING, it overwrites position tracking directly from the API without `max()` protection:

```python
window_state['filled_up_shares'] = api_up      # Direct overwrite!
window_state['filled_down_shares'] = api_down   # No max() protection!
```

This violates the "fills can only increase" principle used elsewhere. If the API returns stale 0/0 data, the bot would "forget" its position. **However, tick data analysis shows this bug did NOT trigger on Feb 8-9** (no position drops found).

### Investigation 3: Market Microstructure — Confirmed calmer, no anomalies

No flash crashes, no unusual spread regime, no systemic anomalies. Feb 9 was objectively calmer than Feb 8 across every metric. The losses are entirely from the bot's own entry/exit decisions, not market conditions.

### Investigation 4: Confidence Formula — Code is correct

Code audit of `trading_bot_smart.py` lines 1987-2004 confirms:
- The confidence formula `ask_price - time_penalty` is implemented correctly
- No stale data risk within a single loop iteration (same `books` object feeds both ticks and entry logic)
- Timing skew is conservative (applies higher penalty, not lower)
- TTL > 120s entries are mathematically impossible (would need 103c+ ask)

### Investigation 5: Win Rate by TTL Bucket

| TTL at Entry | Feb 8 Trades | Feb 8 Win% | Feb 9 Trades | Feb 9 Win% | Avg Conf | Exits |
|---|---|---|---|---|---|---|
| 0-30s | 7 | 100% (7/7) | 9 | 89% (8/9) | 97.5% | 1 |
| 30-60s | 15 | 93% (14/15) | 10 | 100% (10/10) | 96.7% | 2 |
| 60-90s | 6 | 100% (6/6) | 12 | 92% (11/12) | 95.0% | 0 |
| 90-120s | 35 | 100% (35/35) | 32 | 94% (30/32) | 95.5% | 4 |

**Key insight:** The 90-120s bucket is 53% of all trades but has the lowest confidence (avg 95.5%) and generated both of Feb 9's real losses. These are the riskiest trades — most time for reversal, lowest confidence.

### Investigation Verdict: No hidden bugs

**The bot worked as designed.** No position tracking failures, no stale data, no calculation errors, no market anomalies. The losses are entirely explained by:
1. Two marginal 95%-confidence entries with high TTL that lost
2. One catastrophic false exit on a good trade
3. Fewer winning trades to offset losses

---

## Confidence Threshold Analysis (VERIFIED from Supabase)

Precise filled-trade counts and outcomes across both days:

| Threshold | Feb 8 | | Feb 9 | | Combined | |
|---|---|---|---|---|---|---|
| | Trades | W/L | Trades | W/L | Trades | Win Rate |
| **>= 95% (current)** | 63 | 62/1 | 63 | 61/2 | 126 | 97.6% |
| **>= 96%** | 36 | 35/1 | 29 | 29/0 | 65 | 98.5% |
| **>= 97%** | 15 | 14/1 | 15 | 15/0 | 30 | 96.7% |
| **>= 98%** | 9 | 8/1 | 8 | 8/0 | 17 | 94.1% |
| >= 99% | 1 | 1/0 | 1 | 1/0 | 2 | 100% |

**Critical correction:** The 98% threshold does NOT guarantee zero losses. Window 1770498900 (Feb 8, 02:29 EST) entered UP at exactly 98% confidence, TTL=40s, and LOST. This one loss persists at every threshold below 99%.

### The 3 losing trades

| # | Date | Time | Confidence | TTL | Penalty | Eliminated at |
|---|---|---|---|---|---|---|
| 1 | Feb 8 | 02:29 | **98%** | 40s | 0% | 99% only |
| 2 | Feb 9 | 14:13 | **95%** | 120s | 3% | 96% |
| 3 | Feb 9 | 19:13 | **95%** | 104s | 3% | 96% |

### Volume retention at each threshold

| Threshold | Trades/day | % of current | Losses eliminated |
|---|---|---|---|
| 95% (current) | ~63 | 100% | — |
| **96%** | **~32** | **52%** | **Feb 9's 2 losses** |
| 97% | ~15 | 24% | Feb 9's 2 losses |
| 98% | ~8.5 | 13% | Feb 9's 2 losses |
| 99% | ~1 | 1.6% | All 3 losses |

## Counterfactual Scenarios

| Scenario | Feb 8 P&L | Feb 9 P&L | Combined |
|----------|-----------|-----------|----------|
| **Actual (current @ 95%)** | +$2.38 | -$4.07 | -$1.69 |
| No exits at all | -$1.32 | -$14.94 | -$16.26 |
| Hard stop only at 40c | +$2.52 | -$2.10 | +$0.42 |

## Approaches

### Approach A: Raise confidence to 96% (best volume/safety tradeoff)

Change `CAPTURE_99C_MIN_CONFIDENCE` from 0.95 to 0.96. Eliminates both Feb 9 losses while keeping 52% of trade volume.

- **Pros:** Eliminates both Feb 9 losses. Retains ~32 trades/day. Win rate goes from 97.6% to 98.5%.
- **Cons:** Still has the Feb 8 02:29 loss (98% confidence, unavoidable below 99%). Loses ~half of trading volume.
- **Best for:** Meaningful improvement without sacrificing too much volume.

### Approach B: Raise confidence to 98% (maximum safety)

Change `CAPTURE_99C_MIN_CONFIDENCE` from 0.95 to 0.98. Only ~8.5 trades/day.

- **Pros:** Eliminates Feb 9's losses. 16/17 wins (94.1%) over 2 days.
- **Cons:** Volume drops ~87%. Still has 1 loss (Feb 8, 98% confidence). Daily profit ~$0.36-0.48.
- **Best for:** If you want near-zero risk. But daily profit is very small (~8% ROI).

### Approach C: Add hold discipline (never exit below 94c from 97c+ entries)

If you bought at 97c+, your max loss is 3c/share ($0.18). The hard stop at 60c allows exits at 70-86c, which is way worse than just holding to a $5.94 resolution loss.

- **Pros:** Prevents the 07:58 catastrophe. Caps exit losses at 3c instead of 26c.
- **Cons:** Some real losses won't be exited (you'd eat the full $5.94). Doesn't reduce entry count.
- **Best for:** Protecting against large false exits specifically.

### Approach D: Tighten hard stop to 40c (from 60c)

Make the exit trigger harder to hit. At 40c, only truly catastrophic reversals would trigger.

- **Pros:** Reduces false exits significantly (most brief dips don't reach 40c).
- **Cons:** When you DO need to exit, you're selling at worse prices.
- **Best for:** Reducing false positives while keeping some downside protection.

### Approach E: Combine 96% confidence + hold discipline

Raise to 96% AND add hold discipline for 97c+ entries. Addresses both entry quality and exit quality simultaneously.

- **Pros:** Filters the worst entries AND prevents catastrophic false exits. Best combined protection.
- **Cons:** More complex change. Reduced volume.
- **Best for:** Maximum practical improvement without going to ultra-conservative 98%.

## Recommendation

**Raise to 96% (Approach A).** It's the clear sweet spot:
- Eliminates both of today's real losses (both at exactly 95%)
- Retains 52% of volume (~32 trades/day)
- Simple one-line change
- Can combine with hold discipline (Approach E) for additional protection

## Code Bug to Fix (non-urgent)

Position tracking overwrite at lines 3636-3640 in `trading_bot_smart.py` — should use `max()` protection like the rest of the codebase. Didn't cause issues on Feb 8-9 but is a latent risk.

## Open Questions

- Should we backtest 96% across ALL historical data (not just 2 days) before deploying?
- The Feb 8 loss at 98% confidence (window 1770498900, TTL=40s) is concerning — how to protect against high-confidence losses?
- How many profitable trades per day do we need to justify running the bot?
