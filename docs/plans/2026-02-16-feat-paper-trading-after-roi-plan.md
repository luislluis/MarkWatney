---
title: Paper Trading Mode After ROI Target
type: feat
date: 2026-02-16
---

# feat: Paper Trading Mode After ROI Target

## Overview

When the bot's cumulative ROI hits ~45%, it transitions to "paper mode" — continuing to run the exact same trading logic but simulating fills instead of placing real orders. All paper trades are logged to the same Supabase/Sheets tables with a `PAPER_` prefix so we can track how the strategy would have performed beyond the ROI cutoff.

## Problem Statement / Motivation

Once the bot hits a good ROI, we want to lock in gains and stop risking real money. But we still want to collect data on what the strategy *would* have done — this validates the strategy over longer time periods and helps tune parameters for future sessions.

## Proposed Solution

**Paper Mode Gate** — a single `paper_mode` boolean that gates all order execution. The bot runs identically (same opportunity detection, same order book checks, same PnL calculations) but the 3 core order functions return simulated results instead of calling the CLOB API.

### Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| ROI denominator | `total_pnl / total_capital_deployed` | Most intuitive; capital deployed = sum of (shares * fill_price) across all real trades |
| One-way gate | Yes, irreversible per session | Simpler, safer — no oscillation between modes |
| Check timing | Window boundaries only | Avoids mid-window transitions with live positions |
| Corrupted state file | Default to paper mode | Safe default — better to paper-trade unnecessarily than trade real money accidentally |
| Fill simulation | Ask <= bid at opportunity detection time | Simple, accurate enough for 99c captures |
| Exit simulation | Yes, simulate hard stops and early exits | Ensures paper results are comparable to what real trading would produce |
| Logging approach | `PAPER_` event prefix + `"paper": true` in Details JSON | Zero-migration, works with existing Supabase schema |
| Telegram | Send with `[PAPER]` prefix | Maintains visibility without confusion |
| State file location | `~/polybot/paper_mode_state.json` | Alongside logs, not code — avoids accidental overwrite during deploys |
| CLOB client | Still initialize (for order book reads) | Order book data needed for opportunity detection and fill simulation |
| Manual override | `FORCE_PAPER_MODE=true` env var | Enables testing without waiting for 45% ROI |

## Technical Considerations

### Defense-in-Depth

Even though calling code checks `paper_mode` before attempting orders, the 3 core order functions (`place_limit_order`, `place_fok_market_sell`, `place_and_verify_order`) will have an independent guard that returns a simulated result when `paper_mode == True`. This prevents any code path from accidentally placing a real order.

### Transition Window Safety

Paper mode only activates at window boundaries (between windows). The last real window always completes fully — real positions settle, real PnL is calculated, real auto_redeem runs. Paper mode takes effect starting with the *next* window.

### State Persistence

`paper_mode_state.json` is written atomically (write to temp file, then rename) to prevent corruption on crash. On startup, if the file exists and is valid, the bot resumes in paper mode. If corrupted, it defaults to paper mode and logs a loud warning.

## Acceptance Criteria

- [x] Bot transitions to paper mode when cumulative ROI >= 45% at a window boundary
- [x] Paper mode persists across bot restarts via `paper_mode_state.json`
- [x] No real CLOB API order calls are made while in paper mode (defense-in-depth guard)
- [x] Paper fills are simulated by checking ask <= bid price in the order book
- [x] Paper trades logged to Supabase Events table with `PAPER_` event prefix
- [x] Paper ticks logged to Supabase Ticks table with `PAPER_` status prefix
- [x] Paper windows logged to Sheets Windows tab with paper indicator
- [x] Telegram notifications sent with `[PAPER]` prefix
- [x] Auto_redeem skipped entirely while in paper mode
- [x] `FORCE_PAPER_MODE=true` env var forces paper mode for testing
- [x] Exit logic (hard stop, OB exit) is simulated in paper mode
- [x] BOT_VERSION updated with new version number and codename

## Implementation Plan

### Phase 1: Core Paper Mode Infrastructure

**trading_bot_smart.py**

**1.1 Add paper mode constants and state (top of file, near other feature flags ~line 245)**

```python
# Paper Trading Mode
PAPER_MODE_ROI_THRESHOLD = 0.45        # 45% ROI triggers paper mode
PAPER_MODE_STATE_FILE = os.path.expanduser("~/polybot/paper_mode_state.json")
FORCE_PAPER_MODE = os.getenv("FORCE_PAPER_MODE", "").lower() == "true"
```

