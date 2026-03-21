# Status

## Now
- Phase 1 foundation is complete and validated.
- Test strategy is split into function/unit tests (TDD-oriented) and BDD scenario tests.
- All current tests are passing.

## Next (Top 3)
1. Implement provider abstraction interfaces in `src/alpha_holdings/data/providers/base.py`.
2. Add free-adapter skeletons (`yahoo.py`, `stooq.py`, `edgar.py`) conforming to contracts.
3. Add provider contract tests to enforce parity between free and future paid adapters.

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
