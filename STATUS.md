# Status

## Purpose

- This file is the short-lived execution snapshot for handoffs.
- It captures current state (`Now`), upcoming work (checklist), blockers, and known limitations.
- Update it frequently: **remove** tasks when done rather than marking them checked. Phase summaries go into `PLAN.md` progress notes.
- It should summarize deltas from the roadmap in `PLAN.md`, not restate the full plan.

## Now
- Phases 1–4 complete. Phase 5 nearing completion. Phase 6 hardening ongoing.
- Factor attribution via returns-based style analysis implemented (`analytics/attribution.py`).
- HTML report output implemented (`analytics/html_report.py`) with NAV chart, drawdown chart, attribution bars, weight history stacked area.
- Backtest now tracks `weight_history` at each rebalance for visualization.
- `alpha report --html <path>` generates self-contained HTML report with all sections.
- 268 tests pass (241 prior + 27 new). Lint and format clean.

## Upcoming Work

### Phase 5
(All major items complete.)

### Phase 6
- [ ] Contract compliance tests for multi-vendor scenarios
- [ ] Upgrade-path validation (mock paid provider)
- [ ] Final documentation and README updates
- [ ] Release 0.1.0 checklist

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
