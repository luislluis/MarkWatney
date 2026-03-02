#!/usr/bin/env python3
"""
Strategy Extractor — Analyzer
==============================
Queries collected observation data and extracts Uncommon-Oat's trading
strategy across 6 dimensions, each with a confidence score.

Can run:
  1. In-process after each window transition (called by oat_observer.py)
  2. On-demand from CLI:  python3 oat_analyzer.py
"""

import math
import time
import json
import statistics
from collections import Counter

import oat_db as db


# ===========================================
# DIMENSION 1: ENTRY TIMING
# ===========================================

def analyze_entry_timing(observations):
    """When in the 15-min window does Oat first buy?"""
    offsets = [o["target_first_buy_offset_secs"] for o in observations
               if o.get("target_first_buy_offset_secs") is not None]

    if len(offsets) < 5:
        return {"confidence": 0.0, "sample_size": len(offsets)}

    median_offset = statistics.median(offsets)
    mean_offset = statistics.mean(offsets)
    stdev = statistics.stdev(offsets) if len(offsets) > 1 else 0
    q1 = sorted(offsets)[len(offsets) // 4]
    q3 = sorted(offsets)[3 * len(offsets) // 4]

    # Remaining seconds (900 - offset)
    remaining_at_entry = [900 - o for o in offsets]
    median_remaining = statistics.median(remaining_at_entry)

    # Correlation with outcome: do early/late entries correlate with wins?
    early_wins = 0
    early_total = 0
    late_wins = 0
    late_total = 0
    for o in observations:
        if o.get("target_first_buy_offset_secs") is None or o.get("outcome") is None:
            continue
        offset = o["target_first_buy_offset_secs"]
        # Determine if Oat's dominant side won
        dominant = o.get("target_sides", "NONE")
        if dominant == "BOTH":
            won = True  # arb always "wins" in theory
        elif dominant == o.get("outcome"):
            won = True
        else:
            won = False

        if offset < median_offset:
            early_total += 1
            if won:
                early_wins += 1
        else:
            late_total += 1
            if won:
                late_wins += 1

    early_win_rate = early_wins / early_total if early_total > 0 else 0
    late_win_rate = late_wins / late_total if late_total > 0 else 0

    # Confidence: sample size + consistency
    n_factor = min(len(offsets) / 100, 1.0)
    consistency = 1.0 - min(stdev / 450, 1.0)  # normalized to half-window
    confidence = n_factor * max(consistency, 0.3)  # floor at 0.3 consistency

    return {
        "confidence": round(confidence, 3),
        "sample_size": len(offsets),
        "median_offset_secs": round(median_offset, 1),
        "mean_offset_secs": round(mean_offset, 1),
        "stdev_secs": round(stdev, 1),
        "q1_offset": round(q1, 1),
        "q3_offset": round(q3, 1),
        "median_remaining_secs": round(median_remaining, 1),
        "early_win_rate": round(early_win_rate, 3),
        "late_win_rate": round(late_win_rate, 3),
        "rule": f"Enter between T-{int(900 - q3)}s and T-{int(900 - q1)}s remaining "
                f"(median: T-{int(median_remaining)}s)",
    }


# ===========================================
# DIMENSION 2: SIDE SELECTION
# ===========================================

def analyze_side_selection(observations):
    """Which side does Oat buy first? What conditions predict the choice?"""
    first_sides = [o for o in observations
                   if o.get("target_first_buy_side") is not None]

    if len(first_sides) < 5:
        return {"confidence": 0.0, "sample_size": len(first_sides)}

    # Distribution
    side_counts = Counter(o["target_first_buy_side"] for o in first_sides)
    total = len(first_sides)
    down_first_pct = side_counts.get("DOWN", 0) / total
    up_first_pct = side_counts.get("UP", 0) / total
    dominant_side = "DOWN" if down_first_pct > up_first_pct else "UP"
    dominant_pct = max(down_first_pct, up_first_pct)

    # Conditional on OB imbalance
    # Bucket: positive imbalance = UP-favored depth, negative = DOWN-favored
    buckets = {"high_up": [], "neutral": [], "high_down": []}
    for o in first_sides:
        imb = o.get("ob_imbalance_at_entry")
        if imb is None:
            continue
        if imb > 0.15:
            buckets["high_up"].append(o["target_first_buy_side"])
        elif imb < -0.15:
            buckets["high_down"].append(o["target_first_buy_side"])
        else:
            buckets["neutral"].append(o["target_first_buy_side"])

    bucket_stats = {}
    for bname, sides in buckets.items():
        if len(sides) >= 3:
            c = Counter(sides)
            bucket_stats[bname] = {
                "sample": len(sides),
                "down_pct": round(c.get("DOWN", 0) / len(sides), 3),
                "up_pct": round(c.get("UP", 0) / len(sides), 3),
            }

    # Confidence: sample size + pattern strength
    n_factor = min(total / 100, 1.0)
    pattern_strength = dominant_pct  # how consistently one side is chosen first
    confidence = n_factor * pattern_strength

    return {
        "confidence": round(confidence, 3),
        "sample_size": total,
        "down_first_pct": round(down_first_pct, 3),
        "up_first_pct": round(up_first_pct, 3),
        "dominant_first_side": dominant_side,
        "dominant_pct": round(dominant_pct, 3),
        "by_ob_imbalance": bucket_stats,
        "rule": f"Buy {dominant_side} first ({dominant_pct:.0%} of the time)",
    }


# ===========================================
# DIMENSION 3: PRICING
# ===========================================

def analyze_pricing(fills, observations):
    """At what price levels does Oat enter? Maker vs taker?"""
    buy_fills = [f for f in fills if f["side"] == "BUY"]

    if len(buy_fills) < 10:
        return {"confidence": 0.0, "sample_size": len(buy_fills)}

    # Price distributions by side
    up_prices = [f["price"] for f in buy_fills if "up" in f["outcome"].lower()]
    down_prices = [f["price"] for f in buy_fills if "down" in f["outcome"].lower()]

    up_avg = statistics.mean(up_prices) if up_prices else 0
    down_avg = statistics.mean(down_prices) if down_prices else 0
    up_median = statistics.median(up_prices) if up_prices else 0
    down_median = statistics.median(down_prices) if down_prices else 0

    # Maker vs taker ratio
    fill_types = Counter(f.get("fill_type", "UNKNOWN") for f in buy_fills)
    total_classified = fill_types.get("MAKER", 0) + fill_types.get("TAKER", 0)
    maker_pct = fill_types.get("MAKER", 0) / total_classified if total_classified > 0 else 0

    # Average USDC per fill
    usdc_sizes = [f["usdc_size"] for f in buy_fills if f["usdc_size"] > 0]
    avg_usdc = statistics.mean(usdc_sizes) if usdc_sizes else 0

    # Confidence: based on fill count
    n_factor = min(len(buy_fills) / 200, 1.0)
    confidence = n_factor

    execution_style = "MAKER" if maker_pct > 0.6 else ("TAKER" if maker_pct < 0.4 else "MIXED")

    return {
        "confidence": round(confidence, 3),
        "sample_size": len(buy_fills),
        "up_fills": len(up_prices),
        "down_fills": len(down_prices),
        "up_avg_price": round(up_avg, 4),
        "up_median_price": round(up_median, 4),
        "down_avg_price": round(down_avg, 4),
        "down_median_price": round(down_median, 4),
        "maker_pct": round(maker_pct, 3),
        "taker_pct": round(1 - maker_pct, 3) if total_classified > 0 else 0,
        "execution_style": execution_style,
        "avg_usdc_per_fill": round(avg_usdc, 2),
        "fill_type_counts": dict(fill_types),
        "rule": f"UP target: {up_median:.0f}c, DN target: {down_median:.0f}c | {execution_style} ({maker_pct:.0%} maker)",
    }


# ===========================================
# DIMENSION 4: SIZING
# ===========================================

def analyze_sizing(observations):
    """How does Oat size positions? Fixed or variable?"""
    traded = [o for o in observations if o.get("target_traded")]

    if len(traded) < 5:
        return {"confidence": 0.0, "sample_size": len(traded)}

    # Total USDC per window
    usdc_per_window = []
    for o in traded:
        total = (o.get("target_up_total_usdc") or 0) + (o.get("target_down_total_usdc") or 0)
        if total > 0:
            usdc_per_window.append(total)

    # Shares per window (total across both sides)
    shares_per_window = []
    for o in traded:
        total = (o.get("target_up_shares") or 0) + (o.get("target_down_shares") or 0)
        if total > 0:
            shares_per_window.append(total)

    if not usdc_per_window:
        return {"confidence": 0.0, "sample_size": 0}

    avg_usdc = statistics.mean(usdc_per_window)
    median_usdc = statistics.median(usdc_per_window)
    stdev_usdc = statistics.stdev(usdc_per_window) if len(usdc_per_window) > 1 else 0
    cv = stdev_usdc / avg_usdc if avg_usdc > 0 else 0  # coefficient of variation

    avg_shares = statistics.mean(shares_per_window) if shares_per_window else 0
    median_shares = statistics.median(shares_per_window) if shares_per_window else 0

    # Per-side sizing
    up_usdc = [o.get("target_up_total_usdc", 0) for o in traded if o.get("target_up_total_usdc", 0) > 0]
    down_usdc = [o.get("target_down_total_usdc", 0) for o in traded if o.get("target_down_total_usdc", 0) > 0]
    avg_up_usdc = statistics.mean(up_usdc) if up_usdc else 0
    avg_down_usdc = statistics.mean(down_usdc) if down_usdc else 0

    # Confidence: sample size * consistency (low CV = consistent sizing)
    n_factor = min(len(usdc_per_window) / 50, 1.0)
    consistency = 1.0 - min(cv, 1.0)
    confidence = n_factor * max(consistency, 0.3)

    sizing_type = "FIXED" if cv < 0.3 else ("VARIABLE" if cv > 0.6 else "SEMI-FIXED")

    return {
        "confidence": round(confidence, 3),
        "sample_size": len(usdc_per_window),
        "avg_usdc_per_window": round(avg_usdc, 2),
        "median_usdc_per_window": round(median_usdc, 2),
        "stdev_usdc": round(stdev_usdc, 2),
        "coefficient_of_variation": round(cv, 3),
        "avg_shares_per_window": round(avg_shares, 1),
        "median_shares_per_window": round(median_shares, 1),
        "avg_up_usdc": round(avg_up_usdc, 2),
        "avg_down_usdc": round(avg_down_usdc, 2),
        "sizing_type": sizing_type,
        "rule": f"~${median_usdc:.0f}/window ({sizing_type}, CV={cv:.2f}) | "
                f"UP ~${avg_up_usdc:.0f} DN ~${avg_down_usdc:.0f}",
    }


# ===========================================
# DIMENSION 5: ARB STRUCTURE
# ===========================================

def analyze_arb_structure(observations):
    """Does Oat always buy both sides? Leg gap? Combined cost?"""
    traded = [o for o in observations if o.get("target_traded")]

    if len(traded) < 5:
        return {"confidence": 0.0, "sample_size": len(traded)}

    # Both-sides rate
    both_sides = [o for o in traded if o.get("target_sides") == "BOTH"]
    up_only = [o for o in traded if o.get("target_sides") == "UP"]
    down_only = [o for o in traded if o.get("target_sides") == "DOWN"]

    both_rate = len(both_sides) / len(traded) if traded else 0

    # Leg gap (seconds between first and second side)
    leg_gaps = [o["target_leg_gap_secs"] for o in both_sides
                if o.get("target_leg_gap_secs") is not None]
    avg_gap = statistics.mean(leg_gaps) if leg_gaps else 0
    median_gap = statistics.median(leg_gaps) if leg_gaps else 0

    # Combined cost
    combined_costs = [o["target_combined_cost"] for o in both_sides
                      if o.get("target_combined_cost") is not None and o["target_combined_cost"] > 0]
    avg_combined = statistics.mean(combined_costs) if combined_costs else 0
    median_combined = statistics.median(combined_costs) if combined_costs else 0

    # Which leg comes first?
    first_leg_counter = Counter(o.get("target_first_buy_side") for o in both_sides
                                 if o.get("target_first_buy_side"))
    first_leg_dominant = first_leg_counter.most_common(1)[0] if first_leg_counter else ("UNKNOWN", 0)

    # Profitability of arb (combined < 1.00 = guaranteed profit)
    profitable_arbs = sum(1 for c in combined_costs if c < 1.00)
    profitable_rate = profitable_arbs / len(combined_costs) if combined_costs else 0

    # Confidence
    n_factor = min(len(both_sides) / 50, 1.0)
    arb_consistency = both_rate
    confidence = n_factor * max(arb_consistency, 0.3)

    return {
        "confidence": round(confidence, 3),
        "sample_size": len(traded),
        "both_sides_count": len(both_sides),
        "both_sides_rate": round(both_rate, 3),
        "up_only_count": len(up_only),
        "down_only_count": len(down_only),
        "avg_leg_gap_secs": round(avg_gap, 1),
        "median_leg_gap_secs": round(median_gap, 1),
        "avg_combined_cost": round(avg_combined, 4),
        "median_combined_cost": round(median_combined, 4),
        "profitable_arb_rate": round(profitable_rate, 3),
        "first_leg_dominant": first_leg_dominant[0] if first_leg_dominant else "UNKNOWN",
        "first_leg_counts": dict(first_leg_counter),
        "rule": f"{'ARB' if both_rate > 0.7 else 'MIXED'}: both sides {both_rate:.0%} | "
                f"combined ~{median_combined:.2f} | gap ~{median_gap:.0f}s | "
                f"first leg: {first_leg_dominant[0] if first_leg_dominant else 'N/A'}",
    }


# ===========================================
# DIMENSION 6: EXIT BEHAVIOR
# ===========================================

def analyze_exit_behavior(observations, fills):
    """Does Oat sell before settlement?"""
    traded = [o for o in observations if o.get("target_traded")]

    if len(traded) < 5:
        return {"confidence": 0.0, "sample_size": len(traded)}

    # Windows with sells
    with_sells = [o for o in traded if o.get("target_total_sells", 0) > 0]
    sell_rate = len(with_sells) / len(traded) if traded else 0

    # Sell fill details
    sell_fills = [f for f in fills if f["side"] == "SELL"]
    sell_prices = [f["price"] for f in sell_fills]
    avg_sell_price = statistics.mean(sell_prices) if sell_prices else 0

    # Sell timing (how far into the window)
    sell_offsets = []
    for f in sell_fills:
        slug = f.get("slug", "")
        # Extract window_start from slug
        parts = slug.split("-")
        try:
            w_start = int(parts[-1])
            offset = f["timestamp"] - w_start
            if 0 <= offset <= 900:
                sell_offsets.append(offset)
        except (ValueError, IndexError):
            pass

    avg_sell_offset = statistics.mean(sell_offsets) if sell_offsets else 0

    # Confidence: based on windows observed (sells are rare, so lower threshold)
    n_factor = min(len(traded) / 50, 1.0)
    # If sell_rate is very low, confidence is still decent — "they don't sell" is a finding
    confidence = n_factor * 0.7 if len(with_sells) < 3 else n_factor

    strategy = "HOLD_TO_SETTLEMENT" if sell_rate < 0.1 else "ACTIVE_EXIT"

    return {
        "confidence": round(confidence, 3),
        "sample_size": len(traded),
        "windows_with_sells": len(with_sells),
        "sell_rate": round(sell_rate, 3),
        "total_sell_fills": len(sell_fills),
        "avg_sell_price": round(avg_sell_price, 4),
        "avg_sell_offset_secs": round(avg_sell_offset, 1),
        "strategy": strategy,
        "rule": f"{strategy} (sell rate: {sell_rate:.0%})" +
                (f" | avg sell @ {avg_sell_price:.0f}c, T+{avg_sell_offset:.0f}s" if sell_fills else ""),
    }


# ===========================================
# ORCHESTRATOR
# ===========================================

def run_analysis():
    """Run all 6 dimension analyses and store results."""
    observations = db.get_traded_observations(limit=500)
    all_obs = db.get_resolved_observations(limit=500)
    fills = db.get_all_fills(limit=5000)

    if len(observations) < 3:
        return None  # not enough data

    # Run all 6 dimensions
    entry_timing = analyze_entry_timing(observations)
    side_selection = analyze_side_selection(observations)
    pricing = analyze_pricing(fills, observations)
    sizing = analyze_sizing(observations)
    arb_structure = analyze_arb_structure(observations)
    exit_behavior = analyze_exit_behavior(observations, fills)

    # Overall readiness = minimum confidence across all dimensions
    confidences = [
        entry_timing["confidence"],
        side_selection["confidence"],
        pricing["confidence"],
        sizing["confidence"],
        arb_structure["confidence"],
        exit_behavior["confidence"],
    ]
    overall_readiness = min(confidences)

    # Determine sample range
    slugs = [o["slug"] for o in observations]
    sample_start = slugs[-1] if slugs else None
    sample_end = slugs[0] if slugs else None

    # Store to DB
    db.insert_analysis(
        run_timestamp=int(time.time()),
        sample_start=sample_start,
        sample_end=sample_end,
        sample_size=len(observations),
        entry_timing_confidence=entry_timing["confidence"],
        side_selection_confidence=side_selection["confidence"],
        pricing_confidence=pricing["confidence"],
        sizing_confidence=sizing["confidence"],
        arb_structure_confidence=arb_structure["confidence"],
        exit_behavior_confidence=exit_behavior["confidence"],
        overall_readiness=overall_readiness,
        entry_timing_data=entry_timing,
        side_selection_data=side_selection,
        pricing_data=pricing,
        sizing_data=sizing,
        arb_structure_data=arb_structure,
        exit_behavior_data=exit_behavior,
    )

    return {
        "overall_readiness": overall_readiness,
        "sample_size": len(observations),
        "entry_timing": entry_timing,
        "side_selection": side_selection,
        "pricing": pricing,
        "sizing": sizing,
        "arb_structure": arb_structure,
        "exit_behavior": exit_behavior,
    }


# ===========================================
# CLI MODE
# ===========================================

def print_report(result):
    """Print a human-readable strategy report."""
    if not result:
        print("Not enough data for analysis yet.")
        return

    print("=" * 60)
    print(f"  OAT STRATEGY ANALYSIS — {result['sample_size']} observations")
    print(f"  Overall Readiness: {result['overall_readiness']:.0%}")
    print("=" * 60)

    dims = [
        ("Entry Timing", result["entry_timing"]),
        ("Side Selection", result["side_selection"]),
        ("Pricing", result["pricing"]),
        ("Sizing", result["sizing"]),
        ("Arb Structure", result["arb_structure"]),
        ("Exit Behavior", result["exit_behavior"]),
    ]

    for name, data in dims:
        conf = data.get("confidence", 0)
        bar = "█" * int(conf * 20) + "░" * (20 - int(conf * 20))
        print(f"\n  {name}")
        print(f"  [{bar}] {conf:.0%}  (n={data.get('sample_size', 0)})")
        rule = data.get("rule", "Insufficient data")
        print(f"  → {rule}")

        # Print key metrics
        for k, v in data.items():
            if k in ("confidence", "sample_size", "rule"):
                continue
            if isinstance(v, dict):
                for kk, vv in v.items():
                    print(f"    {kk}: {vv}")
            else:
                print(f"    {k}: {v}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    db.init_db()
    result = run_analysis()
    print_report(result)