**1.2 Add paper mode global state (near session_stats ~line 3231)**

```python
paper_mode = False
paper_stats = {
    "paper_pnl": 0.0,
    "paper_windows": 0,
    "paper_wins": 0,
    "paper_losses": 0,
}
capital_deployed = 0.0  # Sum of (shares * fill_price) for all real trades
```

**1.3 Add paper state persistence functions (new, near save_trades ~line 3165)**

- `save_paper_state()` — atomic write to `PAPER_MODE_STATE_FILE`
  - Schema: `{"paper_mode": true, "activated_at": "ISO timestamp", "trigger_roi": float, "real_pnl_at_activation": float, "capital_deployed": float, "paper_stats": {...}}`
  - Write to temp file first, then `os.rename()` for atomicity
- `load_paper_state()` — read on startup, return dict or None
  - If file missing: return None (start in real mode)
  - If file corrupted: return `{"paper_mode": True}` (safe default), log warning

**1.4 Defense-in-depth guards in order functions**

- `place_limit_order()` (~line 1201): Add at the very top, before any validation:
  ```python
  if paper_mode:
      # Simulate: check if ask <= our bid price
      return (True, f"PAPER_{int(time.time())}")
  ```
- `place_fok_market_sell()` (~line 1246): Same guard:
  ```python
  if paper_mode:
      return (True, f"PAPER_{int(time.time())}", shares)
  ```
- `place_and_verify_order()` (~line 1664): Same guard:
  ```python
  if paper_mode:
      return (True, f"PAPER_{int(time.time())}", "PAPER_FILLED")
  ```

**1.5 Paper fill simulation logic**

For 99c capture (the active strategy): When `execute_99c_capture()` fires in paper mode:
- Check current ask price for the winning side
- If `ask <= 0.99`: simulate fill, record paper shares and fill price (use ask price as fill price)
- If `ask > 0.99`: simulate no fill (order would have sat unfilled)
- Update `window_state` with paper fill data normally (same fields)

For exits (hard stop, OB exit, early bail): When exit logic triggers in paper mode:
- Record paper sell at the trigger price (best bid for FOK, target price for limit)
- Calculate paper PnL from simulated entry and exit prices

### Phase 2: ROI Tracking and Trigger

**2.1 Track capital deployed (in the main loop, when real fills are detected)**

Every time a real fill is confirmed (order status check shows fill), add to `capital_deployed`:
```python
capital_deployed += filled_shares * fill_price
```

Key locations:
- After ARB fill detection (~line 2574-2608)
- After 99c capture fill detection (~line 2137)
- After pairing mode fills (~line 3008-3127)

**2.2 ROI check at window boundaries (~line 3247, after session_stats PnL update)**

```python
if not paper_mode and capital_deployed > 0:
    roi = session_stats['pnl'] / capital_deployed
    if roi >= PAPER_MODE_ROI_THRESHOLD:
        paper_mode = True
        save_paper_state()
        log_event("PAPER_MODE_ACTIVATED", slug, pnl=session_stats['pnl'],
                  details=json.dumps({"roi": roi, "capital_deployed": capital_deployed}))
        send_telegram(f"PAPER MODE ACTIVATED | ROI: {roi*100:.1f}% | PnL: ${session_stats['pnl']:.2f}")
```

**2.3 Load paper state on startup (~line 3174, in main() before main loop)**

```python
state = load_paper_state()
if state and state.get("paper_mode"):
    paper_mode = True
    paper_stats = state.get("paper_stats", paper_stats)
    capital_deployed = state.get("capital_deployed", 0.0)
    print(f"[{ts()}] PAPER MODE RESUMED from state file")
    log_event("PAPER_MODE_RESUMED", "", details=json.dumps(state))

if FORCE_PAPER_MODE:
    paper_mode = True
    print(f"[{ts()}] PAPER MODE FORCED via FORCE_PAPER_MODE env var")
```

### Phase 3: Logging Integration

**3.1 Event logging with PAPER prefix**

Modify `log_event()` wrapper (~line 86) to prepend `PAPER_` to event types when in paper mode:
```python
def log_event(event_type, window_id, **kwargs):
    if paper_mode:
        event_type = f"PAPER_{event_type}"
        kwargs.setdefault('details', '{}')
        # Inject paper:true into details JSON
        details = json.loads(kwargs['details']) if isinstance(kwargs['details'], str) else {}
        details['paper'] = True
        kwargs['details'] = json.dumps(details)
    supabase_log_event(event_type, window_id, **kwargs)
    sheets_log_event(event_type, window_id, **kwargs)
```

