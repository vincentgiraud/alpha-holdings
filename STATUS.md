# Status

## Purpose

- This file is the short-lived execution snapshot for handoffs.
- It captures current state (`Now`), immediate priorities (`Next`), blockers, and recent completions.
- Update it frequently during implementation work.
- It should summarize deltas from the roadmap in `PLAN.md`, not restate the full plan.

## Now
- Phase 2 provider abstraction is complete.
- Provider ABCs (`base.py`) and free adapter skeletons are implemented.
- Canonical normalization layer is implemented for Yahoo/Stooq/EDGAR payload shapes.
- Storage abstraction seam is implemented: local backend (Parquet snapshots + DuckDB metadata) and `azure_blob` backend contract placeholder.
- `alpha refresh` now runs end-to-end: loads universe CSV, fetches prices with fallback source support, and persists raw + normalized snapshots via the storage backend.
- Snapshot inspection commands are implemented: `alpha list-snapshots` and `alpha show-snapshot`.
- 77 tests pass.

## Next (Top 3)
1. Start Phase 3 universe design: seed constrained US + developed ex-US universe and enforce identifier/liquidity filters.
2. Start Phase 3 scoring model: implement config-driven fundamental scoring with per-factor contribution outputs.
3. Complete Phase 6 parity hardening task: add provider contract tests for a mock paid adapter.

## Blocked
- No hard blockers currently.

## Known Limitations
- `alpha score`, `alpha construct`, and `alpha backtest` are scaffolded but not implemented yet.
- `azure_blob` backend remains a contract seam and intentionally raises `NotImplementedError` until DevOps phase implementation.
- Optional cleanup pending: migrate Pydantic `Config` usage to `ConfigDict` to remove deprecation warnings.

## Done This Week
- Bootstrapped uv-managed Python project and repo structure.
- Added canonical domain contracts and investor profile models.
- Implemented `ProfileToConstraints` and `AssetAllocator` logic.
- Added goal-aware analytics module.
- Added CLI/config skeleton and environment configuration.
- Added canonical normalization helpers and tests for Yahoo/Stooq/EDGAR row normalization.
- Added storage backend abstraction with a local backend (Parquet + DuckDB) and cloud-ready `azure_blob` seam.
- Wired `alpha refresh` to the provider + normalization + storage path and added orchestration tests.
- Added snapshot discovery/inspection commands (`alpha list-snapshots`, `alpha show-snapshot`).
- Added manual-test fixtures under `tests/fixtures/` for normal, duplicate, empty, and `symbol` alias universe inputs.
- Added unit tests and BDD scenarios.
- Updated `PLAN.md` with testing strategy and command workflows.

## Test Commands
- All tests: `uv run pytest -q`
- Unit/function tests: `uv run pytest tests/test_models.py tests/test_profiles.py tests/test_analytics.py -q`
- BDD scenarios: `uv run pytest tests/bdd -q`
