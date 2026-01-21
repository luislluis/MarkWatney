# Phase 2: Position Detection - Research

**Researched:** 2026-01-20
**Domain:** Polymarket position detection and P/L calculation
**Confidence:** HIGH

## Summary

This phase implements position detection for the performance tracker bot. The tracker needs to detect what trades the trading bot made each window and grade their outcomes. Analysis of the trading bot (trading_bot_smart.py) reveals well-established patterns for:

1. **Position fetching** via `data-api.polymarket.com/positions`
2. **ARB detection** - positions on BOTH sides (UP and DOWN) of the same window
3. **99c capture detection** - single-side position at high price (99c)
4. **Outcome determination** - market resolution determines winning side
5. **P/L calculation** - straightforward cost/payout arithmetic

The tracker can reuse the exact same API endpoints and logic patterns. Key insight: the tracker only needs to OBSERVE, not trade - this simplifies the implementation significantly.

**Primary recommendation:** Copy position-fetching patterns from trading_bot_smart.py, detect trade types via position analysis (both sides = ARB, single side at 99c = capture), grade outcomes at window close via market resolution.

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| requests | stdlib | HTTP API calls | Already used in performance_tracker.py |
| time | stdlib | Timing operations | Already imported |
| datetime | stdlib | Timestamp handling | Already imported |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| None needed | - | - | All APIs accessible via HTTP |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| raw requests | py-clob-client | Overkill for read-only operations, adds dependency |
| Position API | Order history API | Position API is simpler, already proven |

**Installation:**
No new packages needed - requests already imported in performance_tracker.py.

## Architecture Patterns

### Current Tracker Structure (from Phase 1)
```python
# performance_tracker.py already has:
window_state = {
    'slug': slug,                    # Window identifier
    'arb_entry': None,               # Placeholder for ARB data
    'arb_result': None,              # Placeholder for ARB outcome
    'arb_pnl': 0.0,
    'capture_entry': None,           # Placeholder for 99c data
    'capture_result': None,          # Placeholder for 99c outcome
    'capture_pnl': 0.0,
    'graded': False,
}
```

### Recommended Position Detection Flow
```
Window Start
    |
    v
Poll positions (1x per second or less frequently)
    |
    v
Detect changes: new UP/DOWN positions?
    |
    +---> Both UP and DOWN? --> ARB detected
    |
    +---> Single side at ~99c? --> 99c capture detected
    |
    v
Window End (TTL = 0)
    |
    v
Wait for resolution (market API shows winner)
    |
    v
Grade outcomes:
  - ARB: PAIRED (balanced), BAIL (imbalanced), or NO_TRADE
  - 99c: WIN (bet on winning side) or LOSS (bet on losing side)
    |
    v
Calculate P/L:
  - ARB: payout ($1 per paired share) - cost
  - 99c: win = shares * (1.00 - cost), loss = -cost
```

### Pattern 1: Position Fetching (from trading_bot_smart.py)
```python
# Source: trading_bot_smart.py lines 921-949
def fetch_positions(wallet_address, up_token, down_token):
    """Fetch positions for current window tokens."""
    try:
        url = f"https://data-api.polymarket.com/positions?user={wallet_address.lower()}"
        resp = requests.get(url, timeout=5)
        positions = resp.json()

        up_shares = 0
        down_shares = 0

        for pos in positions:
            asset = pos.get('asset', '')
            size = float(pos.get('size', 0))
            if size > 0:
                if asset == up_token:
                    up_shares = size
                elif asset == down_token:
                    down_shares = size

        return up_shares, down_shares
    except Exception as e:
        print(f"[WARN] Position fetch failed: {e}")
        return 0, 0
```

### Pattern 2: Getting Token IDs from Market Data
```python
# Source: trading_bot_smart.py lines 635-643
def get_token_ids(market):
    """Extract UP and DOWN token IDs from market data."""
    clob_ids = market.get('markets', [{}])[0].get('clobTokenIds', '')
    clob_ids = clob_ids.replace('[', '').replace(']', '').replace('"', '')
    tokens = [t.strip() for t in clob_ids.split(',')]
    if len(tokens) >= 2:
        return tokens[0], tokens[1]  # UP token, DOWN token
    return None, None
```

### Pattern 3: Detecting Trade Types
```python
def detect_trade_type(up_shares, down_shares, up_avg_price=None, down_avg_price=None):
    """
    Determine what type of trade was made.

    ARB: Both sides have shares (balanced or imbalanced)
    99c CAPTURE: Single side with price >= 0.98 (if price available)
    NO_TRADE: No positions
    """
    if up_shares == 0 and down_shares == 0:
        return 'NO_TRADE'

    if up_shares > 0 and down_shares > 0:
        # Both sides = ARB trade
        return 'ARB'

    # Single side - check if it's a 99c capture
    # Heuristic: 99c captures are typically placed at 99c
    # Without price data, we can only detect single-side exposure
    return '99C_CAPTURE'  # or 'SINGLE_SIDE' if unsure
```

