# Alpha Holdings: Free-Data Upgradeable Strategy Engine

A Python-first research platform for portfolio construction, rebalancing, backtesting, and performance analytics. Bootstraps on free data sources (Yahoo Finance, SEC EDGAR, Stooq) with a clean provider abstraction that allows seamless upgrade to paid vendors (Bloomberg, FactSet, LSEG) without rewriting portfolio logic.

## Implementation Status

**All six phases complete.** The platform delivers an end-to-end research loop:

- Domain contracts, investor profiles, and asset allocation
- Provider abstraction with free adapters (Yahoo, Stooq, EDGAR) and a reserved paid-provider namespace
- Constrained universe with liquidity filtering and benchmark proxy assignments
- Transparent equity scoring with price-derived and fundamentals-backed factors
- Benchmark-aware portfolio construction with position caps, country deviation bands, turnover limits, and minimum holdings
- Rebalance engine generating trade proposals with share counts and book-cost tracking
- Walk-forward backtesting with daily NAV, weight drift, and configurable rebalance frequency
- Performance reporting (total/annualized return, volatility, Sharpe, max drawdown, Calmar, information ratio)
- Factor attribution via returns-based style analysis (momentum, low-volatility, liquidity)
- Self-contained HTML reports with inline SVG charts (NAV, drawdown, attribution bars, weight history)
- 290 tests (unit, BDD scenarios, and upgrade-path validation), lint and format clean

## Quick Start

### Prerequisites
- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (fast Python package installer and resolver)

### Installation

```bash
git clone <repo-url>
cd alpha-holdings
uv sync --extra dev
```

### Verify installation

```bash
uv run pytest -q
```

## CLI Workflows

All commands are invoked via `uv run alpha <command>`.

### Configuration check
```bash
uv run alpha check
```

### Refresh data
```bash
uv run alpha refresh --universe tests/fixtures/seed_universe.csv
```
Fetches price history and fundamentals for the seed universe, writing raw payloads and normalized parquet snapshots to local storage.

### Inspect snapshots
```bash
uv run alpha list-snapshots
uv run alpha show-snapshot --dataset aapl_prices --as-of 2026-03-28
```

### Score equities
```bash
uv run alpha score --date 2026-03-28
```
Computes factor scores (momentum, low-volatility, liquidity, profitability, balance-sheet quality, cash-flow quality) and persists an `equity_scores` snapshot.

### Construct portfolio
```bash
uv run alpha construct --date 2026-03-28
```
Reads the latest equity scores, applies portfolio constraints from the investor profile, and produces target weights.

### Rebalance
```bash
uv run alpha rebalance --date 2026-03-28
```
Compares target weights against prior holdings, generates trade proposals with share counts and values, and persists a holdings snapshot with book-cost tracking.

### Backtest
```bash
uv run alpha backtest --start-date 2024-01-01 --end-date 2026-03-28
```
Walk-forward simulation with in-memory scoring at each rebalance date, daily NAV tracking, and benchmark comparison.

### Report
```bash
uv run alpha report --date 2026-03-28
uv run alpha report --date 2026-03-28 --html report.html
```
Computes performance metrics, factor attribution, and optionally generates a self-contained HTML report with inline SVG charts.

## Architecture

```
src/alpha_holdings/
├── domain/                   # Data contracts and business models
│   ├── models.py            # Security, PriceBar, FundamentalSnapshot, Holding, TargetWeight, etc.
│   └── investor_profile.py  # InvestorProfile, FireVariant, PortfolioConstraints
├── data/
│   ├── providers/
│   │   ├── base.py          # ABCs: PriceProvider, FundamentalsProvider, ReferenceDataProvider, FXProvider, BenchmarkProvider
│   │   ├── free/            # Yahoo Finance, Stooq, SEC EDGAR
│   │   └── paid/            # Reserved namespace for Bloomberg, FactSet, LSEG
│   ├── normalization.py     # Source-to-canonical transformations
│   ├── refresh.py           # Fetch → normalize → persist orchestration
│   └── storage.py           # LocalStorageBackend (parquet + DuckDB); StorageBackend protocol
├── universe/
│   └── builder.py           # Liquidity-filtered universe from price snapshots
├── scoring/
│   └── fundamental_model.py # 6-factor composite score with z-score normalization
├── portfolio/
│   ├── asset_allocation.py  # Profile → equity/bond/crypto sleeve weights
│   ├── construction.py      # Score-proportional weights with constraint enforcement
│   ├── rebalance.py         # Trade proposal generation and book-cost tracking
│   └── state.py             # Holdings snapshot persistence
├── backtest/
│   └── runner.py            # Walk-forward simulation engine
├── analytics/
│   ├── performance.py       # Return, risk, and benchmark-relative metrics
│   ├── attribution.py       # Returns-based factor attribution (OLS regression)
│   ├── goal.py              # Wealth probability, safe withdrawal rate
│   └── html_report.py       # Self-contained HTML with inline SVG charts
├── cli.py                   # Typer CLI entry points
└── config.py                # Environment-driven configuration
```

