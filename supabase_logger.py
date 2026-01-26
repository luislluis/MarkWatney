"""
Supabase Logger for Polymarket Trading Bot
==========================================
Logs trading ticks to Supabase for real-time analysis.
"""

import os
from datetime import datetime
from typing import Optional, Dict, Any, List
from zoneinfo import ZoneInfo

PST = ZoneInfo("America/Los_Angeles")

# Try to import supabase
try:
    from supabase import create_client, Client
    SUPABASE_AVAILABLE = True
except ImportError:
    SUPABASE_AVAILABLE = False
    print("[SUPABASE] supabase-py not installed. Run: pip install supabase")

# Configuration from environment
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://qszosdrmnoglrkttdevz.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

# Table names (with spaces, matching Google Sheets import)
TICKS_TABLE = "Polymarket Bot Log - Ticks"
EVENTS_TABLE = "Polymarket Bot Log - Events"

# Buffer configuration
TICK_FLUSH_INTERVAL = 10  # Flush every 10 seconds


class SupabaseLogger:
    """Supabase logger for trading bot ticks."""

    def __init__(self):
        self.client: Optional[Client] = None
        self.enabled = False
        self._tick_buffer: List[Dict] = []
        self._last_flush = datetime.now()
        self._initialized = False

    def init(self) -> bool:
        """Initialize Supabase connection."""
        if not SUPABASE_AVAILABLE:
            print("[SUPABASE] supabase-py not available")
            return False

        if not SUPABASE_URL or not SUPABASE_KEY:
            print("[SUPABASE] Missing SUPABASE_URL or SUPABASE_KEY")
            return False

        try:
            self.client = create_client(SUPABASE_URL, SUPABASE_KEY)
            self.enabled = True
            self._initialized = True
            print(f"[SUPABASE] Connected to Supabase")
            return True
        except Exception as e:
            print(f"[SUPABASE] Failed to connect: {e}")
            return False

    def buffer_tick(self, window_id: str, ttc: float, status: str,
                    ask_up: float, ask_down: float,
                    up_shares: float, down_shares: float,
                    btc_price: float = None, up_imb: float = None, down_imb: float = None,
                    danger_score: float = None, reason: str = ""):
        """Buffer a tick for batch upload."""
        # Use column names matching the existing table (with spaces)
        self._tick_buffer.append({
            "Timestamp": datetime.now(PST).isoformat(),
            "Window ID": window_id,
            "TTL": int(ttc) if ttc else 0,
            "Status": status,
            "UP Ask": str(round(ask_up, 4)) if ask_up else None,
            "DN Ask": str(round(ask_down, 4)) if ask_down else None,
            "UP Pos": str(round(up_shares, 2)) if up_shares else "0",
            "DN Pos": str(round(down_shares, 2)) if down_shares else "0",
            "BTC": str(round(btc_price, 2)) if btc_price else None,
            "UP Imb": str(round(up_imb, 4)) if up_imb else None,
            "DN Imb": str(round(down_imb, 4)) if down_imb else None,
            "Reason": reason[:100] if reason else None,
            "Reason 2": str(danger_score) if danger_score else None
        })

    def flush_ticks(self) -> bool:
        """Flush buffered ticks to Supabase."""
        if not self._tick_buffer:
            return True

        if not self.enabled or not self.client:
            self._tick_buffer = []
            return False

        try:
            # Insert batch to Supabase
            result = self.client.table(TICKS_TABLE).insert(self._tick_buffer).execute()
            count = len(self._tick_buffer)
            self._tick_buffer = []
            self._last_flush = datetime.now()
            print(f"[SUPABASE] Flushed {count} ticks")
            return True
        except Exception as e:
            print(f"[SUPABASE] Failed to flush ticks: {e}")
            # Keep buffer for retry, but limit size
            if len(self._tick_buffer) > 500:
                self._tick_buffer = self._tick_buffer[-200:]
            return False

    def maybe_flush_ticks(self, ttl: float = None) -> bool:
        """Flush ticks if enough time has passed.

        Args:
            ttl: Time to close (seconds). If < 60, skip flush to protect critical trading period.
        """
        # Don't flush in final 60 seconds - protect trading operations
        if ttl is not None and ttl < 60:
            return True  # Skip, keep buffer for later

        elapsed = (datetime.now() - self._last_flush).total_seconds()
        if elapsed >= TICK_FLUSH_INTERVAL:
            return self.flush_ticks()
        return True

    def log_event(self, event_type: str, window_id: str = "", side: str = "",
                  shares: float = 0, price: float = 0, pnl: float = 0,
                  details: str = ""):
        """Log a trading event to Supabase."""
        if not self.enabled or not self.client:
            return False

        try:
            data = {
                "Timestamp": datetime.now(PST).isoformat(),
                "Event": event_type,
                "Window ID": window_id,
                "Side": side,
                "Shares": str(shares) if shares else None,
                "Price": str(price) if price else None,
                "PnL": str(pnl) if pnl else None,
                "Details": details[:500] if details else None
            }
            self.client.table(EVENTS_TABLE).insert(data).execute()
            return True
        except Exception as e:
            print(f"[SUPABASE] Failed to log event: {e}")
            return False


# Global logger instance
_logger: Optional[SupabaseLogger] = None


def init_supabase_logger() -> bool:
    """Initialize the global Supabase logger."""
    global _logger
    _logger = SupabaseLogger()
    return _logger.init()


def buffer_tick(window_id: str, ttc: float, status: str,
                ask_up: float, ask_down: float,
                up_shares: float, down_shares: float,
                btc_price: float = None, up_imb: float = None, down_imb: float = None,
                danger_score: float = None, reason: str = ""):
    """Buffer a tick for batch upload."""
    if _logger and _logger.enabled:
        _logger.buffer_tick(window_id, ttc, status, ask_up, ask_down,
                           up_shares, down_shares, btc_price,
                           up_imb, down_imb, danger_score, reason)


def maybe_flush_ticks(ttl: float = None) -> bool:
    """Flush ticks if enough time has passed.

    Args:
        ttl: Time to close (seconds). If < 60, skip flush to protect critical trading period.
    """
    if _logger and _logger.enabled:
        return _logger.maybe_flush_ticks(ttl)
    return True


def flush_ticks() -> bool:
    """Force flush all buffered ticks."""
    if _logger and _logger.enabled:
        return _logger.flush_ticks()
    return True


def log_event(event_type: str, **kwargs):
    """Log a trading event."""
    if _logger and _logger.enabled:
        return _logger.log_event(event_type, **kwargs)
    return False


# Test function
if __name__ == "__main__":
    import os
    os.environ["SUPABASE_KEY"] = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InFzem9zZHJtbm9nbHJrdHRkZXZ6Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2OTE5ODU1NiwiZXhwIjoyMDg0Nzc0NTU2fQ.Mu1VuL7Y-FI-LDycHbAfgcqjSYFufAqjwHiSoo8aUzs"

    if init_supabase_logger():
        buffer_tick("test-window", 100, "TEST", 0.45, 0.55, 0, 0, 87000, 0.5, -0.5, 0, "test tick")
        flush_ticks()
        print("✓ Test complete!")
