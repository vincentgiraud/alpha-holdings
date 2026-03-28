# Status

## Purpose

- This file is the short-lived execution snapshot for handoffs.
- It captures current state (`Now`), upcoming work (checklist), blockers, and known limitations.
- Update it frequently: **remove** tasks when done rather than marking them checked. Phase summaries go into `PLAN.md` progress notes.
- It should summarize deltas from the roadmap in `PLAN.md`, not restate the full plan.

## Now

- Phases 1–6 complete.
- Phase 7 scope and priorities defined in PLAN.md.
- Phase 7 step 14 (sector metadata completeness) delivered.
- Phase 7 step 15 (fundamentals-aware backtest) delivered: backtest runner now loads point-in-time fundamentals snapshots and includes fundamentals factors in scoring when available.
- Release 0.1.0 finalized and changelog created.
- Full verification pass complete: 292 tests pass; lint and format checks clean.

## Upcoming Work

### Post-0.1.0

- [ ] Migrate remaining Pydantic `Config` usage to `ConfigDict`.
- [ ] Add tests and report/CLI annotations for degraded execution paths.

## Blocked

- No hard blockers currently.

## Known Limitations

- EDGAR fundamentals coverage is US-centric, so mixed universes still rely on graceful degradation for ex-US symbols without fundamentals snapshots.
- Backtest fundamentals scoring uses latest available snapshot (point-in-time alignment approximate). Financial snapshots added in future enhancement for period-exact alignment.
- Sector deviation enforcement deferred until sector metadata is added to seed universe.
- `azure_blob` backend remains a contract seam and intentionally raises `NotImplementedError` until DevOps phase implementation.
- Portfolio holdings state (book cost, realized gains) tracked via `holdings_snapshot_{portfolio_id}` dataset; each rebalance run persists a new snapshot.
- Optional cleanup pending: migrate Pydantic `Config` usage to `ConfigDict` to remove deprecation warnings (already complete — no outstanding issues).

## Test Commands

- All tests: `uv run pytest -q`
- Unit/function tests: `uv run pytest tests/test_models.py tests/test_profiles.py tests/test_analytics.py -q`
- BDD scenarios: `uv run pytest tests/bdd -q`
- Provider contracts: `uv run pytest tests/test_provider_contracts.py -q`
- Upgrade-path validation: `uv run pytest tests/test_upgrade_path.py -q`
- Backtest tests: `uv run pytest tests/test_backtest.py -q`
