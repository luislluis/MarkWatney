# Brainstorm: Use Polymarket API for Real Fill Prices

**Date:** 2026-02-05
**Status:** Approved

## What We're Building

Replace the bot's self-reported trade data (always shows 99c) with actual fill prices from Polymarket's `/trades` API (shows real execution prices: 97c, 98c, 99c, etc.).

Two-part fix:
1. **Dashboard**: Query Polymarket `/trades` API directly for trade data (prices, sizes, sides), keep Supabase for WIN/LOSS outcomes
2. **Bot**: Fix `trading_bot_smart.py` to query Polymarket API after fills and log actual execution prices in CAPTURE_FILL events

## Why This Approach

- Polymarket API is the authoritative source: `https://data-api.polymarket.com/trades?user={wallet}&limit=500&side=BUY`
- Returns actual fill prices (varies: 97c, 98c, 99c), not the bid price (always 99c)
- API is public, no auth needed, returns slug + outcome + price + size + timestamp
- Bot's CAPTURE_FILL events log `order.get('price')` which is the limit order price, not execution price

## Key Decisions

1. **Dashboard data source**: Polymarket API for trades + Supabase for WIN/LOSS outcomes
2. **Join key**: Polymarket slug = Supabase Window ID (both are `btc-updown-15m-{timestamp}`)
3. **P&L**: Still computed from Supabase WIN/LOSS events (Polymarket API doesn't include P&L)
4. **Wallet address**: Already public (blockchain), OK to have in client-side JS
5. **Bot fix**: Query Polymarket `/trades` after fill to get real execution price, log in CAPTURE_FILL

## Open Questions

- CORS: Need to test if `data-api.polymarket.com` allows browser requests from GitHub Pages origin
- Fallback: If Polymarket API fails, should dashboard fall back to Supabase events?
