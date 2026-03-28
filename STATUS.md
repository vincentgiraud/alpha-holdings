# Status

## Purpose

- This file is the short-lived execution snapshot for handoffs.
- It captures current state (`Now`), upcoming work (checklist), blockers, and known limitations.
- Update it frequently: **remove** tasks when done rather than marking them checked. Phase summaries go into `PLAN.md` progress notes.
- It should summarize deltas from the roadmap in `PLAN.md`, not restate the full plan.

## Now

- Phases 1–8 complete.
- Phase 7 step 17 delivered: construction/backtest degraded assumptions are annotated in CLI/report output.
- Backtest warnings are now persisted in snapshot metadata and carried into performance reports.
- Phase 8 step 18 delivered: backtests now use fundamentals snapshots aligned to rebalance date (on-or-before selection).
- Phase 8 step 19 delivered: seed reference data now fails fast on missing sector/country/benchmark fields.
- Phase 8 step 20 delivered: performance reports now include holdings continuity (realized/unrealized totals and rollforward delta).
- Phase 8 step 21 delivered: CLI workflows now write machine-readable run manifests with inputs, outputs, warnings, and artifact paths.
- Full verification pass complete: 302 tests pass; lint and format checks clean.
- Release 0.1.0 remains current baseline.

## Upcoming Work

### Next Planning

- [ ] Define Phase 9 scope and priorities in PLAN.md.

## Blocked

- No hard blockers currently.

## Known Limitations

- EDGAR fundamentals coverage is US-centric, so mixed universes still rely on graceful degradation for ex-US symbols without fundamentals snapshots.
- Backtest fundamentals scoring uses latest available snapshot (point-in-time alignment approximate). Financial snapshots added in future enhancement for period-exact alignment.
- `azure_blob` backend remains a contract seam and intentionally raises `NotImplementedError` until DevOps phase implementation.
- Portfolio holdings state (book cost, realized gains) tracked via `holdings_snapshot_{portfolio_id}` dataset; each rebalance run persists a new snapshot.

## Test Commands

- All tests: `uv run pytest -q`
- Unit/function tests: `uv run pytest tests/test_models.py tests/test_profiles.py tests/test_analytics.py -q`
- BDD scenarios: `uv run pytest tests/bdd -q`
- Provider contracts: `uv run pytest tests/test_provider_contracts.py -q`
- Upgrade-path validation: `uv run pytest tests/test_upgrade_path.py -q`
- Backtest tests: `uv run pytest tests/test_backtest.py -q`
