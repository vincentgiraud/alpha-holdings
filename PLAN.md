# Plan: Free-Data Upgradeable Strategy Engine

## Purpose

- This file is the long-lived project roadmap.
- It captures scope, phased milestones, architectural decisions, and verification strategy.
- Update it when priorities, scope boundaries, or milestone completion states change.
- Do not use it for day-to-day session notes; those belong in `STATUS.md`.

Build a Python-first research platform managed with uv, starting from free daily market and filing-derived data but structured so paid vendors can be added later by swapping provider adapters rather than rewriting portfolio logic. The first milestone should bootstrap the repository, define stable data contracts, and deliver an end-to-end research loop for a paper portfolio with scoring, construction, rebalancing, backtesting, and performance analytics.

## Phase Completion Snapshot

- [x] Phase 1 (Bootstrap/contracts/profile/allocation/goal analytics): done
- [x] Phase 2 (Provider abstraction/adapters/normalization/storage): done
- [x] Phase 3 (Universe and scoring): done
- [x] Phase 4 (Construction/rebalance/backtest): done
- [ ] Phase 5 (Analytics workflows and full CLI surface): in progress
- [ ] Phase 6 (Upgrade-path hardening and final docs): in progress

Phase 5 progress notes (remove when phase completes):
- Rebalance engine (`alpha rebalance`) implemented: reads latest target vs. prior weights, generates buy/sell trade proposals with share counts and values from latest prices, persists as `trade_proposals` snapshot.
- Backtest runner (`alpha backtest`) implemented: walk-forward simulation over stored price data with in-memory scoring at each rebalance date, daily NAV tracking with weight drift, configurable frequency (weekly/monthly/quarterly), benchmark comparison.
- Performance report (`alpha report`) implemented: reads backtest NAV series and computes total/annualized return, volatility, Sharpe, max drawdown, Calmar ratio, best/worst day, benchmark-relative metrics (excess return, tracking error, information ratio), persists as `performance_report` snapshot.
- 43 new tests (9 rebalance, 15 backtest, 19 report). 207 total tests pass.
- Next: benchmark-relative analytics (attribution), portfolio snapshot persistence improvements.

## Steps

- [x] **1. Phase 1: Bootstrap.** Initialize the repository with uv-managed Python packaging, project metadata, lint/test tooling, a top-level README.md, and a .gitignore tuned for Python, uv, caches, notebooks, environment files, and local data artifacts.

- [x] **2. Phase 1: Bootstrap.** Establish the package layout under src so data ingestion, normalization, storage, domain models, scoring, portfolio construction, rebalancing, backtesting, analytics, and CLI entry points are isolated from each other. Parallel with step 1.

- [x] **3. Phase 1: Contracts.** Define canonical schemas for Security, IdentifierMap, PriceBar, CorporateAction, FundamentalSnapshot, BenchmarkConstituent, Holding, TargetWeight, TradeProposal, and PerformanceSnapshot. Add explicit metadata for source, as-of date, publish date, currency, and data quality flags. Depends on steps 1 and 2.

- [x] **3a. Phase 1: Investor profile.** Define an `InvestorProfile` model with fields: `fire_variant` (fat_fire | lean_fire | barista_fire | coast_fire | retirement_complement), `risk_appetite` (1–5 integer scale), `horizon_years` (integer), `withdrawal_pattern` (lump_sum | regular_drawdown | compound_only), `target_real_return_pct` (optional float), and `crypto_enabled` (bool, default false). Add a `ProfileToConstraints` resolver that maps these fields to concrete portfolio engine defaults: max single-name weight, sector deviation band, country deviation band, max annualized turnover, min holdings count, max portfolio volatility, max drawdown tolerance, rebalance cadence, and scoring factor holding-period bias. Depends on steps 1 and 2.

- [x] **3c. Phase 1: Asset class allocation.** Add an `AssetClass` enum (equity, bond, crypto) and an `AssetClassAllocation` model that holds target weight bands per sleeve. Implement an `AssetAllocator` that derives the top-level equity/bond/crypto split from `InvestorProfile` before any security selection occurs. Example mappings: fat_fire 20yr risk 4–5 → 85% equity / 12% bond / 3% crypto; lean_fire 10yr risk 3 → 80% equity / 20% bond / 0% crypto; retirement_complement 5yr risk 2 → 50% equity / 45% bond / 5% optional crypto. Crypto sleeve is only emitted when `crypto_enabled=true` and `risk_appetite >= 4`. Bond sleeve is always present and its weight floor rises as `horizon_years` decreases and `risk_appetite` decreases. Security selection (scoring model, ETF proxy for bonds, capped crypto ETF proxy) runs independently within each sleeve's weight band. Depends on step 3a.

