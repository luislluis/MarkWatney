---
title: "feat: ROI halt via Polymarket Activity API"
type: feat
date: 2026-02-17
---

# feat: ROI halt via Polymarket Activity API

## Overview

Replace the Supabase-based `get_daily_roi()` with a direct call to the Polymarket `/activity` API — the same data source the dashboard uses. This guarantees the bot's ROI number matches the dashboard exactly: same trades, same win/loss determination, same formula.

## Problem Statement

The current `get_daily_roi()` in `supabase_logger.py` queries Supabase events (CAPTURE_FILL, CAPTURE_99C_WIN, CAPTURE_99C_LOSS) to calculate ROI. This diverges from the dashboard because:

1. **Win/loss mismatch**: Bot logs `CAPTURE_99C_LOSS` when it detects a loss, but the dashboard uses on-chain redemptions. A hard-stop exit can be a LOSS in Supabase but an EXIT (or even WIN) on-chain.
2. **Null data**: Some WIN events have null Shares, requiring fragile fallback logic.
3. **Different data source**: Supabase events are logged by the bot itself, while the dashboard reads authoritative on-chain data.

This caused the bot to calculate -5.7% ROI while the dashboard showed +50.4% — leading to the bot continuing to trade when it should have halted.

## Proposed Solution

Add a new function `get_roi_from_activity_api()` in `trading_bot_smart.py` that:

1. Calls `GET https://data-api.polymarket.com/activity?user={WALLET}&limit=1000`
2. Filters TRADE events to today (>= midnight EST)
3. Builds `redeemed` set from REDEEM events
4. Groups trades by `slug|outcome`, separates buys/sells
5. FIFO-matches sells against buys (same algorithm as dashboard)
6. Classifies: EXIT (fully sold), WIN (redeemed), LOSS (>30min, not redeemed), PENDING (pnl=0)
7. Returns ROI using dashboard formula: `total_pnl / (total_cost / num_windows)`

Wire this into `check_daily_roi()` replacing the `supabase_get_daily_roi()` call.

## Acceptance Criteria

- [ ] Bot's ROI calculation matches dashboard for the same point in time
- [ ] Bot halts at 45% ROI using Activity API data
- [ ] API failure is fail-open (skip check, keep trading) — same as current behavior
- [ ] Startup ROI check runs ALWAYS (even if halt file exists) to self-correct stale halts
- [ ] Log line shows: event count, windows, PnL, avg cost, ROI for debugging
- [ ] Timeout on API call (10s) to avoid blocking main loop
- [ ] Version bumped to v1.49

## Technical Approach

### Files to modify

#### `trading_bot_smart.py`

**1. Add `get_roi_from_activity_api()` function** (~60 lines)

Place after the existing `check_daily_roi()` function (~line 3290).

