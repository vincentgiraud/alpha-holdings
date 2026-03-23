# Status

## Purpose

- This file is the short-lived execution snapshot for handoffs.
- It captures current state (`Now`), upcoming work (checklist), blockers, and known limitations.
- Update it frequently: **remove** tasks when done rather than marking them checked. Phase summaries go into `PLAN.md` progress notes.
- It should summarize deltas from the roadmap in `PLAN.md`, not restate the full plan.

## Now
- Phases 1 and 2 are complete. Phase 3 is in progress.
- `alpha score --date ...` is functional: liquidity-filtered universe from snapshots, deterministic factor scoring (momentum, low-volatility, liquidity), and `equity_scores` snapshot persistence.
- Seeded constrained universe (25 US large-cap + 10 developed ex-US) with identifier mapping, currency normalization, and benchmark proxy assignments.
- 80 tests pass.

## Upcoming Work

### Phase 3 (in progress)
- [ ] Evolve scoring from price-based starter factors to fundamentals-backed factors
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
- `alpha score` currently uses a transparent price-derived starter factor set (momentum, low-volatility, liquidity) while fundamentals ingestion is expanded.
- `alpha construct` and `alpha backtest` are scaffolded but not implemented yet.
- `azure_blob` backend remains a contract seam and intentionally raises `NotImplementedError` until DevOps phase implementation.
- Optional cleanup pending: migrate Pydantic `Config` usage to `ConfigDict` to remove deprecation warnings.

## Test Commands
- All tests: `uv run pytest -q`
- Unit/function tests: `uv run pytest tests/test_models.py tests/test_profiles.py tests/test_analytics.py -q`
- BDD scenarios: `uv run pytest tests/bdd -q`
