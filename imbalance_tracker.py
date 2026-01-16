#!/usr/bin/env python3
"""
Order Book Imbalance Tracker
============================
Runs for several hours, tracking:
- Order book imbalance readings
- Price movements
- Whether imbalance predicted direction
- Window outcomes (UP won or DOWN won)

Run: python3 imbalance_tracker.py
Results saved to: imbalance_data.json
"""

import time
import json
import requests
from datetime import datetime
from collections import defaultdict
from orderbook_analyzer import OrderBookAnalyzer

# Output file
DATA_FILE = "imbalance_data.json"
SUMMARY_FILE = "imbalance_summary.txt"

# Tracking data
windows = {}  # window_id -> window data
current_window = None
analyzer = OrderBookAnalyzer(history_size=60, imbalance_threshold=0.3)

def get_current_slug():
    current = int(time.time())
    window_start = (current // 900) * 900
    return f"btc-updown-15m-{window_start}", window_start

def fetch_market_data(slug):
    """Fetch market data for a slug."""
    try:
        url = f"https://gamma-api.polymarket.com/events?slug={slug}"
        resp = requests.get(url, timeout=5)
        events = resp.json()
        if events:
            return events[0]
    except:
        pass
    return None

def fetch_order_books(market):
    """Fetch order books for both tokens."""
    try:
        clob_ids = market.get('markets', [{}])[0].get('clobTokenIds', '')
        clob_ids = clob_ids.replace('[', '').replace(']', '').replace('"', '')
        tokens = [t.strip() for t in clob_ids.split(',')]

        if len(tokens) >= 2:
            up_resp = requests.get(f"https://clob.polymarket.com/book?token_id={tokens[0]}", timeout=3)
            down_resp = requests.get(f"https://clob.polymarket.com/book?token_id={tokens[1]}", timeout=3)

            up_book = up_resp.json()
            down_book = down_resp.json()

            return {
                'up_bids': up_book.get('bids', []),
                'up_asks': up_book.get('asks', []),
                'down_bids': down_book.get('bids', []),
                'down_asks': down_book.get('asks', [])
            }
    except:
        pass
    return None

def get_prices(books):
    """Extract best ask prices."""
    ask_up = float(books['up_asks'][0]['price']) if books.get('up_asks') else 0.5
    ask_down = float(books['down_asks'][0]['price']) if books.get('down_asks') else 0.5
    return ask_up, ask_down

def save_data():
    """Save collected data to file."""
    with open(DATA_FILE, 'w') as f:
        json.dump(windows, f, indent=2)

def generate_summary():
    """Generate correlation summary."""
    summary = []
    summary.append("=" * 60)
    summary.append("ORDER BOOK IMBALANCE CORRELATION ANALYSIS")
    summary.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    summary.append("=" * 60)
    summary.append("")

    # Analyze completed windows
    completed = [w for w in windows.values() if w.get('outcome')]

    if not completed:
        summary.append("No completed windows yet. Keep running...")
        return "\n".join(summary)

    summary.append(f"Completed windows analyzed: {len(completed)}")
    summary.append("")

    # Track prediction accuracy
    predictions = {'correct': 0, 'wrong': 0, 'no_signal': 0}
    strong_predictions = {'correct': 0, 'wrong': 0}
    trend_predictions = {'correct': 0, 'wrong': 0, 'no_trend': 0}

    # Correlation by strength
    by_strength = defaultdict(lambda: {'correct': 0, 'wrong': 0})

    for w in completed:
        outcome = w.get('outcome')  # "UP" or "DOWN"
        readings = w.get('readings', [])

        if not readings:
            continue

        # Analyze readings from when prices were 30-70c (tradeable range)
        tradeable_readings = [r for r in readings if 0.30 <= r['ask_up'] <= 0.70 or 0.30 <= r['ask_down'] <= 0.70]

        if not tradeable_readings:
            continue

        # Get dominant signal during tradeable period
        signals = [r['signal'] for r in tradeable_readings if r.get('signal')]
        if signals:
            # Most common signal
            from collections import Counter
            signal_counts = Counter(signals)
            dominant_signal = signal_counts.most_common(1)[0][0]
            predicted = "UP" if dominant_signal == "BUY_UP" else "DOWN"

            if predicted == outcome:
                predictions['correct'] += 1
            else:
                predictions['wrong'] += 1

            # Check strength
            strengths = [r['strength'] for r in tradeable_readings if r.get('strength')]
            if strengths:
                strength_counts = Counter(strengths)
                dominant_strength = strength_counts.most_common(1)[0][0]

                if predicted == outcome:
                    by_strength[dominant_strength]['correct'] += 1
                else:
                    by_strength[dominant_strength]['wrong'] += 1

                if dominant_strength == "STRONG":
                    if predicted == outcome:
                        strong_predictions['correct'] += 1
                    else:
                        strong_predictions['wrong'] += 1
        else:
            predictions['no_signal'] += 1

        # Check trend predictions
        trends = [r['trend'] for r in tradeable_readings if r.get('trend')]
        if trends:
            trend_counts = Counter(trends)
            dominant_trend = trend_counts.most_common(1)[0][0]
            trend_predicted = "UP" if dominant_trend == "TREND_UP" else "DOWN"

            if trend_predicted == outcome:
                trend_predictions['correct'] += 1
            else:
                trend_predictions['wrong'] += 1
        else:
            trend_predictions['no_trend'] += 1

    # Report results
    summary.append("SIGNAL PREDICTION ACCURACY:")
    summary.append("-" * 40)
    total = predictions['correct'] + predictions['wrong']
    if total > 0:
        accuracy = predictions['correct'] / total * 100
        summary.append(f"  Correct: {predictions['correct']}/{total} ({accuracy:.1f}%)")
        summary.append(f"  Wrong: {predictions['wrong']}/{total}")
        summary.append(f"  No signal: {predictions['no_signal']}")
    else:
        summary.append("  Not enough data yet")
    summary.append("")

    summary.append("BY SIGNAL STRENGTH:")
    summary.append("-" * 40)
    for strength in ["STRONG", "MODERATE", "WEAK"]:
        data = by_strength[strength]
        total = data['correct'] + data['wrong']
        if total > 0:
            acc = data['correct'] / total * 100
            summary.append(f"  {strength}: {data['correct']}/{total} correct ({acc:.1f}%)")
    summary.append("")

    summary.append("STRONG SIGNALS ONLY:")
    summary.append("-" * 40)
    strong_total = strong_predictions['correct'] + strong_predictions['wrong']
    if strong_total > 0:
        strong_acc = strong_predictions['correct'] / strong_total * 100
        summary.append(f"  Accuracy: {strong_predictions['correct']}/{strong_total} ({strong_acc:.1f}%)")
    else:
        summary.append("  No strong signals recorded yet")
    summary.append("")

    summary.append("TREND CONFIRMATION:")
    summary.append("-" * 40)
    trend_total = trend_predictions['correct'] + trend_predictions['wrong']
    if trend_total > 0:
        trend_acc = trend_predictions['correct'] / trend_total * 100
        summary.append(f"  Correct: {trend_predictions['correct']}/{trend_total} ({trend_acc:.1f}%)")
        summary.append(f"  No trend: {trend_predictions['no_trend']}")
    else:
        summary.append("  Not enough trend data yet")
    summary.append("")

    # Strategy recommendations
    summary.append("=" * 60)
    summary.append("STRATEGY RECOMMENDATIONS:")
    summary.append("=" * 60)

    if total >= 5:
        if accuracy >= 60:
            summary.append("✅ Order book imbalance shows POSITIVE correlation")
            summary.append("   - Consider using imbalance signals for trade entry")
        elif accuracy >= 50:
            summary.append("⚠️  Order book imbalance shows WEAK correlation")
            summary.append("   - Use only as secondary confirmation, not primary signal")
        else:
            summary.append("❌ Order book imbalance shows NEGATIVE correlation")
            summary.append("   - May indicate smart money fading retail")
            summary.append("   - Consider CONTRARIAN approach (fade the signal)")

        if strong_total >= 3:
            if strong_acc >= 65:
                summary.append("")
                summary.append("✅ STRONG signals are reliable - prioritize these")
            elif strong_acc < 50:
                summary.append("")
                summary.append("⚠️  STRONG signals may be traps - use caution")
    else:
        summary.append("Need more data (5+ windows) for reliable recommendations")

    summary.append("")
    summary.append("=" * 60)

    return "\n".join(summary)

def main():
    global current_window, windows

    print("=" * 60)
    print("ORDER BOOK IMBALANCE TRACKER")
    print("=" * 60)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Data file: {DATA_FILE}")
    print(f"Summary file: {SUMMARY_FILE}")
    print("Press Ctrl+C to stop and generate report")
    print("=" * 60)
    print()

    last_slug = None
    reading_count = 0

    try:
        while True:
            slug, window_start = get_current_slug()
            now = int(time.time())
            ttc = (window_start + 900) - now  # Time to close

            # New window?
            if slug != last_slug:
                # Mark previous window outcome if we can
                if last_slug and last_slug in windows:
                    prev_market = fetch_market_data(last_slug)
                    if prev_market:
                        books = fetch_order_books(prev_market)
                        if books:
                            ask_up, ask_down = get_prices(books)
                            # At 99c/1c, we know the winner
                            if ask_up >= 0.95:
                                windows[last_slug]['outcome'] = "UP"
                                print(f"[RESULT] {last_slug}: UP won")
                            elif ask_down >= 0.95:
                                windows[last_slug]['outcome'] = "DOWN"
                                print(f"[RESULT] {last_slug}: DOWN won")

                print(f"\n[NEW WINDOW] {slug}")
                windows[slug] = {
                    'start_time': datetime.now().isoformat(),
                    'readings': [],
                    'outcome': None
                }
                current_window = slug
                last_slug = slug
                analyzer.history.clear()  # Reset for new window
                save_data()

            # Fetch current data
            market = fetch_market_data(slug)
            if not market:
                time.sleep(2)
                continue

            books = fetch_order_books(market)
            if not books:
                time.sleep(2)
                continue

            ask_up, ask_down = get_prices(books)

            # Analyze imbalance
            result = analyzer.analyze(
                books['up_bids'], books['up_asks'],
                books['down_bids'], books['down_asks']
            )

            # Store reading
            reading = {
                'time': datetime.now().isoformat(),
                'ttc': ttc,
                'ask_up': ask_up,
                'ask_down': ask_down,
                'up_imbalance': result['up_imbalance'],
                'down_imbalance': result['down_imbalance'],
                'signal': result['signal'],
                'strength': result['strength'],
                'trend': result['trend']
            }
            windows[slug]['readings'].append(reading)
            reading_count += 1

            # Display
            sig = result['signal'] or '-'
            str_val = result['strength'][0] if result['strength'] else '-'
            trend = result['trend'] or '-'

            print(f"[{datetime.now().strftime('%H:%M:%S')}] T-{ttc:3.0f}s | UP:{ask_up*100:2.0f}c DN:{ask_down*100:2.0f}c | Imb:{result['up_imbalance']:+.2f}/{result['down_imbalance']:+.2f} | {sig:8} ({str_val}) | {trend}")

            # Save every 30 readings
            if reading_count % 30 == 0:
                save_data()
                # Update summary
                summary = generate_summary()
                with open(SUMMARY_FILE, 'w') as f:
                    f.write(summary)

            time.sleep(5)  # Sample every 5 seconds

    except KeyboardInterrupt:
        print("\n\nStopping tracker...")

    # Final save
    save_data()

    # Generate final summary
    print("\n")
    summary = generate_summary()
    print(summary)

    with open(SUMMARY_FILE, 'w') as f:
        f.write(summary)

    print(f"\nData saved to: {DATA_FILE}")
    print(f"Summary saved to: {SUMMARY_FILE}")

if __name__ == "__main__":
    main()