```python
# trading_bot_smart.py — new function

def get_roi_from_activity_api() -> dict:
    """
    Query Polymarket Activity API for today's ROI.
    Uses same data source and logic as dashboard.
    Returns dict with: total_pnl, avg_trade_cost, roi, wins, losses, exits, pending, trades
    Returns None on error.
    """
    try:
        midnight_utc = get_midnight_est_utc()
        midnight_ts = midnight_utc_to_unix(midnight_utc)  # need helper

        url = f"https://data-api.polymarket.com/activity?user={WALLET_ADDRESS}&limit=1000"
        resp = http_session.get(url, timeout=10)
        resp.raise_for_status()
        all_activity = resp.json()

        # Split into trades and redeems
        poly_trades = [a for a in all_activity if a.get('type') == 'TRADE']
        redeem_events = [a for a in all_activity if a.get('type') == 'REDEEM']
        redeemed = {r['slug'] for r in redeem_events if r.get('slug')}

        # Filter trades to today
        today_trades = [t for t in poly_trades if t.get('timestamp', 0) >= midnight_ts]

        if not today_trades:
            return {"total_pnl": 0, "avg_trade_cost": 0, "roi": 0,
                    "wins": 0, "losses": 0, "exits": 0, "pending": 0,
                    "trades": 0, "capital_deployed": 0}

        # Group by slug|outcome
        grouped = {}
        for t in today_trades:
            key = f"{t['slug']}|{t.get('outcome', t.get('side', ''))}"
            if key not in grouped:
                grouped[key] = {"buys": [], "sells": []}
            entry = {
                "size": float(t.get('size', 0)),
                "price": float(t.get('price', 0)),
                "timestamp": float(t.get('timestamp', 0)),
                "slug": t['slug'],
            }
            if t.get('side') == 'SELL':
                grouped[key]["sells"].append(entry)
            else:
                grouped[key]["buys"].append(entry)

        # Process each group: FIFO match sells to buys, classify
        trades_out = []
        for key, group in grouped.items():
            slug = key.split('|')[0]
            if not group["buys"]:
                continue
            won = slug in redeemed

            group["buys"].sort(key=lambda x: x["timestamp"])
            group["sells"].sort(key=lambda x: x["timestamp"])

            # FIFO: match sells against buys
            sell_pool = [{"remaining": s["size"], "price": s["price"]} for s in group["sells"]]
            buy_exit_info = [{"exit_shares": 0.0, "exit_revenue": 0.0} for _ in group["buys"]]

            for sell in sell_pool:
                for i, buy in enumerate(group["buys"]):
                    if sell["remaining"] <= 0.001:
                        break
                    can_match = buy["size"] - buy_exit_info[i]["exit_shares"]
                    if can_match <= 0.001:
                        continue
                    matched = min(sell["remaining"], can_match)
                    buy_exit_info[i]["exit_shares"] += matched
                    buy_exit_info[i]["exit_revenue"] += matched * sell["price"]
                    sell["remaining"] -= matched

            # Classify each buy
            import time as _time
            now_ts = _time.time()
            for i, buy in enumerate(group["buys"]):
                info = buy_exit_info[i]
                cost = buy["size"] * buy["price"]
                exited_all = info["exit_shares"] >= buy["size"] - 0.02

                if exited_all:
                    pnl = info["exit_revenue"] - cost
                    trades_out.append({"status": "EXIT", "pnl": round(pnl, 2),
                                       "cost": round(cost, 2), "slug": slug})
                elif info["exit_shares"] > 0.001:
                    # Partial exit: split into EXIT + remainder
                    exit_cost = info["exit_shares"] * buy["price"]
                    exit_pnl = info["exit_revenue"] - exit_cost
                    trades_out.append({"status": "EXIT", "pnl": round(exit_pnl, 2),
                                       "cost": round(exit_cost, 2), "slug": slug})
                    remain = buy["size"] - info["exit_shares"]
                    remain_cost = remain * buy["price"]
                    age = now_ts - buy["timestamp"]
                    status = "WIN" if won else ("LOSS" if age > 1800 else "PENDING")
                    remain_pnl = (remain * (1 - buy["price"]) if status == "WIN"
                                  else (-remain_cost if status == "LOSS" else 0))
                    trades_out.append({"status": status, "pnl": round(remain_pnl, 2),
                                       "cost": round(remain_cost, 2), "slug": slug})
                else:
                    age = now_ts - buy["timestamp"]
                    status = "WIN" if won else ("LOSS" if age > 1800 else "PENDING")
                    pnl = (buy["size"] * (1 - buy["price"]) if status == "WIN"
                           else (-cost if status == "LOSS" else 0))
                    trades_out.append({"status": status, "pnl": round(pnl, 2),
                                       "cost": round(cost, 2), "slug": slug})

        # Calculate ROI (dashboard formula)
        total_pnl = sum(t["pnl"] for t in trades_out)
        total_cost = sum(t["cost"] for t in trades_out)
        window_ids = set(t["slug"] for t in trades_out)
        num_windows = len(window_ids)
        avg_trade_cost = total_cost / num_windows if num_windows > 0 else 0
        roi = total_pnl / avg_trade_cost if avg_trade_cost > 0 else 0

        wins = sum(1 for t in trades_out if t["status"] == "WIN")
        losses = sum(1 for t in trades_out if t["status"] == "LOSS")
        exits = sum(1 for t in trades_out if t["status"] == "EXIT")
        pending = sum(1 for t in trades_out if t["status"] == "PENDING")

        print(f"[{ts()}] ACTIVITY_API: {len(all_activity)} events, "
              f"{len(today_trades)} today, {len(redeemed)} redeemed slugs")

        return {
            "total_pnl": total_pnl, "avg_trade_cost": avg_trade_cost,
            "roi": roi, "capital_deployed": total_cost,
            "wins": wins, "losses": losses, "exits": exits,
            "pending": pending, "trades": num_windows
        }
    except Exception as e:
        print(f"[{ts()}] ACTIVITY_API_ERROR: {e}")
        return None
```

