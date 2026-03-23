# Status

## Purpose

- This file is the short-lived execution snapshot for handoffs.
- It captures current state (`Now`), upcoming work (checklist), blockers, and known limitations.
- Update it frequently: **remove** tasks when done rather than marking them checked. Phase summaries go into `PLAN.md` progress notes.
- It should summarize deltas from the roadmap in `PLAN.md`, not restate the full plan.

## Now
- Phases 1 and 2 are complete. Phase 3 is in progress.
- `alpha refresh --universe ...` now persists price snapshots and EDGAR fundamentals snapshots where available. Provider-native ticker resolution ensures ex-US names (Swiss, German, UK, Canadian exchanges) and share-class tickers (BRK.B) fetch correctly.
- `alpha score --date ...` is functional: liquidity-filtered universe from snapshots, mixed price/fundamentals factor scoring (momentum, low-volatility, liquidity, profitability, balance-sheet quality, cash-flow quality), explicit degradation for missing fundamentals, and `equity_scores` snapshot persistence.
- Seeded constrained universe (25 US large-cap + 10 developed ex-US) with identifier mapping, currency normalization, and benchmark proxy assignments.
- Manual smoke test: all 35/35 tickers refresh, 35/35 symbols scored.
- 92 tests pass (includes 6 BDD scenarios: 3 asset-allocation, 3 scoring).

## Upcoming Work

### Phase 3 (in progress)
- [ ] Add provider contract tests for a mock paid adapter (Phase 6 parity)

### Phase 4
- [ ] Implement benchmark-aware portfolio construction (`alpha construct`)
- [ ] Add position size, sector/country deviation, turnover, liquidity, and min holdings constraints
- [ ] Build rebalance engine (`alpha rebalance`)
- [ ] Build historical backtest runner around point-in-time snapshots (`alpha backtest`)
- [ ] Add explicit degraded-assumption warnings for free-source data in backtest

### Phase 5
- [ ] Complete CLI surface: `alpha construct`, `alpha rebalance`, `alpha backtest`, `alpha report`
- [ ] Add portfolio analytics (returns, volatility, Sharpe, drawdown)
- [ ] Add benchmark-relative analytics (tracking error, information ratio, attribution)
- [ ] Add performance snapshot persistence

## Blocked
- No hard blockers currently.

## Known Limitations
- EDGAR fundamentals coverage is US-centric, so mixed universes still rely on graceful degradation for ex-US symbols without fundamentals snapshots.
- `alpha construct` and `alpha backtest` are scaffolded but not implemented yet.
- `azure_blob` backend remains a contract seam and intentionally raises `NotImplementedError` until DevOps phase implementation.
- Optional cleanup pending: migrate Pydantic `Config` usage to `ConfigDict` to remove deprecation warnings.

## Test Commands
- All tests: `uv run pytest -q`
- Unit/function tests: `uv run pytest tests/test_models.py tests/test_profiles.py tests/test_analytics.py -q`
- BDD scenarios: `uv run pytest tests/bdd -q`
