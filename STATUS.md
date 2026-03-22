# Status

## Now
- Phase 2 provider abstraction is in progress.
- Provider ABCs (`base.py`) and free adapter skeletons are implemented.
- 47 provider contract tests added; all 66 tests pass.

## Next (Top 3)
1. Implement normalization layer and local storage (parquet snapshots + DuckDB metadata).
2. Wire up Yahoo and Stooq adapters in the `alpha refresh` CLI command.
3. Add provider contract tests for a mock paid adapter to validate parity enforcement.

## Blocked
- No hard blockers currently.

## Known Limitations
- `alpha refresh`, `alpha score`, `alpha construct`, and `alpha backtest` are scaffolded but not implemented yet.
- Optional cleanup pending: migrate Pydantic `Config` usage to `ConfigDict` to remove deprecation warnings.

## Done This Week
- Bootstrapped uv-managed Python project and repo structure.
- Added canonical domain contracts and investor profile models.
- Implemented `ProfileToConstraints` and `AssetAllocator` logic.
- Added goal-aware analytics module.
- Added CLI/config skeleton and environment configuration.
- Added unit tests and BDD scenarios.
- Updated `PLAN.md` with testing strategy and command workflows.

## Test Commands
- All tests: `uv run pytest -q`
- Unit/function tests: `uv run pytest tests/test_models.py tests/test_profiles.py tests/test_analytics.py -q`
- BDD scenarios: `uv run pytest tests/bdd -q`
