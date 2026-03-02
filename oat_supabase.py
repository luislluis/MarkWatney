#!/usr/bin/env python3
"""
Supabase push layer for the Strategy Extractor Bot.
Pushes observations, analysis results, and fills to Supabase
for the web dashboard to consume.

Follows supabase_logger.py pattern: buffered writes, background threads.
"""

import os
import json
import threading
from datetime import datetime
from typing import Optional, Dict, List
from zoneinfo import ZoneInfo

EST = ZoneInfo("America/New_York")

try:
    from supabase import create_client, Client
    SUPABASE_AVAILABLE = True
except ImportError:
    SUPABASE_AVAILABLE = False

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://qszosdrmnoglrkttdevz.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

# Table names
OAT_OBSERVATIONS_TABLE = "oat_observations"
OAT_ANALYSIS_TABLE = "oat_analysis"
OAT_FILLS_TABLE = "oat_fills"

FILL_FLUSH_INTERVAL = 30  # seconds


class OatSupabaseLogger:
    """Pushes strategy extractor data to Supabase for dashboard."""

    def __init__(self):
        self.client: Optional[Client] = None
        self.enabled = False
        self._fill_buffer: List[Dict] = []
        self._last_fill_flush = datetime.now()

    def init(self) -> bool:
        if not SUPABASE_AVAILABLE:
            print("[OAT_SUPABASE] supabase-py not available")
            return False
        if not SUPABASE_URL or not SUPABASE_KEY:
            print("[OAT_SUPABASE] Missing SUPABASE_URL or SUPABASE_KEY")
            return False
        try:
            self.client = create_client(SUPABASE_URL, SUPABASE_KEY)
            self.enabled = True
            print("[OAT_SUPABASE] Connected")
            return True
        except Exception as e:
            print(f"[OAT_SUPABASE] Failed to connect: {e}")
            return False

    def push_observation(self, obs: dict):
        """Push a window observation to Supabase (background thread)."""
        if not self.enabled or not self.client:
            return
        data = {
            "slug": obs.get("slug"),
            "window_start": obs.get("window_start"),
            "target_traded": obs.get("target_traded", False),
            "target_sides": obs.get("target_sides"),
            "target_total_buys": obs.get("target_total_buys", 0),
            "target_total_sells": obs.get("target_total_sells", 0),
            "target_up_shares": obs.get("target_up_shares", 0),
            "target_down_shares": obs.get("target_down_shares", 0),
            "target_up_avg_price": obs.get("target_up_avg_price", 0),
            "target_down_avg_price": obs.get("target_down_avg_price", 0),
            "target_up_total_usdc": obs.get("target_up_total_usdc", 0),
            "target_down_total_usdc": obs.get("target_down_total_usdc", 0),
            "target_first_buy_offset_secs": obs.get("target_first_buy_offset_secs"),
            "target_first_buy_side": obs.get("target_first_buy_side"),
            "target_leg_gap_secs": obs.get("target_leg_gap_secs"),
            "target_combined_cost": obs.get("target_combined_cost"),
            "target_maker_count": obs.get("target_maker_count", 0),
            "target_taker_count": obs.get("target_taker_count", 0),
            "up_ask_at_entry": obs.get("up_ask_at_entry"),
            "down_ask_at_entry": obs.get("down_ask_at_entry"),
            "ob_imbalance_at_entry": obs.get("ob_imbalance_at_entry"),
            "time_remaining_at_entry": obs.get("time_remaining_at_entry"),
            "outcome": obs.get("outcome"),
        }

        def _do():
            try:
                self.client.table(OAT_OBSERVATIONS_TABLE).upsert(
                    data, on_conflict="slug"
                ).execute()
            except Exception as e:
                print(f"[OAT_SUPABASE] push_observation error: {e}")

        threading.Thread(target=_do, daemon=True).start()

    def push_analysis(self, result: dict):
        """Push analysis results to Supabase (background thread)."""
        if not self.enabled or not self.client:
            return
        data = {
            "run_timestamp": result.get("run_timestamp"),
            "sample_start": result.get("sample_start"),
            "sample_end": result.get("sample_end"),
            "sample_size": result.get("sample_size"),
            "entry_timing_confidence": result.get("entry_timing_confidence", 0),
            "side_selection_confidence": result.get("side_selection_confidence", 0),
            "pricing_confidence": result.get("pricing_confidence", 0),
            "sizing_confidence": result.get("sizing_confidence", 0),
            "arb_structure_confidence": result.get("arb_structure_confidence", 0),
            "exit_behavior_confidence": result.get("exit_behavior_confidence", 0),
            "overall_readiness": result.get("overall_readiness", 0),
            "entry_timing_data": json.dumps(result.get("entry_timing_data")) if isinstance(result.get("entry_timing_data"), dict) else result.get("entry_timing_data"),
            "side_selection_data": json.dumps(result.get("side_selection_data")) if isinstance(result.get("side_selection_data"), dict) else result.get("side_selection_data"),
            "pricing_data": json.dumps(result.get("pricing_data")) if isinstance(result.get("pricing_data"), dict) else result.get("pricing_data"),
            "sizing_data": json.dumps(result.get("sizing_data")) if isinstance(result.get("sizing_data"), dict) else result.get("sizing_data"),
            "arb_structure_data": json.dumps(result.get("arb_structure_data")) if isinstance(result.get("arb_structure_data"), dict) else result.get("arb_structure_data"),
            "exit_behavior_data": json.dumps(result.get("exit_behavior_data")) if isinstance(result.get("exit_behavior_data"), dict) else result.get("exit_behavior_data"),
        }

        def _do():
            try:
                self.client.table(OAT_ANALYSIS_TABLE).insert(data).execute()
            except Exception as e:
                print(f"[OAT_SUPABASE] push_analysis error: {e}")

        threading.Thread(target=_do, daemon=True).start()

    def buffer_fill(self, fill: dict):
        """Buffer a fill for batch push."""
        self._fill_buffer.append({
            "slug": fill.get("slug"),
            "tx_hash": fill.get("tx_hash"),
            "timestamp": fill.get("timestamp"),
            "side": fill.get("side"),
            "outcome": fill.get("outcome"),
            "price": fill.get("price"),
            "size": fill.get("size"),
            "usdc_size": fill.get("usdc_size"),
            "fill_type": fill.get("fill_type"),
            "sequence_in_window": fill.get("sequence_in_window"),
        })

    def flush_fills(self):
        """Flush buffered fills to Supabase (background thread)."""
        if not self._fill_buffer or not self.enabled or not self.client:
            self._fill_buffer = []
            return
        buffer_copy = self._fill_buffer[:]
        self._fill_buffer = []
        self._last_fill_flush = datetime.now()

        def _do():
            try:
                self.client.table(OAT_FILLS_TABLE).upsert(
                    buffer_copy, on_conflict="tx_hash"
                ).execute()
                print(f"[OAT_SUPABASE] Flushed {len(buffer_copy)} fills")
            except Exception as e:
                print(f"[OAT_SUPABASE] flush_fills error: {e}")

        threading.Thread(target=_do, daemon=True).start()

    def maybe_flush_fills(self):
        """Flush fills if enough time has passed."""
        elapsed = (datetime.now() - self._last_fill_flush).total_seconds()
        if elapsed >= FILL_FLUSH_INTERVAL:
            self.flush_fills()


# Global instance
_logger: Optional[OatSupabaseLogger] = None


def init() -> bool:
    global _logger
    _logger = OatSupabaseLogger()
    return _logger.init()


def push_observation(obs: dict):
    if _logger and _logger.enabled:
        _logger.push_observation(obs)


def push_analysis(result: dict):
    if _logger and _logger.enabled:
        _logger.push_analysis(result)


def buffer_fill(fill: dict):
    if _logger and _logger.enabled:
        _logger.buffer_fill(fill)


def flush_fills():
    if _logger and _logger.enabled:
        _logger.flush_fills()


def maybe_flush_fills():
    if _logger and _logger.enabled:
        _logger.maybe_flush_fills()


if __name__ == "__main__":
    if init():
        print("OAT Supabase logger connected. Test push...")
        push_observation({
            "slug": "test-slug",
            "window_start": 1709400000,
            "target_traded": False,
            "target_sides": "NONE",
        })
        print("Test complete!")
    else:
        print("Set SUPABASE_KEY env var to run test")
