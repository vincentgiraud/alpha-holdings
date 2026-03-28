# Changelog

All notable changes to this project will be documented in this file.

## [0.1.0] - 2026-03-28

### Added

- Complete free-data, provider-agnostic portfolio research engine with canonical domain models and contract-first provider interfaces.
- Investor profile system with FIRE variants, risk-to-constraints resolver, and top-level asset allocator for equity, bond, and optional crypto sleeves.
- End-to-end CLI workflows for refresh, normalization, scoring, construction, rebalance, backtest, and reporting.
- Local storage backend seam using DuckDB metadata plus parquet snapshots and raw/normalized dataset separation.
- Upgrade-path test coverage for provider parity, contract compliance, normalization invariants, and mock paid-provider swap behavior.

### Notes

- This release is focused on research workflows and paper portfolio operations.
- Free-data limitations and paid-provider upgrade process are documented in README.md.