- [x] **3b. Phase 1: Goal analytics.** Extend the analytics module to include profile-aware reporting: probability of reaching a wealth target given the profile, sequence-of-returns risk summary for profiles near withdrawal, and safe withdrawal rate estimate. These are additive outputs alongside standard benchmark-relative analytics. Depends on step 3a.

- [x] **4. Phase 2: Provider abstraction.** Implement provider interfaces for prices, fundamentals, reference data, classifications, FX, and benchmarks. The free-source adapters and later paid adapters must conform to the same contracts so scoring and portfolio code remain vendor-agnostic. Depends on step 3.

- [x] **5. Phase 2: Free-source adapters.** Plan initial adapters for Yahoo Finance and Stooq price history, SEC EDGAR and company filing ingestion for raw fundamentals, and curated static mappings for sectors, countries, exchanges, and benchmark proxies. Depends on step 4.

- [x] **6. Phase 2: Normalization and storage.** Add canonical normalization rules and local storage that combine relational metadata with parquet snapshots for reproducible research runs. Store raw payloads separately from normalized tables so vendor swaps and audit/debug flows remain possible. Depends on steps 4 and 5.

- [x] **7. Phase 3: Universe design.** Start with a deliberately constrained free-data universe: US large-cap plus a curated developed ex-US subset, rather than full developed markets immediately. Add identifier mapping, currency normalization, liquidity filters, and benchmark-proxy membership rules. Depends on steps 3 through 6.

- [x] **8. Phase 3: Scoring model.** Implement a transparent, config-driven fundamental score using only factors that can be supported credibly with free inputs at first, and record per-security factor contributions to make later vendor comparisons measurable. Depends on steps 5 through 7.

- [x] **9. Phase 4: Portfolio engine.** Add benchmark-aware portfolio construction with ETF-like stability controls: max position size, sector and country deviation bands, turnover limits, liquidity rules, and minimum holdings. Depends on step 8.

- [x] **10. Phase 4: Rebalancing and backtesting.** Build the rebalance engine and historical runner around point-in-time snapshots where available, and add explicit warnings when free-source data forces weaker assumptions. Depends on steps 6 through 9.

- [ ] **11. Phase 5: Analytics and operator surface.** Add CLI workflows for refresh, normalize, score, construct, rebalance, backtest, and report, along with portfolio, benchmark, and attribution analytics. Depends on steps 6 through 10.

- [ ] **12. Phase 6: Upgrade path hardening.** Add tests focused on contract compliance, provider parity, normalization invariants, and benchmark-relative risk controls so a future paid vendor can be introduced with adapter-level validation rather than system-wide rewrites. Depends on steps 4 through 11.

- [ ] **13. Phase 6: Documentation.** Document the free-data limitations, upgrade seams, supported workflows, and the exact process for adding a paid provider implementation later. Depends on all prior steps.

## Verification

1. Verify the uv bootstrap by creating the environment, installing dependencies, and running the test and lint commands from a clean checkout.
2. Add contract tests that every provider adapter, free or paid, must pass for identifiers, prices, fundamentals, currencies, and missing-data behavior.
3. Run a seeded workflow over a small sample universe and confirm reproducible outputs for normalized datasets, score tables, target weights, trade proposals, and performance analytics.
4. Validate that portfolio construction still respects sector, country, position-size, liquidity, and turnover limits when underlying data comes from the free adapters.
5. Simulate a future vendor migration by running the same downstream scoring and construction tests against a mock paid adapter with the same contracts.
6. Manually verify that README.md explains setup, free-data limitations, local storage behavior, and the path for introducing paid providers later.

### Current Test Strategy (Implemented)

The test suite is split into two layers for readability and intent clarity.

1. **Function/unit tests (TDD-oriented):** fast checks for pure logic, validation, and deterministic calculations.
   - Files: `tests/test_models.py`, `tests/test_profiles.py`, `tests/test_analytics.py`
2. **Scenario tests (BDD-oriented):** business behavior in Given/When/Then format using `pytest-bdd`.
   - Files: `tests/bdd/features/asset_allocation.feature`, `tests/bdd/test_asset_allocation_bdd.py`, `tests/bdd/features/scoring.feature`, `tests/bdd/test_scoring_bdd.py`

### Current Scenario Coverage (Implemented)

**Asset allocation (3 scenarios):**
1. Crypto remains excluded when `crypto_enabled=true` but `risk_appetite=3`.
2. Crypto is included when `crypto_enabled=true` and `risk_appetite=4`.
3. For otherwise identical profiles, shorter horizon (5y) results in higher bond target than longer horizon (20y).

**Equity scoring (3 scenarios):**
4. Symbols without fundamentals are scored and flagged as degraded (zero fundamentals factor contributions).
5. Fundamentals factors contribute to rank differences when price histories are identical.
6. Partial fundamentals row (missing some fields) does not crash scoring and the symbol is still marked as having fundamentals.

### Test Commands

