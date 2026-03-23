# Alpha Holdings: Free-Data Upgradeable Strategy Engine

A Python-first research platform for portfolio construction, rebalancing, backtesting, and performance analytics. Designed to bootstrap with free data sources (Yahoo Finance, SEC EDGAR, Stooq) while maintaining a clean abstraction layer that allows seamless upgrade to paid data vendors (Bloomberg, FactSet, LSEG) without rewriting portfolio logic.

## Current Implementation Status

Implemented now:
- Domain contracts and investor profile models
- Profile-to-constraints resolver and top-level asset allocation
- Goal analytics baseline
- Provider abstraction, free-source adapters, normalization, and local snapshot storage
- Seeded constrained universe with liquidity filtering and benchmark proxy assignments
- Snapshot-driven equity scoring with transparent factor contributions and persisted `equity_scores`
- Unit tests and BDD scenarios

Partially implemented now:
- `alpha refresh`, `alpha list-snapshots`, `alpha show-snapshot`, and `alpha score` are functional
- `alpha construct` and `alpha backtest` exist as CLI commands but are placeholders only

Planned next:
- Phase 3 expansion: richer universe rules and fundamentals-backed factor inputs
- Portfolio construction, rebalance, backtest command workflows

## Quick Start

### Prerequisites
- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (fast Python package installer and resolver)

### Installation

1. **Clone and setup environment:**
   ```bash
   git clone <repo-url>
   cd alpha-holdings
   uv venv
   source .venv/bin/activate  # or .venv\Scripts\activate on Windows
   ```

2. **Install dependencies:**
   ```bash
   uv sync --extra dev
   ```

3. **Configure environment:**
   ```bash
   cp .env.example .env
   # Edit .env as needed
   ```

4. **Verify installation:**
   ```bash
   uv run pytest -q
   ```

## Architecture Overview

```
alpha_holdings/
├── domain/                   # Data contracts and business models
│   ├── models.py            # Security, Holding, TargetWeight, PerformanceSnapshot
│   ├── investor_profile.py  # InvestorProfile, FireVariant, AssetClass, AssetClassAllocation
│   └── ...
├── data/
│   ├── providers/           # Provider abstraction and implementations
│   │   ├── base.py          # Abstract interfaces (PriceProvider, FundamentalProvider, etc.)
│   │   ├── free/            # Free adapters (yahoo.py, stooq.py, edgar.py)
│   │   └── paid/            # Future: Bloomberg, FactSet, LSEG adapters
│   ├── normalization.py     # Source-to-canonical transformations
│   └── storage.py           # Local metadata and parquet snapshots
├── portfolio/
│   └── asset_allocation.py  # AssetAllocator: profile → sleeve weights
├── universe/
│   └── builder.py           # Universe construction and benchmark proxies
├── scoring/
│   └── fundamental_model.py # Price-derived starter factors and composite score
├── backtest/
│   └── __init__.py          # Backtesting package placeholder
├── analytics/
│   ├── performance.py       # Return and risk analytics
│   └── goal.py              # Goal-aware analytics: wealth probability, SWR, etc.
└── cli.py                   # Operator entry points
```

## Key Features

### Multi-Asset Architecture
- **Two-tier construction:** Asset Allocator generates sleeve weights (equity/bond/crypto) from InvestorProfile; per-sleeve security selection follows independently
- **Bonds:** Always included; weight floor rises as horizon shrinks and risk appetite decreases
- **Crypto:** Opt-in satellite sleeve (enabled when `crypto_enabled=true` and `risk_appetite >= 4`); capped broad ETF proxy, never individual coins
- **Planned stability controls:** Max position size, sector/country deviation bands, turnover limits, liquidity rules

### Free-Data Foundation
- **Yahoo Finance:** Daily price history and metadata
- **Stooq:** Secondary price source and cross-validation
- **SEC EDGAR:** Filing ingestion for fundamentals
- **Static mappings:** Sectors, countries, exchanges, benchmark proxies

### Reproducible Research
- Point-in-time data snapshots with explicit publish/as-of dates
- Raw payload storage for vendor swaps and audit trails
- Deterministic scoring and backtesting across runs

### Smooth Upgrade Path
- Provider interfaces and adapters enforce contract compliance
- Free and paid implementations coexist; test parity between sources
- Downstream logic (scoring, construction, rebalancing) remains vendor-agnostic

## Free-Data Caveats

1. **Identifier drift:** Ticker symbols can change across exchanges and historical periods. External match-on-name risk exists until identifier maps are comprehensively curated.
2. **Benchmark coverage:** Public ETF proxies are used; actual licensed index constituent history is not available.
3. **Filing latency:** SEC filings are published with a lag; near-real-time fundamental updates are not supported.
4. **Survivorship bias:** Historical price and identifier downloads may miss delisted or renamed securities without explicit curation.

## Recommended Workflows

Implemented today:

### Refresh and Normalize
```bash
uv run alpha refresh --universe tests/fixtures/seed_universe.csv
```

This refreshes price data using the configured provider settings and writes snapshots to the configured storage backend.

### Inspect Snapshots
```bash
uv run alpha list-snapshots
uv run alpha show-snapshot --dataset aapl_prices --as-of 2026-03-23
```

### Score Equities
```bash
uv run alpha score --date 2026-03-23
```

This computes the current starter score from persisted price snapshots and writes an `equity_scores` snapshot.

Planned next:

### Score and Construct
```bash
uv run alpha score --date 2025-01-31
uv run alpha construct --date 2025-01-31
```

`alpha score` is implemented. `alpha construct` is currently a placeholder command.

### Backtest
```bash
uv run alpha backtest --start-date 2020-01-01 --end-date 2025-01-31
```

`alpha backtest` is currently a placeholder command.

### Analyze
```bash
uv run alpha analyze --backtest-output backtest_results.parquet --benchmark SPY
```

This workflow is planned and not implemented yet.

## Upgrade Path

To add a paid data vendor (e.g., Bloomberg):

1. Create a new adapter at `src/alpha_holdings/data/providers/paid/bloomberg.py`
2. Implement the same `PriceProvider`, `FundamentalProvider`, etc. interfaces
3. Update config/environment to select the paid adapter
4. Run existing test suite against the new adapter; pass or fix gaps in the new vendor
5. No changes needed to scoring, construction, rebalancing, or analytics logic

## Development

### Run tests
```bash
uv run pytest -q
```

### Format and lint
```bash
ruff check src/ tests/ --fix
ruff format src/ tests/
```

### Coverage report
```bash
uv run pytest --cov=src/alpha_holdings
open htmlcov/index.html
```

## Tracking

- Long-term roadmap: `PLAN.md`
- Active execution status: `STATUS.md`

## Configuration

See `.env.example` for all available settings:
- Data source selection and fallbacks
- Storage paths and database configuration
- Portfolio constraint defaults
- Rebalance schedules
- Analysis windows and confidence levels

## Project Structure

- **Phase 1 (Bootstrap):** uv project setup, package layout, data contracts, investor profile models, asset allocation, goal analytics
- **Phase 2 (Provider abstraction):** Interface definitions, free adapters, normalization, local storage
- **Phase 3 (Universe design):** Constrained free-data universe, identifier mapping, scoring model
- **Phase 4 (Portfolio engine):** Construction, rebalancing, backtesting
- **Phase 5 (Analytics & CLI):** Performance reporting, operator workflows
- **Phase 6 (Hardening):** Contract tests, provider parity validation, upgrade seams

## License

MIT
