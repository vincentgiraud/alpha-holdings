# Alpha Holdings: Free-Data Upgradeable Strategy Engine

A Python-first research platform for portfolio construction, rebalancing, backtesting, and performance analytics. Designed to bootstrap with free data sources (Yahoo Finance, SEC EDGAR, Stooq) while maintaining a clean abstraction layer that allows seamless upgrade to paid data vendors (Bloomberg, FactSet, LSEG) without rewriting portfolio logic.

## Current Implementation Status

Implemented now:
- Domain contracts and investor profile models
- Profile-to-constraints resolver and top-level asset allocation
- Goal analytics baseline
- Unit tests and BDD scenarios

Partially implemented now:
- CLI surface exists, but only `alpha --version` and `alpha check` are functional

Planned next:
- Provider abstractions and free adapters
- Normalization/storage pipeline
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
│   ├── asset_allocation.py  # AssetAllocator: profile → sleeve weights
│   ├── construction.py      # Target weight generation and constraint enforcement
│   └── rebalance.py         # Trade proposal and turnover controls
├── universe/
│   └── builder.py           # Universe construction and benchmark proxies
├── scoring/
│   └── fundamental_model.py # Free-data-compatible factors and composite score
├── backtest/
│   └── runner.py            # Historical orchestration and data-quality warnings
├── analytics/
│   ├── performance.py       # Return, risk, attribution analytics
│   └── goal.py              # Goal-aware analytics: wealth probability, SWR, etc.
└── cli.py                   # Operator entry points
```

## Key Features

### Multi-Asset Architecture
- **Two-tier construction:** Asset Allocator generates sleeve weights (equity/bond/crypto) from InvestorProfile; per-sleeve security selection follows independently
- **Bonds:** Always included; weight floor rises as horizon shrinks and risk appetite decreases
- **Crypto:** Opt-in satellite sleeve (enabled when `crypto_enabled=true` and `risk_appetite >= 4`); capped broad ETF proxy, never individual coins
- **Stability controls:** Max position size, sector/country deviation bands, turnover limits, liquidity rules

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

The examples below describe target workflows for upcoming phases. The corresponding CLI commands are not all implemented yet.

### Refresh and Normalize
```bash
alpha refresh --universe seed_universe.csv --sources yahoo,edgar --output data/
```

### Score and Construct
```bash
alpha score --date 2025-01-31 --factors value,quality,growth
alpha construct --date 2025-01-31 --profile fat_fire_10yr --constraints default
```

### Backtest
```bash
alpha backtest --start-date 2020-01-01 --end-date 2025-01-31 --profile fat_fire_10yr
```

### Analyze
```bash
alpha analyze --backtest-output backtest_results.parquet --benchmark SPY
```

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
