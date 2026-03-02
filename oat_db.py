#!/usr/bin/env python3
"""
SQLite storage for the Strategy Extractor Bot.
Stores observations, fills, order book snapshots, and analysis results
for reverse-engineering Uncommon-Oat's trading strategy.
"""

import sqlite3
import os
import time
import json
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

EST = ZoneInfo("America/New_York")

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "oat.db")

_local = threading.local()


def get_conn():
    conn = getattr(_local, 'conn', None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        _local.conn = conn
    return conn


def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY,
            slug TEXT NOT NULL,
            window_start INTEGER NOT NULL,
            target_wallet TEXT NOT NULL DEFAULT '0xd0d6053c3c37e727402d84c14069780d360993aa',
            -- Target behavior
            target_traded BOOLEAN DEFAULT 0,
            target_sides TEXT,
            target_total_buys INTEGER DEFAULT 0,
            target_total_sells INTEGER DEFAULT 0,
            target_up_shares REAL DEFAULT 0,
            target_down_shares REAL DEFAULT 0,
            target_up_avg_price REAL DEFAULT 0,
            target_down_avg_price REAL DEFAULT 0,
            target_up_total_usdc REAL DEFAULT 0,
            target_down_total_usdc REAL DEFAULT 0,
            target_first_buy_offset_secs INTEGER,
            target_first_buy_side TEXT,
            target_leg_gap_secs INTEGER,
            target_combined_cost REAL,
            target_maker_count INTEGER DEFAULT 0,
            target_taker_count INTEGER DEFAULT 0,
            -- Market context at first buy
            up_ask_at_entry REAL,
            down_ask_at_entry REAL,
            ob_imbalance_at_entry REAL,
            time_remaining_at_entry INTEGER,
            -- Outcome
            outcome TEXT,
            resolved_at INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(slug)
        );

        CREATE TABLE IF NOT EXISTS target_fills (
            id INTEGER PRIMARY KEY,
            slug TEXT NOT NULL,
            target_wallet TEXT NOT NULL DEFAULT '0xd0d6053c3c37e727402d84c14069780d360993aa',
            tx_hash TEXT UNIQUE NOT NULL,
            timestamp INTEGER NOT NULL,
            side TEXT NOT NULL,
            outcome TEXT NOT NULL,
            price REAL NOT NULL,
            size REAL NOT NULL,
            usdc_size REAL NOT NULL,
            fill_type TEXT,
            ob_snapshot_id INTEGER,
            sequence_in_window INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS ob_snapshots (
            id INTEGER PRIMARY KEY,
            slug TEXT NOT NULL,
            timestamp REAL NOT NULL,
            up_best_bid REAL,
            up_best_ask REAL,
            up_bid_depth REAL,
            up_ask_depth REAL,
            down_best_bid REAL,
            down_best_ask REAL,
            down_bid_depth REAL,
            down_ask_depth REAL
        );

        CREATE TABLE IF NOT EXISTS analysis_results (
            id INTEGER PRIMARY KEY,
            run_timestamp INTEGER NOT NULL,
            sample_start TEXT,
            sample_end TEXT,
            sample_size INTEGER,
            entry_timing_confidence REAL DEFAULT 0,
            side_selection_confidence REAL DEFAULT 0,
            pricing_confidence REAL DEFAULT 0,
            sizing_confidence REAL DEFAULT 0,
            arb_structure_confidence REAL DEFAULT 0,
            exit_behavior_confidence REAL DEFAULT 0,
            overall_readiness REAL DEFAULT 0,
            entry_timing_data JSON,
            side_selection_data JSON,
            pricing_data JSON,
            sizing_data JSON,
            arb_structure_data JSON,
            exit_behavior_data JSON,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_obs_slug ON observations(slug);
        CREATE INDEX IF NOT EXISTS idx_obs_window ON observations(window_start);
        CREATE INDEX IF NOT EXISTS idx_fills_slug ON target_fills(slug, timestamp);
        CREATE INDEX IF NOT EXISTS idx_fills_tx ON target_fills(tx_hash);
        CREATE INDEX IF NOT EXISTS idx_ob_slug ON ob_snapshots(slug, timestamp);
        CREATE INDEX IF NOT EXISTS idx_analysis_ts ON analysis_results(run_timestamp);
    """)
    conn.commit()


# ===========================================
# OBSERVATION HELPERS
# ===========================================

def upsert_observation(slug, window_start, **kwargs):
    conn = get_conn()
    existing = conn.execute("SELECT id FROM observations WHERE slug = ?", (slug,)).fetchone()
    if existing:
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [slug]
        conn.execute(f"UPDATE observations SET {sets} WHERE slug = ?", vals)
    else:
        cols = ["slug", "window_start"] + list(kwargs.keys())
        placeholders = ", ".join(["?"] * len(cols))
        vals = [slug, window_start] + list(kwargs.values())
        conn.execute(f"INSERT INTO observations ({', '.join(cols)}) VALUES ({placeholders})", vals)
    conn.commit()


def get_observation(slug):
    conn = get_conn()
    row = conn.execute("SELECT * FROM observations WHERE slug = ?", (slug,)).fetchone()
    return dict(row) if row else None


def get_recent_observations(limit=100):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM observations ORDER BY window_start DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_resolved_observations(limit=500):
    """Get observations where outcome is known — the dataset for analysis."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM observations WHERE outcome IS NOT NULL ORDER BY window_start DESC LIMIT ?",
        (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_traded_observations(limit=500):
    """Get observations where target actually traded — for strategy analysis."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM observations WHERE target_traded = 1 AND outcome IS NOT NULL "
        "ORDER BY window_start DESC LIMIT ?",
        (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


# ===========================================
# TARGET FILL HELPERS
# ===========================================

def insert_fill(slug, tx_hash, timestamp, side, outcome, price, size, usdc_size,
                fill_type=None, ob_snapshot_id=None, sequence_in_window=None):
    conn = get_conn()
    try:
        conn.execute("""
            INSERT INTO target_fills (slug, tx_hash, timestamp, side, outcome, price, size,
                                      usdc_size, fill_type, ob_snapshot_id, sequence_in_window)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (slug, tx_hash, timestamp, side, outcome, price, size, usdc_size,
              fill_type, ob_snapshot_id, sequence_in_window))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False  # duplicate tx_hash


def get_fills_for_slug(slug):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM target_fills WHERE slug = ? ORDER BY timestamp", (slug,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_all_fills(limit=5000):
    """Get all fills for analysis — pricing and execution patterns."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM target_fills ORDER BY timestamp DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_buy_fills(limit=5000):
    """Get only BUY fills — core data for strategy extraction."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM target_fills WHERE side = 'BUY' ORDER BY timestamp DESC LIMIT ?",
        (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_sell_fills(limit=2000):
    """Get SELL fills — for exit behavior analysis."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM target_fills WHERE side = 'SELL' ORDER BY timestamp DESC LIMIT ?",
        (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


# ===========================================
# ORDER BOOK SNAPSHOT HELPERS
# ===========================================

def insert_ob_snapshot(slug, timestamp, up_best_bid, up_best_ask, up_bid_depth,
                       up_ask_depth, down_best_bid, down_best_ask, down_bid_depth,
                       down_ask_depth):
    conn = get_conn()
    conn.execute("""
        INSERT INTO ob_snapshots (slug, timestamp, up_best_bid, up_best_ask,
            up_bid_depth, up_ask_depth, down_best_bid, down_best_ask,
            down_bid_depth, down_ask_depth)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (slug, timestamp, up_best_bid, up_best_ask, up_bid_depth,
          up_ask_depth, down_best_bid, down_best_ask, down_bid_depth,
          down_ask_depth))
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def get_nearest_ob_snapshot(slug, timestamp):
    conn = get_conn()
    row = conn.execute("""
        SELECT * FROM ob_snapshots WHERE slug = ?
        ORDER BY ABS(timestamp - ?) LIMIT 1
    """, (slug, timestamp)).fetchone()
    return dict(row) if row else None


# ===========================================
# ANALYSIS RESULTS HELPERS
# ===========================================

def insert_analysis(run_timestamp, sample_start, sample_end, sample_size,
                    entry_timing_confidence, side_selection_confidence,
                    pricing_confidence, sizing_confidence,
                    arb_structure_confidence, exit_behavior_confidence,
                    overall_readiness,
                    entry_timing_data, side_selection_data,
                    pricing_data, sizing_data,
                    arb_structure_data, exit_behavior_data):
    conn = get_conn()
    conn.execute("""
        INSERT INTO analysis_results (
            run_timestamp, sample_start, sample_end, sample_size,
            entry_timing_confidence, side_selection_confidence,
            pricing_confidence, sizing_confidence,
            arb_structure_confidence, exit_behavior_confidence,
            overall_readiness,
            entry_timing_data, side_selection_data,
            pricing_data, sizing_data,
            arb_structure_data, exit_behavior_data
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (run_timestamp, sample_start, sample_end, sample_size,
          entry_timing_confidence, side_selection_confidence,
          pricing_confidence, sizing_confidence,
          arb_structure_confidence, exit_behavior_confidence,
          overall_readiness,
          json.dumps(entry_timing_data), json.dumps(side_selection_data),
          json.dumps(pricing_data), json.dumps(sizing_data),
          json.dumps(arb_structure_data), json.dumps(exit_behavior_data)))
    conn.commit()


def get_latest_analysis():
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM analysis_results ORDER BY run_timestamp DESC LIMIT 1"
    ).fetchone()
    if row:
        d = dict(row)
        for key in ('entry_timing_data', 'side_selection_data', 'pricing_data',
                     'sizing_data', 'arb_structure_data', 'exit_behavior_data'):
            if d.get(key) and isinstance(d[key], str):
                d[key] = json.loads(d[key])
        return d
    return None


def get_analysis_history(limit=50):
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, run_timestamp, sample_size, overall_readiness, "
        "entry_timing_confidence, side_selection_confidence, pricing_confidence, "
        "sizing_confidence, arb_structure_confidence, exit_behavior_confidence "
        "FROM analysis_results ORDER BY run_timestamp DESC LIMIT ?",
        (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


# ===========================================
# SUMMARY STATS
# ===========================================

def compute_basic_stats():
    """Quick stats for status line display."""
    conn = get_conn()
    total_obs = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    resolved_obs = conn.execute(
        "SELECT COUNT(*) FROM observations WHERE outcome IS NOT NULL"
    ).fetchone()[0]
    traded_obs = conn.execute(
        "SELECT COUNT(*) FROM observations WHERE target_traded = 1"
    ).fetchone()[0]
    total_fills = conn.execute("SELECT COUNT(*) FROM target_fills").fetchone()[0]
    total_snapshots = conn.execute("SELECT COUNT(*) FROM ob_snapshots").fetchone()[0]

    latest = get_latest_analysis()
    readiness = latest["overall_readiness"] if latest else 0.0

    return {
        'total_observations': total_obs,
        'resolved_observations': resolved_obs,
        'traded_observations': traded_obs,
        'total_fills': total_fills,
        'total_snapshots': total_snapshots,
        'overall_readiness': readiness,
    }


# ===========================================
# CLEANUP
# ===========================================

def cleanup_old_ob_snapshots(days=7):
    """Delete order book snapshots older than N days."""
    cutoff = time.time() - (days * 86400)
    conn = get_conn()
    deleted = conn.execute("DELETE FROM ob_snapshots WHERE timestamp < ?", (cutoff,)).rowcount
    conn.commit()
    return deleted