**2. Update `check_daily_roi()`** (~line 3244)

Replace `supabase_get_daily_roi(midnight_utc)` call with `get_roi_from_activity_api()`.

Update the log line to show avg_trade_cost and include exits/pending counts.

**3. Fix startup check** (~line 3344)

Change `if not trading_halted:` to always run the API check. If the disk file says halted but it's a new day (API ROI < threshold), clear the halt. This self-corrects stale halt files if the cron fails.

```python
# Always check daily ROI at startup (self-corrects stale halt files)
print("  Checking daily ROI from Activity API...")
halted, roi_data = check_daily_roi()
if halted:
    print(f"  *** HALTED at startup: daily ROI {roi_data['roi']*100:.1f}% >= {ROI_HALT_THRESHOLD*100:.0f}% ***")
elif trading_halted and roi_data and roi_data['roi'] < ROI_HALT_THRESHOLD:
    # Stale halt file from previous day — self-correct
    print(f"  *** Clearing stale halt: daily ROI {roi_data['roi']*100:.1f}% < {ROI_HALT_THRESHOLD*100:.0f}% ***")
    trading_halted = False
    try:
        os.remove(ROI_HALT_STATE_FILE)
    except:
        pass
```

**4. Update version** to v1.49

**5. Keep Supabase `get_daily_roi()` as-is** — no changes needed to `supabase_logger.py`. The function stays for potential fallback or debugging, but is no longer called by the bot.

### Helper needed

Add `midnight_utc_to_unix()` — convert the midnight UTC ISO string to Unix timestamp for filtering activity API events. Simple: `datetime.fromisoformat(s).timestamp()`.

Or simpler: compute midnight as Unix timestamp directly in `get_roi_from_activity_api()` using the existing `get_midnight_est_utc()` logic.

## Implementation Checklist

- [x] Add `get_roi_from_activity_api()` function in `trading_bot_smart.py`
- [x] Update `check_daily_roi()` to call `get_roi_from_activity_api()` instead of Supabase
- [x] Fix startup to always run ROI check + self-correct stale halts
- [x] Update log lines with new data fields (avg_cost, exits, pending)
- [x] Bump version to v1.49
- [x] Update BOT_REGISTRY.md
- [x] Deploy to server
- [x] Verify via bot log that ROI matches dashboard

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| PENDING trades | Include at PnL=0 (match dashboard) | User wants 100% dashboard match |
| API failure | Fail-open (skip check, keep trading) | Same as current Supabase behavior |
| Timeout | 10 seconds | Matches other API calls in bot |
| 1000-event limit | Accept for now (96 windows/day well under limit) | YAGNI — can add pagination if needed |
| ARB trades | Not handled (ARB disabled) | YAGNI — re-evaluate if ARB re-enabled |
| Supabase get_daily_roi() | Keep but don't call | No-cost backup, avoid removing code |
| Startup halt check | Always run, self-correct stale halts | Safety improvement over current skip behavior |

## Dependencies & Risks

- **Polymarket Activity API reliability**: No auth required, public endpoint. If down, bot continues trading (fail-open). Same risk as current dashboard.
- **API schema changes**: If Polymarket changes field names, function breaks silently. Mitigated by structured error handling + log output.
- **30-minute LOSS threshold**: Positions < 30min old are PENDING (pnl=0). This slightly deflates ROI while positions are open. Acceptable since dashboard has same behavior.

## References

- Brainstorm: `docs/brainstorms/2026-02-17-roi-from-activity-api-brainstorm.md`
- Dashboard logic: `dashboard.html:917-1108`
- Current ROI check: `trading_bot_smart.py:3244-3291`
- Current Supabase ROI: `supabase_logger.py:263-357`
- Activity API endpoint: `https://data-api.polymarket.com/activity?user=WALLET&limit=1000`
