# Status

## Purpose

- This file is the short-lived execution snapshot for handoffs.
- It captures current state (`Now`), upcoming work (checklist), blockers, and known limitations.
- Update it frequently: **remove** tasks when done rather than marking them checked. Phase summaries go into `PLAN.md` progress notes.
- It should summarize deltas from the roadmap in `PLAN.md`, not restate the full plan.

## Now
- Phases 1–2 complete. Phase 3 complete. Phase 4 in progress. Phase 6 hardening ongoing.
- `alpha construct` implemented: score-proportional weights with position cap, min holdings, country deviation, turnover blending, and snapshot persistence.
- 164 tests pass (including 16 construction tests, 6 BDD scenarios). Lint and format clean.
- Next: implement rebalance engine (`alpha rebalance`) and backtest runner (`alpha backtest`).

## Upcoming Work

### Phase 3 (in progress)

### Phase 4
- [ ] Build rebalance engine (`alpha rebalance`)
- [ ] Build historical backtest runner around point-in-time snapshots (`alpha backtest`)
- [ ] Add explicit degraded-assumption warnings for free-source data in backtest
- [ ] Add sector deviation enforcement once sector metadata is in seed universe

### Phase 5
- [ ] Complete CLI surface: `alpha rebalance`, `alpha backtest`, `alpha report`
- [ ] Add portfolio analytics (returns, volatility, Sharpe, drawdown)
- [ ] Add benchmark-relative analytics (tracking error, information ratio, attribution)
- [ ] Add performance snapshot persistence

## Blocked
- No hard blockers currently.

## Known Limitations
- EDGAR fundamentals coverage is US-centric, so mixed universes still rely on graceful degradation for ex-US symbols without fundamentals snapshots.
- `alpha backtest` is scaffolded but not implemented yet.
- Sector deviation enforcement deferred until sector metadata is added to seed universe.
- `azure_blob` backend remains a contract seam and intentionally raises `NotImplementedError` until DevOps phase implementation.
- Optional cleanup pending: migrate Pydantic `Config` usage to `ConfigDict` to remove deprecation warnings.

## Test Commands
- All tests: `uv run pytest -q`
- Unit/function tests: `uv run pytest tests/test_models.py tests/test_profiles.py tests/test_analytics.py -q`
- BDD scenarios: `uv run pytest tests/bdd -q`
