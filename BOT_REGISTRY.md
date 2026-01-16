# Polybot Version Registry

## Current Version: v1.2 "Silent Thunder"

| Version | DateTime | Codename | Changes | Status |
|---------|----------|----------|---------|--------|
| v1.2 | 2026-01-16 00:30 PST | Silent Thunder | Fix: PAIRING_MODE race condition causing duplicate orders | Active |
| v1.1 | 2026-01-15 21:58 PST | Quantum Badger | Auto-redeem: direct CTF contract redemption through Gnosis Safe | Archived |
| v1.0 | 2026-01-15 20:50 PST | Iron Phoenix | Baseline - includes PAIRING_MODE hedge escalation + 99c capture hedge protection | Archived |

## Version History Details

### v1.2 - Silent Thunder (2026-01-16)
- **Bug fix**: Race condition in PAIRING_MODE causing duplicate orders
- Added position re-verification after cancel_all_orders()
- Prevents placing new order if original order already filled

### v1.1 - Quantum Badger (2026-01-15)
- **Auto-redeem feature** for winning positions
- Direct CTF contract redemption via Gnosis Safe execTransaction
- Detects resolved markets with winning positions
- Automatically claims USDC from winning outcome tokens
- Test mode: `test_redeem_detection()` for dry-run

### v1.0 - Iron Phoenix (2026-01-15)
- **Baseline release** with all current features
- ARB trading strategy (buy both sides when combined < $1)
- 99c capture strategy (confidence-based single-side bets)
- PAIRING_MODE with time-based hedge escalation
- 99c capture hedge protection (auto-hedge on confidence drop)
- Google Sheets logging
- Dual-source position verification
- Chainlink price feed integration
- Order book imbalance analysis

---

## Codename Convention
Each version gets a unique two-word codename: `[Adjective] [Animal/Object]`
Examples: Iron Phoenix, Quantum Badger, Silent Thunder, Neon Falcon