## Multi-Asset Architecture

- **Two-tier construction:** `AssetAllocator` derives sleeve weights (equity, bond, crypto) from `InvestorProfile`; per-sleeve security selection runs independently.
- **Bonds:** Always included as a sleeve. Weight floor rises as horizon shrinks and risk appetite decreases. Bond ETF proxies sourced via Yahoo Finance.
- **Crypto:** Opt-in satellite sleeve, enabled only when `crypto_enabled=true` and `risk_appetite >= 4`. Represented by a capped broad crypto ETF proxy, never individual coins. Max 5% (risk 4) or 10% (risk 5).

## Free-Data Limitations

1. **No point-in-time fundamentals:** SEC EDGAR filings may include restated figures. The `data_flags` field marks this explicitly (`no_point_in_time`).
2. **Adjusted prices:** Yahoo provides adjusted data; Stooq provides unadjusted only. The `data_flags` field tracks which adjustment mode was applied.
3. **Identifier drift:** Ticker symbols change across exchanges and time. External match-on-name risk exists until identifier maps are comprehensively curated.
4. **Benchmark coverage:** Public ETF proxies are used rather than licensed index constituent history.
5. **Survivorship bias:** Historical downloads may miss delisted securities without explicit curation.
6. **Global coverage:** EDGAR fundamentals are US-centric. Mixed universes rely on graceful degradation for ex-US symbols without fundamental snapshots.

## Upgrade Path: Adding a Paid Data Provider

The provider abstraction is the key upgrade seam. To add a paid vendor (e.g., Bloomberg):

1. **Create the adapter:** `src/alpha_holdings/data/providers/paid/bloomberg.py`
2. **Implement provider ABCs:** `PriceProvider`, `FundamentalsProvider`, etc. from `base.py`
3. **Declare capabilities:** Set the `capabilities` frozenset and `source_id` property
4. **Implement `resolve_ticker`:** Map canonical symbols to vendor-native tickers
5. **Run contract tests:** The existing suite in `tests/test_provider_contracts.py` validates structural compliance
6. **Run upgrade-path tests:** `tests/test_upgrade_path.py` validates end-to-end pipeline compatibility (refresh → score → construct)
7. **Update config:** Point `DATA_SOURCE` to the new adapter
8. **No changes needed** to scoring, construction, rebalancing, backtesting, or analytics code

The upgrade-path test suite proves this works by running the full pipeline with mock paid providers and verifying that:
- All downstream modules produce valid output
- Score and weight DataFrames share identical schemas across providers
- Paid fundamentals produce different rankings (proving factor integration)

## Development

### Run all tests
```bash
uv run pytest -q
```

### Run specific test layers
```bash
# Unit/function tests
uv run pytest tests/test_models.py tests/test_profiles.py tests/test_analytics.py -q

# BDD scenarios
uv run pytest tests/bdd -q

# Provider contract tests
uv run pytest tests/test_provider_contracts.py -q

# Upgrade-path validation
uv run pytest tests/test_upgrade_path.py -q
```

### Lint and format
```bash
uv run ruff check .
uv run ruff format --check .
```

### Coverage
```bash
uv run pytest --cov=src/alpha_holdings
open htmlcov/index.html
```

## Tracking

- Long-term roadmap: `PLAN.md`
- Active execution status: `STATUS.md`

## Configuration

See `.env.example` for available settings:
- `DATA_SOURCE` / `FALLBACK_SOURCE`: Provider selection
- `DATA_STORAGE_PATH` / `DATABASE_URL`: Storage paths
- `BENCHMARK_SYMBOL`: Benchmark proxy
- Portfolio constraint defaults (position caps, turnover limits, etc.)

## License

MIT