**3.2 Tick logging with PAPER status prefix**

Modify `buffer_tick()` call (~line 67) to prefix Status when in paper mode:
```python
status_val = f"PAPER_{status}" if paper_mode else status
```

**3.3 Telegram notifications with [PAPER] prefix**

Modify `send_telegram()` calls or add a wrapper that prepends `[PAPER]` when `paper_mode == True`. Affects:
- 99c fill notifications
- 99c resolution notifications
- Window summary messages

### Phase 4: Suppress Real Activity in Paper Mode

**4.1 Skip auto_redeem (~line 3327-3335)**

```python
if not paper_mode:
    from auto_redeem import check_and_claim
    claimable = check_and_claim()
    # ... existing logic
```

**4.2 Skip cancel_all_orders at window boundaries (~line 3337)**

```python
if not paper_mode:
    cancel_all_orders()
```

**4.3 Paper PnL tracking at window end**

After the existing PnL calculation (~line 3258-3296), if in paper mode:
```python
if paper_mode:
    paper_stats['paper_pnl'] += window_state['realized_pnl_usd']
    paper_stats['paper_windows'] += 1
    if window_state['realized_pnl_usd'] > 0:
        paper_stats['paper_wins'] += 1
    else:
        paper_stats['paper_losses'] += 1
    save_paper_state()
```

### Phase 5: Status Display Updates

**5.1 Main loop status line**

Update the per-second status display to show paper mode:
```
[HH:MM:SS] PAPER_IDLE | T-XXXs | UP:XXc DN:XXc | paper:X/X | reason
```

**5.2 Window summary display**

At window end, show paper stats:
```
[PAPER] Window complete | PnL: $X.XX | Cumulative Paper PnL: $X.XX (W/L: X/X)
```

**5.3 Startup banner**

Add paper mode status to the bot startup banner (near BOT_VERSION display):
```
PAPER MODE: ACTIVE (triggered at XX.X% ROI)
```

### Phase 6: Version Update

Update `BOT_VERSION` at top of `trading_bot_smart.py`:
```python
BOT_VERSION = {
    "version": "v1.20",
    "codename": "Ghost Runner",
    "date": "2026-02-16",
    "changes": "Paper trading mode: simulates trades after 45% ROI target hit"
}
```

Update `BOT_REGISTRY.md` with the new version entry.

## Success Metrics

- Paper mode activates correctly when ROI >= 45%
- Zero real orders placed after paper mode activation (verifiable in CLOB API logs)
- Paper PnL tracked accurately — compare paper 99c outcomes against actual market settlements
- Paper mode persists correctly across restarts
- Dashboard can filter paper vs real trades by event prefix

## Dependencies & Risks

| Risk | Mitigation |
|------|------------|
| Accidental real order in paper mode | Defense-in-depth guard in all 3 order functions |
| State file corruption on crash | Atomic write (temp + rename), safe default to paper mode |
| Order book stale during paper fill sim | Log warning if no asks for 3+ ticks, skip simulation |
| ROI calculation drift | Persist `capital_deployed` in state file, load on restart |
| Deploy overwrites state file | State file at `~/polybot/` (not `~/polymarket_bot/`), excluded from scp |

## Open Questions

1. **ROI denominator confirmation**: Plan uses `total_pnl / total_capital_deployed`. If you prefer a fixed bankroll number (e.g., "45% of $50"), let me know.
2. **Manual re-entry to real mode**: Currently paper mode is one-way. Do you want a `FORCE_REAL_MODE=true` env var to override?
3. **Paper ticks volume**: Paper ticks add ~900 rows/window to the Ticks table (same as real). Want to reduce frequency or skip them?

## References & Research

- Brainstorm: `docs/brainstorms/2026-02-16-paper-trade-after-roi-brainstorm.md`
- Order placement functions: `trading_bot_smart.py:1201` (place_limit_order), `:1246` (place_fok_market_sell), `:1664` (place_and_verify_order)
- Session stats: `trading_bot_smart.py:3231`
- Window state reset: `trading_bot_smart.py:445`
- Supabase logger: `supabase_logger.py:129` (log_event), `:66` (buffer_tick)
- Sheets logger: `sheets_logger.py:217` (log_event), `:388` (buffer_tick)
- Feature flag pattern: `trading_bot_smart.py:245` (ARB_ENABLED)
- Auto-redeem integration: `trading_bot_smart.py:3327`
- State persistence pattern: `trading_bot_smart.py:3165` (save_trades)