### Pattern 4: Market Resolution (from auto_redeem.py)
```python
# Source: auto_redeem.py lines 186-205
def get_market_resolution(condition_id):
    """Check if market resolved and which side won."""
    try:
        url = f"https://clob.polymarket.com/markets/{condition_id}"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            tokens = data.get('tokens', [])
            for token in tokens:
                if token.get('winner') == True:
                    return {
                        'resolved': True,
                        'winner': token.get('outcome'),
                        'token_id': token.get('token_id')
                    }
            return {'resolved': False}
    except:
        pass
    return {'resolved': False}
```

### Pattern 5: P/L Calculation for ARB
```python
# Source: trading_bot_smart.py lines 557-601
def calculate_arb_pnl(up_shares, down_shares, avg_up_price, avg_down_price):
    """
    Calculate ARB P/L.

    For balanced pair: payout = min(up, down) * $1.00
    Cost = (avg_up * up_shares) + (avg_down * down_shares)
    Profit = payout - cost

    If combined cost < 99c: guaranteed profit
    If combined cost >= 99c: loss capped by pairing
    """
    min_shares = min(up_shares, down_shares)
    total_cost = (avg_up_price * up_shares) + (avg_down_price * down_shares)
    payout = min_shares * 1.00  # Winner always pays $1
    profit = payout - total_cost
    return profit
```

### Pattern 6: P/L Calculation for 99c Capture
```python
def calculate_99c_pnl(side, shares, entry_price, winning_side):
    """
    Calculate 99c capture P/L.

    WIN: Bet on winning side
        payout = shares * $1.00
        cost = shares * entry_price
        profit = payout - cost = shares * (1.00 - entry_price)

    LOSS: Bet on losing side
        payout = $0
        cost = shares * entry_price
        loss = -cost
    """
    cost = shares * entry_price
    if side == winning_side:
        # WIN - position pays out $1 per share
        payout = shares * 1.00
        return payout - cost, 'WIN'
    else:
        # LOSS - position worth $0
        return -cost, 'LOSS'
```

### Anti-Patterns to Avoid
- **Fetching positions too frequently:** Once per second is plenty; positions don't change that fast
- **Assuming price data is available:** The position API gives shares, not entry price - may need to estimate or track during window
- **Grading before resolution:** Wait for market to resolve before determining win/loss
- **Hardcoding token order:** UP/DOWN token order comes from API, don't assume

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Position fetching | Custom blockchain query | `data-api.polymarket.com/positions` | Simple HTTP, already working |
| Market resolution | Manual price comparison | `clob.polymarket.com/markets/{id}` | API returns `winner: true/false` |
| Wallet address | Manual entry | Load from `~/.env` | Already set up for trading bot |

**Key insight:** The trading bot already proved these APIs work. Reuse the patterns exactly.

## Common Pitfalls

### Pitfall 1: Position API Returns All Positions
**What goes wrong:** API returns positions from ALL markets, not just current window
**Why it happens:** Wallet may have positions in multiple markets
**How to avoid:** Filter by token ID - only count positions matching current window's UP/DOWN tokens
**Warning signs:** P/L numbers don't match expected values

### Pitfall 2: Missing Entry Price
**What goes wrong:** Position API returns shares but not entry price
**Why it happens:** Position API only tracks current state, not trade history
**How to avoid:** Either:
  - Track price when position first appears (requires polling)
  - Use orders API to get fill prices
  - Estimate from typical bot behavior (ARB ~42c cheap side, 99c capture at 99c)
**Warning signs:** P/L calculations are wrong or impossible

### Pitfall 3: Race Condition at Window End
**What goes wrong:** Grading happens before positions settle
**Why it happens:** Blockchain settlement takes seconds
**How to avoid:** Wait 3-5 seconds after window end before grading (already in tracker)
**Warning signs:** Missing positions, incorrect grades

### Pitfall 4: Confusing ARB with 99c Capture
**What goes wrong:** Treating hedged 99c capture (both sides) as ARB
**Why it happens:** Hedged 99c capture looks like ARB (has both UP and DOWN)
**How to avoid:** Track position changes during window:
  - ARB: Both sides appear at roughly same time (within seconds)
  - 99c with hedge: Single side first, then other side appears later (usually with price reversal)
**Warning signs:** P/L calculations off, wrong trade type classification

### Pitfall 5: Market Not Yet Resolved
**What goes wrong:** Trying to grade before market resolution API returns winner
**Why it happens:** Resolution can take a few seconds after window closes
**How to avoid:** Retry resolution check with backoff, or poll until resolved
**Warning signs:** `resolved: false` from API, missing outcome data

## Code Examples

