#!/usr/bin/env python3
"""
Hourly Telegram summary of Polybot trading performance.

Fetches from Polymarket Activity API (same source as dashboard),
computes daily stats, and sends to Telegram.

Cron: 0 * * * * cd ~/polymarket_bot && python3 telegram_summary.py >> /tmp/telegram_summary.log 2>&1
"""

import json
import os
import time
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

EST = ZoneInfo("America/New_York")
WALLET_ADDRESS = "0x636796704404959f5Ae9BEfEb2B3880eadf6960a"
TELEGRAM_CONFIG_FILE = os.path.expanduser("~/.telegram-bot.json")


def load_telegram_config():
    with open(TELEGRAM_CONFIG_FILE) as f:
        return json.load(f)


def send_telegram(config, message):
    url = f"https://api.telegram.org/bot{config['token']}/sendMessage"
    requests.post(url, data={
        "chat_id": config["chat_id"],
        "text": message,
        "parse_mode": "HTML"
    }, timeout=10)


def fetch_activity():
    url = f"https://data-api.polymarket.com/activity?user={WALLET_ADDRESS}&limit=1000"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    return resp.json()


def process_trades(activity):
    """
    Process Polymarket activity into trade records.
    Mirrors the FIFO matching logic in dashboard.html lines 696-834.
    """
    poly_trades = [a for a in activity if a.get("type") == "TRADE"]
    redeem_events = [a for a in activity if a.get("type") == "REDEEM"]

    redeemed = set()
    for r in redeem_events:
        if r.get("slug"):
            redeemed.add(r["slug"])

    # Group by slug|outcome
    grouped = {}
    for t in poly_trades:
        slug = t.get("slug", "")
        outcome = (t.get("outcome") or "").upper()
        key = f"{slug}|{outcome}"
        if key not in grouped:
            grouped[key] = {"buys": [], "sells": []}
        entry = {
            "timestamp": t.get("timestamp", 0),
            "size": float(t.get("size", 0) or 0),
            "price": float(t.get("price", 0) or 0),
        }
        side = (t.get("side") or "").upper()
        if side == "SELL":
            grouped[key]["sells"].append(entry)
        else:
            grouped[key]["buys"].append(entry)

    trades = []
    now_ts = time.time()

    for key, group in grouped.items():
        slug, side = key.split("|", 1)
        if not group["buys"]:
            continue
        won = slug in redeemed

        group["buys"].sort(key=lambda x: x["timestamp"])
        group["sells"].sort(key=lambda x: x["timestamp"])

        # FIFO match sells to buys
        sell_pool = [{"remaining": s["size"], **s} for s in group["sells"]]
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

        for i, buy in enumerate(group["buys"]):
            info = buy_exit_info[i]
            cost = buy["size"] * buy["price"]
            exited_all = info["exit_shares"] >= buy["size"] - 0.02
            has_exit = info["exit_shares"] > 0.001
            ts_iso = datetime.fromtimestamp(buy["timestamp"], tz=EST).strftime("%Y-%m-%d")

            if exited_all:
                pnl = info["exit_revenue"] - cost
                trades.append({
                    "date": ts_iso,
                    "window_id": slug,
                    "status": "EXIT",
                    "profit_loss": round(pnl, 2),
                    "cost_basis": round(cost, 2),
                })
            elif has_exit:
                remain_shares = buy["size"] - info["exit_shares"]
                exit_cost = info["exit_shares"] * buy["price"]
                exit_pnl = info["exit_revenue"] - exit_cost
                trades.append({
                    "date": ts_iso,
                    "window_id": slug,
                    "status": "EXIT",
                    "profit_loss": round(exit_pnl, 2),
                    "cost_basis": round(exit_cost, 2),
                })
                remain_cost = remain_shares * buy["price"]
                remain_age = now_ts - buy["timestamp"]
                remain_status = "WIN" if won else ("LOSS" if remain_age > 1800 else "PENDING")
                if remain_status == "WIN":
                    remain_pnl = remain_shares * (1 - buy["price"])
                elif remain_status == "LOSS":
                    remain_pnl = -remain_cost
                else:
                    remain_pnl = 0
                trades.append({
                    "date": ts_iso,
                    "window_id": slug,
                    "status": remain_status,
                    "profit_loss": round(remain_pnl, 2),
                    "cost_basis": round(remain_cost, 2),
                })
            else:
                age = now_ts - buy["timestamp"]
                status = "WIN" if won else ("LOSS" if age > 1800 else "PENDING")
                if status == "WIN":
                    pnl = buy["size"] * (1 - buy["price"])
                elif status == "LOSS":
                    pnl = -cost
                else:
                    pnl = 0
                trades.append({
                    "date": ts_iso,
                    "window_id": slug,
                    "status": status,
                    "profit_loss": round(pnl, 2),
                    "cost_basis": round(cost, 2),
                })

    return trades


