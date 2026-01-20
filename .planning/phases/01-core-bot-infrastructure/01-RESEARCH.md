# Phase 1: Core Bot Infrastructure - Research

**Researched:** 2026-01-20
**Domain:** Python bot infrastructure, BTC 15-min window monitoring, Polymarket API
**Confidence:** HIGH

## Summary

This phase builds a standalone Performance Tracker bot that runs alongside the existing trading bot. The core infrastructure involves:

1. **Window detection** - Calculate current BTC 15-min window from Unix timestamp (same pattern as trading bot)
2. **Time remaining tracking** - Fetch market data from Polymarket gamma-api and calculate countdown
3. **Window transition detection** - Compare current slug to last slug, trigger actions on change
4. **Main loop architecture** - Continuous polling with state management

The existing trading bot (`trading_bot_smart.py`) provides battle-tested patterns for all of these. The tracker bot can reuse the exact same logic for window detection and API calls, just without the trading components.

**Primary recommendation:** Copy the window detection patterns (`get_current_slug()`, `get_time_remaining()`, `get_market_data()`) directly from trading_bot_smart.py. They work correctly and are already deployed.

## Standard Stack

The established libraries/tools for this domain:

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Python 3 | 3.10+ | Runtime | Already on server |
| requests | latest | HTTP API calls | Used by trading bot |
| python-dotenv | latest | Environment config | Used by trading bot |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| gspread | latest | Google Sheets API | Phase 3 integration |
| google-auth | latest | Google authentication | Phase 3 integration |
| zoneinfo | stdlib | Timezone handling | Timestamp display |
| json | stdlib | Data serialization | Local state persistence |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| requests | httpx | httpx has async support but trading bot uses requests; consistency wins |
| json file | SQLite | SQLite is overkill for simple window state; JSON matches trading bot pattern |
| polling | websockets | No websocket API available for market data; polling is the only option |

**Installation:**
```bash
# Already installed on server from trading bot
pip3 install python-dotenv requests --break-system-packages
```

## Architecture Patterns

### Recommended Project Structure
```
~/polymarket_bot/           # Same directory as trading bot
├── trading_bot_smart.py    # Existing trading bot (don't modify)
├── performance_tracker.py  # NEW: This phase's main script
├── tracker_log.txt         # Local log file
└── sheets_logger.py        # Shared (optional - Phase 3)
```

### Pattern 1: Window Slug Calculation
**What:** Calculate current BTC 15-min window from Unix timestamp
**When to use:** Every main loop iteration
**Example:**
```python
# Source: trading_bot_smart.py lines 607-610
def get_current_slug():
    current = int(time.time())
    window_start = (current // 900) * 900  # 900 seconds = 15 minutes
    return f"btc-updown-15m-{window_start}", window_start
```

### Pattern 2: Time Remaining Calculation
**What:** Calculate seconds until window closes from market data
**When to use:** After fetching market data
**Example:**
```python
# Source: trading_bot_smart.py lines 624-633
def get_time_remaining(market):
    try:
        end_str = market.get('markets', [{}])[0].get('endDate', '')
        end_time = datetime.fromisoformat(end_str.replace('Z', '+00:00'))
        remaining = (end_time - datetime.now(timezone.utc)).total_seconds()
        if remaining < 0:
            return "ENDED", -1
        return f"{int(remaining)//60:02d}:{int(remaining)%60:02d}", remaining
    except:
        return "??:??", 0
```

### Pattern 3: Market Data Fetch
**What:** Fetch market metadata from Polymarket gamma-api
**When to use:** When window changes or cache needs refresh
**Example:**
```python
# Source: trading_bot_smart.py lines 612-622
def get_market_data(slug):
    try:
        url = f"https://gamma-api.polymarket.com/events?slug={slug}"
        resp = requests.get(url, timeout=3)
        data = resp.json()
        return data[0] if data else None
    except:
        return None
```

### Pattern 4: Window Transition Detection
**What:** Detect when one window ends and another begins
**When to use:** In main loop, compare current vs last slug
**Example:**
```python
# Source: trading_bot_smart.py lines 2391-2449
last_slug = None
while True:
    slug, _ = get_current_slug()

    if slug != last_slug:
        # Window transition detected!
        if last_slug is not None:
            # Process completed window
            grade_window(window_state)

        # Start new window
        window_state = reset_window_state(slug)
        last_slug = slug
```

### Pattern 5: TeeLogger for File + Console Output
**What:** Log to both console and file simultaneously
**When to use:** Bot startup, all print statements
**Example:**
```python
# Source: trading_bot_smart.py lines 62-75
class TeeLogger:
    def __init__(self, filename):
        self.terminal = sys.stdout
        self.log = open(filename, "a", buffering=1)  # Line buffered
    def write(self, message):
        self.terminal.write(message)
        self.log.write(message)
    def flush(self):
        self.terminal.flush()
        self.log.flush()

LOG_FILE = os.path.expanduser("~/polybot/tracker.log")
sys.stdout = TeeLogger(LOG_FILE)
sys.stderr = TeeLogger(LOG_FILE)
```

### Anti-Patterns to Avoid
- **Modifying trading_bot_smart.py:** Creates risk of breaking production trading
- **Sharing state between bots:** Each bot must be fully independent
- **Trusting window calculation from API alone:** Use local slug calculation as ground truth
- **Blocking on API calls:** Use timeouts, handle failures gracefully

## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| 15-min window calculation | Custom time math | `(current // 900) * 900` | Already proven in trading bot |
| Timezone handling | Manual UTC offset | `zoneinfo.ZoneInfo` | Handles DST correctly |
| ISO date parsing | Custom parser | `datetime.fromisoformat()` | Handles Polymarket's format |
| HTTP retries | Custom retry loop | Simple try/except + sleep | Matches trading bot pattern |

**Key insight:** The trading bot has run for months with these patterns. Copy them exactly rather than reinventing.

## Common Pitfalls

### Pitfall 1: API Response Structure
**What goes wrong:** Polymarket API returns nested structure that's easy to misparse
**Why it happens:** Market data is in `data[0]['markets'][0]`, not flat
**How to avoid:** Use exact parsing patterns from trading bot
**Warning signs:** Getting `None` or `KeyError` when accessing market fields

### Pitfall 2: Window Boundary Race Condition
**What goes wrong:** Processing a window that hasn't fully settled
**Why it happens:** API may return stale data near window boundaries
**How to avoid:** Trading bot waits for new window to stabilize; tracker should too
**Warning signs:** Getting ENDED status immediately after window transition

### Pitfall 3: Timezone Confusion
**What goes wrong:** Displaying wrong times in logs/sheets
**Why it happens:** Polymarket uses UTC, local display needs conversion
**How to avoid:** Use `PST = ZoneInfo("America/Los_Angeles")` consistently
**Warning signs:** Times off by 7-8 hours in logs

### Pitfall 4: Missing Credentials
**What goes wrong:** Bot crashes on startup
**Why it happens:** Credentials file path hardcoded incorrectly
**How to avoid:** Use `os.path.expanduser("~/.env")` for home directory
**Warning signs:** `FileNotFoundError` or environment variables returning `None`

### Pitfall 5: Silent API Failures
**What goes wrong:** Bot appears to run but no data collected
**Why it happens:** API calls fail silently, returning `None`
**How to avoid:** Log API failures explicitly, implement health checks
**Warning signs:** Empty window_state at window close

## Code Examples

Verified patterns from the existing trading bot:

### Main Loop Structure
```python
# Source: trading_bot_smart.py lines 2396-2467
def main():
    last_slug = None
    cached_market = None

    while True:
        cycle_start = time.time()

        try:
            slug, _ = get_current_slug()

            # Window transition
            if slug != last_slug:
                if last_slug is not None:
                    # Old window complete - grade it
                    print(f"WINDOW COMPLETE: {last_slug}")

                # New window starting
                window_state = reset_window_state(slug)
                cached_market = None
                last_slug = slug
                print(f"NEW WINDOW: {slug}")

            # Fetch market data
            if not cached_market:
                cached_market = get_market_data(slug)

            if not cached_market:
                time.sleep(0.5)
                continue

            # Calculate time remaining
            time_str, remaining_secs = get_time_remaining(cached_market)

            if remaining_secs < 0:
                time.sleep(2)
                continue

            # Log status
            print(f"T-{remaining_secs:.0f}s | Window: {slug}")

            # Sleep to maintain ~1 second loop
            elapsed = time.time() - cycle_start
            time.sleep(max(0, 1 - elapsed))

        except Exception as e:
            print(f"ERROR: {e}")
            time.sleep(1)
```

### Graceful Shutdown
```python
# Source: trading_bot_smart.py lines 114-119
import signal

def signal_handler(sig, frame):
    print("\n\nCtrl+C pressed. Exiting...")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
```

### Environment Loading
```python
# Source: trading_bot_smart.py lines 83-85
from dotenv import load_dotenv
load_dotenv(os.path.expanduser("~/.env"))
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS")
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Custom window math | `(ts // 900) * 900` formula | Always | Industry standard for 15-min intervals |
| Multiple log files | TeeLogger pattern | v1.0 | Single source of truth |
| Manual time parsing | `datetime.fromisoformat()` | Python 3.7+ | Handles ISO 8601 correctly |

**Deprecated/outdated:**
- Nothing relevant for this phase

## Open Questions

Things that couldn't be fully resolved:

1. **Exact log file location**
   - What we know: Trading bot uses `~/polybot/bot.log`
   - What's unclear: Should tracker use same dir or separate?
   - Recommendation: Use `~/polybot/tracker.log` for separation

2. **API rate limits**
   - What we know: Trading bot polls every 1 second without issues
   - What's unclear: If running two bots, does rate limit apply?
   - Recommendation: Both bots polling gamma-api at 1/sec should be fine (different endpoints used per-window)

3. **Start-of-window behavior**
   - What we know: Trading bot skips first 60s of window for stability
   - What's unclear: Does tracker need this?
   - Recommendation: No, tracker just observes - can start immediately

## Sources

### Primary (HIGH confidence)
- `/Users/luislluis/MarkWatney/trading_bot_smart.py` - Lines 607-633 (window detection), 2338-2700 (main loop)
- `/Users/luislluis/MarkWatney/sheets_logger.py` - Google Sheets integration patterns
- `/Users/luislluis/MarkWatney/CLAUDE.md` - Project documentation, API endpoints

### Secondary (MEDIUM confidence)
- Polymarket gamma-api response structure (observed in trading bot code)
- Server environment (`174.138.5.183`, `~/polymarket_bot/`)

### Tertiary (LOW confidence)
- None - all findings verified against existing code

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - same stack as working trading bot
- Architecture: HIGH - patterns copied from production code
- Pitfalls: HIGH - based on documented issues in trading bot

**Research date:** 2026-01-20
**Valid until:** Indefinite (patterns are stable, no external dependencies)