1. Run all tests:
   - `uv run pytest -q`
2. Run function/unit tests only:
   - `uv run pytest tests/test_models.py tests/test_profiles.py tests/test_analytics.py -q`
3. Run scenario/BDD tests only:
   - `uv run pytest tests/bdd -q`
4. Run a single function test:
   - `uv run pytest tests/test_profiles.py::TestAssetAllocator::test_allocate_fat_fire_with_crypto -q`
5. Run a single BDD scenario by text filter:
   - `uv run pytest tests/bdd -k "Shorter horizon increases bond allocation" -q`

## Decisions

- **Included in initial build:** uv-based Python project setup, .gitignore, README.md, free-data provider abstraction, paper portfolio operation, scoring, portfolio construction, rebalancing, backtesting, and benchmark-relative analytics.
- **Excluded from initial build:** live execution, intraday processing, derivatives, tax optimization, full global coverage on day one, and a web dashboard.
- **Multi-asset architecture:** portfolio construction is two-tier. Tier 1 is the `AssetAllocator` which produces sleeve weight bands (equity, bond, crypto) from the investor profile. Tier 2 is per-sleeve security selection (equity scoring model; bond ETF proxy via Yahoo Finance; crypto ETF proxy capped and gated by profile flags). The two tiers are independent so bond or crypto sleeves can be evolved without touching equity construction.
- **Bonds:** always included as a sleeve; weight floor is profile-driven (minimum ~5% for long-horizon aggressive profiles, up to ~50% for near-withdrawal conservative profiles). Free data source: US Treasury and aggregate bond ETF proxies via Yahoo Finance; FRED for rate context.
- **Crypto:** opt-in satellite sleeve, enabled only when `crypto_enabled=true` and `risk_appetite >= 4`. Represented by a capped broad crypto ETF proxy (e.g. ticker-based), never individual coins. Max sleeve weight: 5% for risk 4, 10% for risk 5. Explicitly documented as speculative and not aligned with the ETF-like stability goal of the equity sleeve.
- **Recommended starting universe:** US large-cap plus a curated developed ex-US subset until identifier, filings, and benchmark-proxy coverage are stable.
- **Recommended benchmark approach for free data:** use a public ETF proxy and constrain sector and country deviations rather than pretending to have licensed index constituent history.
- **Recommended upgrade strategy:** reserve a dedicated paid-provider namespace and enforce adapter contracts so a later Bloomberg, FactSet, or LSEG integration is an additive change.
- **Storage backend seam decision (implemented):** storage now uses a backend abstraction so refresh/scoring code can remain backend-agnostic. Local backend writes parquet snapshots and DuckDB metadata; cloud backend key `azure_blob` is reserved with explicit config contract and will be implemented during DevOps.
- **Recommended execution model:** batch-oriented refresh and rebalance workflows, typically daily data refresh with monthly or quarterly rebalance cadence.
- **Python version:** 3.12. Key dependencies: `pydantic` v2 for domain models and contracts, `yfinance` for Yahoo Finance adapter, `pandas` and `pyarrow` for data handling and parquet, `duckdb` for local relational metadata and analytics queries, `typer` for the CLI, `pytest` for tests, `ruff` for linting and formatting.
- **Seed test universe:** 25 US large-cap names plus 10 developed ex-US names stored as a static fixture CSV at `tests/fixtures/seed_universe.csv`. This universe is used for all deterministic backtest, rebalancing, and analytics tests.
- **Default portfolio constraint values** (all config-driven and overridable via InvestorProfile): max single-name weight 5%, sector deviation vs. benchmark ±5pp, country deviation vs. benchmark ±5pp, annual turnover cap 50%, minimum holdings 30. The ProfileToConstraints resolver may tighten or relax these based on horizon, risk appetite, and FIRE variant — for example, a coast_fire profile with 15+ years will loosen turnover to 30% and widen sector bands; a retirement_complement profile near withdrawal will tighten max drawdown tolerance and enforce a minimum cash-like allocation.
- **Testing organization decision:** keep unit/function tests and BDD scenario tests separate to improve readability. Unit tests protect internal logic contracts; BDD tests protect business behavior and policy rules.

## Further Considerations

1. Free data is best for proving pipeline design and portfolio logic, not for making strong claims about production alpha. The plan should preserve that distinction in README.md and test fixtures.
2. The biggest future migration risk is identifier drift across exchanges and vendors. The canonical model should treat internal security IDs and identifier maps as first-class entities from the start.
3. Raw filing ingestion can become a time sink if V1 chases too much automation. The better path is to support manual or semi-structured fundamentals for the first covered universe, then automate once the scoring model is stable.
4. The architecture should make provider capability gaps explicit. If a source cannot supply benchmark constituents, publish dates, or adjusted price history reliably, the system should flag degraded assumptions instead of silently proceeding.