def get_today_summary(trades):
    """Compute daily summary matching dashboard.html lines 847-875."""
    today = datetime.now(EST).strftime("%Y-%m-%d")
    day_trades = [t for t in trades if t["date"] == today]
    if not day_trades:
        return None

    # Classify each window: EXIT > LOSS > WIN > PENDING
    window_status = {}
    for t in day_trades:
        wid = t["window_id"]
        cur = window_status.get(wid)
        if t["status"] == "EXIT" and cur != "EXIT":
            window_status[wid] = "EXIT"
        elif t["status"] == "LOSS" and cur != "EXIT":
            window_status[wid] = "LOSS"
        elif t["status"] == "WIN" and (not cur or cur == "PENDING"):
            window_status[wid] = "WIN"
        elif not cur:
            window_status[wid] = "PENDING"

    statuses = list(window_status.values())
    wins = statuses.count("WIN")
    losses = statuses.count("LOSS")
    exits = statuses.count("EXIT")
    pending = statuses.count("PENDING")
    total_pnl = sum(t["profit_loss"] for t in day_trades)
    total_cost = sum(t["cost_basis"] for t in day_trades)
    num_windows = len(statuses)
    avg_trade_value = total_cost / num_windows if num_windows > 0 else 0
    resolved = wins + losses + exits
    win_rate = round(wins / resolved * 100, 1) if resolved > 0 else 0
    roi = round(total_pnl / avg_trade_value * 100, 1) if avg_trade_value > 0 else 0

    return {
        "wins": wins,
        "losses": losses,
        "exits": exits,
        "pending": pending,
        "total_pnl": total_pnl,
        "win_rate": win_rate,
        "roi": roi,
    }


def format_message(summary):
    pnl = summary["total_pnl"]
    pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
    pnl_emoji = "\U0001f7e2" if pnl >= 0 else "\U0001f534"

    record = f"{summary['wins']}W / {summary['losses']}L"
    if summary["exits"] > 0:
        record += f" / {summary['exits']}E"
    if summary["pending"] > 0:
        record += f" ({summary['pending']} pending)"

    roi = summary["roi"]
    roi_str = f"+{roi}%" if roi >= 0 else f"{roi}%"

    now = datetime.now(EST)
    time_str = now.strftime("%-I:%M %p EST")

    return (
        f"\u26a1 <b>POLYBOT HOURLY</b> \u2014 {time_str}\n"
        f"\n"
        f"{pnl_emoji} Today's P&L: <b>{pnl_str}</b>\n"
        f"\U0001f4ca Win Rate: <b>{summary['win_rate']}%</b>\n"
        f"\U0001f4cb {record}\n"
        f"\U0001f4b0 ROI: <b>{roi_str}</b>"
    )


def main():
    config = load_telegram_config()
    activity = fetch_activity()
    trades = process_trades(activity)
    summary = get_today_summary(trades)

    if summary is None:
        now = datetime.now(EST)
        time_str = now.strftime("%-I:%M %p EST")
        msg = f"\u26a1 <b>POLYBOT HOURLY</b> \u2014 {time_str}\n\nNo trades today."
    else:
        msg = format_message(summary)

    send_telegram(config, msg)
    print(f"[{datetime.now(EST).isoformat()}] Sent summary")


if __name__ == "__main__":
    main()
