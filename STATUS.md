# Status

## Purpose

- This file is the short-lived execution snapshot for handoffs.
- It captures current state (`Now`), upcoming work (checklist), blockers, and known limitations.
- Update it frequently: **remove** tasks when done rather than marking them checked. Phase summaries go into `PLAN.md` progress notes.
- It should summarize deltas from the roadmap in `PLAN.md`, not restate the full plan.

## Now
- Phases 1–5 complete. Phase 6 nearing completion.
- Contract compliance and upgrade-path validation tests added (`tests/test_upgrade_path.py`, 22 tests).
- README.md fully rewritten with complete architecture, CLI commands, free-data limitations, and upgrade-path instructions.
- 290 tests pass. Lint and format clean.

## Upcoming Work

### Phase 6
- [ ] Release 0.1.0 checklist (version bump, changelog, final pass)

## Blocked
- No hard blockers currently.

## Known Limitations
- EDGAR fundamentals coverage is US-centric, so mixed universes still rely on graceful degradation for ex-US symbols without fundamentals snapshots.
- Backtest uses in-memory scoring without fundamentals factors (free-source data degradation). Financial snapshots added in future enhancement.
- Sector deviation enforcement deferred until sector metadata is added to seed universe.
- `azure_blob` backend remains a contract seam and intentionally raises `NotImplementedError` until DevOps phase implementation.
- Portfolio holdings state (book cost, realized gains) tracked via `holdings_snapshot_{portfolio_id}` dataset; each rebalance run persists a new snapshot.
- Optional cleanup pending: migrate Pydantic `Config` usage to `ConfigDict` to remove deprecation warnings.

## Test Commands
- All tests: `uv run pytest -q`
- Unit/function tests: `uv run pytest tests/test_models.py tests/test_profiles.py tests/test_analytics.py -q`
- BDD scenarios: `uv run pytest tests/bdd -q`
- Provider contracts: `uv run pytest tests/test_provider_contracts.py -q`
- Upgrade-path validation: `uv run pytest tests/test_upgrade_path.py -q`