### Example 1: Complete Position Detection Function
```python
# Source: Composite of trading_bot_smart.py patterns

def detect_window_positions(wallet_address, market):
    """
    Detect positions in current window.

    Returns:
        {
            'up_shares': float,
            'down_shares': float,
            'up_token': str,
            'down_token': str,
            'trade_type': 'ARB' | '99C_CAPTURE' | 'NO_TRADE',
            'arb_balanced': bool (True if equal shares)
        }
    """
    # Get token IDs for this window
    up_token, down_token = get_token_ids(market)
    if not up_token or not down_token:
        return {'trade_type': 'NO_TRADE', 'up_shares': 0, 'down_shares': 0}

    # Fetch current positions
    up_shares, down_shares = fetch_positions(wallet_address, up_token, down_token)

    # Determine trade type
    if up_shares == 0 and down_shares == 0:
        trade_type = 'NO_TRADE'
    elif up_shares > 0 and down_shares > 0:
        trade_type = 'ARB'
    else:
        trade_type = '99C_CAPTURE'

    return {
        'up_shares': up_shares,
        'down_shares': down_shares,
        'up_token': up_token,
        'down_token': down_token,
        'trade_type': trade_type,
        'arb_balanced': abs(up_shares - down_shares) < 0.5
    }
```

### Example 2: Complete Window Grading Function
```python
def grade_completed_window(window_state, market):
    """
    Grade a completed window's trades.
    Called after window closes and positions settle.
    """
    # Get market condition_id for resolution check
    condition_id = market.get('markets', [{}])[0].get('conditionId')

    # Check resolution
    resolution = get_market_resolution(condition_id)
    if not resolution.get('resolved'):
        return None  # Can't grade yet

    winning_side = resolution['winner']  # 'UP' or 'DOWN'

    # Grade based on trade type
    if window_state['trade_type'] == 'NO_TRADE':
        return {
            'arb_result': '-',
            'arb_pnl': 0,
            'capture_result': '-',
            'capture_pnl': 0
        }

    if window_state['trade_type'] == 'ARB':
        return grade_arb_trade(window_state, winning_side)

    if window_state['trade_type'] == '99C_CAPTURE':
        return grade_99c_trade(window_state, winning_side)
```

### Example 3: ARB Result Classification
```python
def classify_arb_result(up_shares, down_shares):
    """
    Classify ARB result based on share balance.

    PAIRED: Equal shares (difference < 0.5)
    BAIL: Position was exited early (only one side remains)
    LOPSIDED: Unequal shares (pairing failed)
    """
    diff = abs(up_shares - down_shares)

    if diff < 0.5:  # MICRO_IMBALANCE_TOLERANCE from trading bot
        return 'PAIRED'
    elif up_shares == 0 or down_shares == 0:
        return 'BAIL'  # One side was sold
    else:
        return 'LOPSIDED'  # Both sides but unequal
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Poll orders API | Poll positions API | Current | Simpler, more reliable |
| Trust local state | Verify with API | v1.4+ | Prevents stale data bugs |

**Deprecated/outdated:**
- Relying solely on local tracking (trading bot learned this lesson)
- Single-source position data (now uses dual verification in trading bot)

## Open Questions

1. **How to get entry price?**
   - Position API doesn't include entry price
   - Options: Track when position first appears, use orders API, or estimate
   - Recommendation: For v1, use estimates (42c for ARB cheap side, 99c for capture). Consider orders API for v2.

2. **How to distinguish 99c capture from regular single-side exposure?**
   - Without price data, can't be 100% certain
   - Heuristic: If position appeared late in window (T-300s to T-0s) and is single-side, likely 99c capture
   - Recommendation: Track timing of position appearance

3. **What if market never resolves?**
   - Rare but possible (market cancelled, etc.)
   - Recommendation: Timeout after 60 seconds of polling, mark as 'UNRESOLVED'

4. **What about HARD_FLATTEN trades?**
   - These sell one side at a loss
   - Would appear as reduced/zero position on one side
   - Recommendation: Detect by tracking position changes during window

## Sources

### Primary (HIGH confidence)
- `/Users/luislluis/MarkWatney/trading_bot_smart.py` - Direct code analysis
  - Lines 921-949: Position API usage
  - Lines 635-643: Token ID extraction
  - Lines 545-601: P/L calculation logic
  - Lines 364-402: window_state structure
- `/Users/luislluis/MarkWatney/auto_redeem.py` - Market resolution API
  - Lines 186-205: `get_market_resolution()` function
- `/Users/luislluis/MarkWatney/sheets_logger.py` - Window logging structure
  - Lines 56-69: WINDOWS_HEADERS showing expected data fields

### Secondary (MEDIUM confidence)
- Polymarket API documentation (via code patterns in trading bot)
- Trading bot behavior observed in logs

### Tertiary (LOW confidence)
- None

## Metadata

**Confidence breakdown:**
- Position fetching: HIGH - exact same API used by trading bot
- Trade type detection: HIGH - clear heuristics from trading bot behavior
- P/L calculation: HIGH - arithmetic verified in trading bot
- Entry price detection: MEDIUM - may need estimation without orders API
- Edge cases (bail, hard flatten): MEDIUM - may need iteration

**Research date:** 2026-01-20
**Valid until:** 60 days (APIs stable, same as trading bot uses)
