"""Upgrade-path validation and multi-vendor contract compliance tests (Phase 6).

These tests prove that swapping a mock paid provider into the full
refresh → normalize → score → construct pipeline produces valid results.
They validate PLAN.md verification items 2 and 5:

    2. Contract tests that every provider adapter must pass for identifiers,
       prices, fundamentals, currencies, and missing-data behavior.
    5. Simulate a future vendor migration by running the same downstream
       scoring and construction tests against a mock paid adapter.

The mock paid adapters here are *full pipeline participants* — not just
structural stubs. They produce deterministic multi-bar price histories and
rich fundamental snapshots that the scoring and construction modules consume.
"""

from __future__ import annotations

import csv
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from alpha_holdings.data.providers.base import (
    FundamentalsProvider,
    PriceProvider,
    ProviderCapability,
)
from alpha_holdings.data.refresh import refresh_prices
from alpha_holdings.data.storage import LocalStorageBackend
from alpha_holdings.domain.models import DataQuality, FundamentalSnapshot, PriceBar
from alpha_holdings.portfolio.construction import construct_portfolio
from alpha_holdings.scoring.fundamental_model import score_equities_from_snapshots

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_START = date(2024, 1, 2)
_END = date(2024, 2, 15)
_SYMBOLS = ["AAPL", "MSFT", "GOOGL", "AMZN", "META"]
# as_of prefix for storage lookups — matches datetime.now() year when tests run
_AS_OF = str(datetime.now(tz=UTC).year)


# ---------------------------------------------------------------------------
# Mock paid provider implementations (pipeline-grade)
# ---------------------------------------------------------------------------


class MockPaidPriceProvider(PriceProvider):
    """Deterministic multi-bar price provider simulating a paid vendor."""

    capabilities = frozenset({ProviderCapability.PRICES})

    @property
    def source_id(self) -> str:
        return "mock_paid_price"

    def resolve_ticker(self, canonical: str, *, country: str = "") -> str:  # noqa: ARG002
        return canonical

    def get_prices(
        self,
        ticker: str,
        start: date,
        end: date,
        *,
        adjusted: bool = True,
    ) -> list[PriceBar]:
        quality = DataQuality(
            source=self.source_id,
            as_of_date=datetime.now(tz=UTC),
            data_flags=["point_in_time"] if adjusted else ["unadjusted"],
        )
        # Deterministic seed based on ticker
        seed = sum(ord(c) for c in ticker)
        bars: list[PriceBar] = []
        d = start
        idx = 0
        while d <= end:
            if d.weekday() < 5:  # skip weekends
                base = 100.0 + seed % 50 + idx * 0.1
                bars.append(
                    PriceBar(
                        security_id=ticker,
                        date=datetime(d.year, d.month, d.day, tzinfo=UTC),
                        open=Decimal(str(round(base, 2))),
                        high=Decimal(str(round(base + 2.0, 2))),
                        low=Decimal(str(round(base - 1.5, 2))),
                        close=Decimal(str(round(base + 0.5, 2))),
                        adjusted_close=Decimal(str(round(base + 0.5, 2))) if adjusted else None,
                        volume=1_000_000 + seed * 100,
                        quality=quality,
                    )
                )
                idx += 1
            d += timedelta(days=1)
        return bars


class MockPaidFundamentalsProvider(FundamentalsProvider):
    """Deterministic fundamentals provider simulating a paid vendor."""

    capabilities = frozenset({ProviderCapability.FUNDAMENTALS})

    @property
    def source_id(self) -> str:
        return "mock_paid_fundamentals"

    def get_fundamentals(
        self,
        ticker: str,
        *,
        limit: int = 8,
    ) -> list[FundamentalSnapshot]:
        quality = DataQuality(
            source=self.source_id,
            as_of_date=datetime.now(tz=UTC),
            data_flags=["point_in_time"],
        )
        seed = sum(ord(c) for c in ticker)
        snaps: list[FundamentalSnapshot] = []
        for i in range(min(limit, 4)):
            year = 2023 - i
            rev = Decimal(str(50_000_000 + seed * 1000 - i * 5_000_000))
            # Vary margin by ticker seed so z-scores are nonzero across symbols
            margin = 0.05 + (seed % 20) * 0.01
            snaps.append(
                FundamentalSnapshot(
                    security_id=ticker,
                    period_end_date=datetime(year, 12, 31, tzinfo=UTC),
                    period_type="FY",
                    revenue=rev,
                    net_income=Decimal(str(int(float(rev) * margin))),
                    operating_income=Decimal(str(int(float(rev) * (margin + 0.05)))),
                    eps=Decimal(str(round(3.0 + seed % 5, 2))),
                    book_value_per_share=Decimal(str(round(20.0 + seed % 10, 2))),
                    debt_to_equity=Decimal(str(round(0.3 + (seed % 10) * 0.1, 2))),
                    current_ratio=Decimal(str(round(1.5 + (seed % 5) * 0.1, 2))),
                    free_cash_flow=Decimal(str(int(float(rev) * (margin - 0.02)))),
                    shares_outstanding=Decimal(str(1_500_000 + seed * 100)),
                    quality=quality,
                )
            )
        return snaps


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_storage(tmp_path: Path) -> LocalStorageBackend:
    """Create a fresh local storage backend in a temp directory."""
    return LocalStorageBackend(
        root_path=tmp_path / "data",
        database_path=tmp_path / "test.duckdb",
    )


@pytest.fixture()
def mini_universe_csv(tmp_path: Path) -> Path:
    """Write a minimal seed universe CSV for the 5-symbol test set."""
    csv_path = tmp_path / "seed_universe.csv"
    with csv_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "symbol",
                "security_id",
                "isin",
                "name",
                "country",
                "currency",
                "region",
                "benchmark",
                "sector",
            ]
        )
        for sym in _SYMBOLS:
            writer.writerow(
                [
                    sym,
                    f"US_{sym}",
                    f"US0000{sym}",
                    f"{sym} Inc.",
                    "US",
                    "USD",
                    "US",
                    "SPY",
                    "Technology",
                ]
            )
    return csv_path


@pytest.fixture()
def _refreshed_with_paid_provider(
    tmp_storage: LocalStorageBackend,
    mini_universe_csv: Path,
) -> tuple[LocalStorageBackend, Path]:
    """Run refresh_prices with the mock paid provider, populating storage."""
    paid_price = MockPaidPriceProvider()
    paid_fundamentals = MockPaidFundamentalsProvider()

    # Monkey-patch the fundamentals provider used by refresh
    import alpha_holdings.data.refresh as refresh_mod

    original_default_fundamentals = refresh_mod._default_fundamentals_provider

    def _mock_fundamentals() -> FundamentalsProvider:
        return paid_fundamentals

    refresh_mod._default_fundamentals_provider = _mock_fundamentals
    try:
        summary = refresh_prices(
            universe_path=mini_universe_csv,
            start_date=_START,
            end_date=_END,
            storage=tmp_storage,
            preferred_source="mock_paid",
            providers={"mock_paid": paid_price},
        )
    finally:
        refresh_mod._default_fundamentals_provider = original_default_fundamentals

    assert summary.tickers_succeeded == len(_SYMBOLS)
    assert summary.tickers_failed == 0
    assert summary.price_snapshots_written == len(_SYMBOLS)
    assert summary.fundamentals_snapshots_written == len(_SYMBOLS)
    return tmp_storage, mini_universe_csv


# ---------------------------------------------------------------------------
# Contract compliance: multi-vendor refresh
# ---------------------------------------------------------------------------


class TestMultiVendorRefresh:
    """Verify that a mock paid provider can drive the full refresh pipeline."""

    def test_refresh_succeeds_for_all_symbols(self, _refreshed_with_paid_provider):
        storage, _ = _refreshed_with_paid_provider
        snapshots = storage.list_snapshots()
        # Should have price + fundamentals snapshots for each symbol
        datasets = {s["dataset"] for s in snapshots}
        for sym in _SYMBOLS:
            assert f"{sym.lower()}_prices" in datasets
            assert f"{sym.lower()}_fundamentals" in datasets

    def test_price_snapshots_have_expected_columns(self, _refreshed_with_paid_provider):
        storage, _ = _refreshed_with_paid_provider
        for sym in _SYMBOLS:
            df = storage.read_snapshot(dataset=f"{sym.lower()}_prices", as_of=_AS_OF)
            required_cols = {"security_id", "date", "open", "high", "low", "close", "volume"}
            assert required_cols.issubset(set(df.columns))

    def test_price_snapshots_have_positive_values(self, _refreshed_with_paid_provider):
        storage, _ = _refreshed_with_paid_provider
        df = storage.read_snapshot(dataset="aapl_prices", as_of=_AS_OF)
        assert (df["close"] > 0).all()
        assert (df["volume"] > 0).all()

    def test_fundamentals_snapshots_have_expected_columns(self, _refreshed_with_paid_provider):
        storage, _ = _refreshed_with_paid_provider
        for sym in _SYMBOLS:
            df = storage.read_snapshot(dataset=f"{sym.lower()}_fundamentals", as_of=_AS_OF)
            required_cols = {"security_id", "period_end_date", "period_type", "revenue"}
            assert required_cols.issubset(set(df.columns))

    def test_fundamentals_have_rich_field_coverage(self, _refreshed_with_paid_provider):
        """Paid fundamentals should populate fields that free adapters leave None."""
        storage, _ = _refreshed_with_paid_provider
        df = storage.read_snapshot(dataset="aapl_fundamentals", as_of=_AS_OF)
        assert df["net_income"].notna().all()
        assert df["debt_to_equity"].notna().all()
        assert df["free_cash_flow"].notna().all()

    def test_source_metadata_tracks_paid_provider(self, _refreshed_with_paid_provider):
        storage, _ = _refreshed_with_paid_provider
        snapshots = storage.list_snapshots(dataset_filter="aapl_prices")
        assert len(snapshots) >= 1
        meta = snapshots[0]["metadata"]
        assert meta["source"] == "mock_paid"


# ---------------------------------------------------------------------------
# Contract compliance: scoring with paid-provider data
# ---------------------------------------------------------------------------


class TestScoringWithPaidProvider:
    """Scoring module produces valid output from paid-provider data."""

    def test_scoring_produces_scores_for_all_symbols(self, _refreshed_with_paid_provider):
        storage, universe_csv = _refreshed_with_paid_provider
        result = score_equities_from_snapshots(
            storage=storage,
            as_of=_AS_OF,
            lookback_days=20,
            min_avg_dollar_volume=0.0,
            seed_universe_path=universe_csv,
        )
        assert result.securities_scored == len(_SYMBOLS)
        assert len(result.skipped) == 0

    def test_scored_dataframe_has_required_columns(self, _refreshed_with_paid_provider):
        storage, universe_csv = _refreshed_with_paid_provider
        result = score_equities_from_snapshots(
            storage=storage,
            as_of=_AS_OF,
            lookback_days=20,
            min_avg_dollar_volume=0.0,
            seed_universe_path=universe_csv,
        )
        required = {"symbol", "composite_score", "rank", "momentum", "volatility"}
        assert required.issubset(set(result.scores.columns))

    def test_paid_provider_fundamentals_contribute_to_scores(self, _refreshed_with_paid_provider):
        """With paid fundamentals, profitability/balance_sheet factors should be nonzero."""
        storage, universe_csv = _refreshed_with_paid_provider
        result = score_equities_from_snapshots(
            storage=storage,
            as_of=_AS_OF,
            lookback_days=20,
            min_avg_dollar_volume=0.0,
            seed_universe_path=universe_csv,
        )
        scores = result.scores
        assert scores["has_fundamentals"].all()
        # At least some symbols should have nonzero fundamental factor contributions
        assert (scores["factor_profitability"].abs() > 0).any()
        assert (scores["factor_balance_sheet_quality"].abs() > 0).any()
        assert (scores["factor_cash_flow_quality"].abs() > 0).any()

    def test_composite_scores_are_finite(self, _refreshed_with_paid_provider):
        storage, universe_csv = _refreshed_with_paid_provider
        result = score_equities_from_snapshots(
            storage=storage,
            as_of=_AS_OF,
            lookback_days=20,
            min_avg_dollar_volume=0.0,
            seed_universe_path=universe_csv,
        )
        assert result.scores["composite_score"].notna().all()
        assert (result.scores["composite_score"].abs() < 100).all()  # sanity bound

    def test_ranks_are_sequential(self, _refreshed_with_paid_provider):
        storage, universe_csv = _refreshed_with_paid_provider
        result = score_equities_from_snapshots(
            storage=storage,
            as_of=_AS_OF,
            lookback_days=20,
            min_avg_dollar_volume=0.0,
            seed_universe_path=universe_csv,
        )
        ranks = sorted(result.scores["rank"].tolist())
        assert ranks == list(range(1, len(ranks) + 1))


# ---------------------------------------------------------------------------
# Contract compliance: construction with paid-provider data
# ---------------------------------------------------------------------------


class TestConstructionWithPaidProvider:
    """Portfolio construction works correctly on paid-provider-derived scores."""

    @pytest.fixture(autouse=True)
    def _score_first(self, _refreshed_with_paid_provider):
        """Score equities so construction has scores to consume."""
        storage, universe_csv = _refreshed_with_paid_provider
        score_equities_from_snapshots(
            storage=storage,
            as_of=_AS_OF,
            lookback_days=20,
            min_avg_dollar_volume=0.0,
            seed_universe_path=universe_csv,
        )
        self.storage = storage
        self.universe_csv = universe_csv

    def test_construction_produces_weights(self):
        result = construct_portfolio(
            storage=self.storage,
            as_of=_AS_OF,
            portfolio_id="paid_test",
            seed_universe_path=self.universe_csv,
        )
        assert result.holdings_count > 0
        assert float(result.total_weight) == pytest.approx(1.0, abs=1e-4)

    def test_all_scored_symbols_receive_weight(self):
        result = construct_portfolio(
            storage=self.storage,
            as_of=_AS_OF,
            portfolio_id="paid_test",
            seed_universe_path=self.universe_csv,
        )
        weighted_symbols = set(result.weights["symbol"].tolist())
        assert weighted_symbols == set(_SYMBOLS)

    def test_no_weight_exceeds_position_cap(self):
        result = construct_portfolio(
            storage=self.storage,
            as_of=_AS_OF,
            portfolio_id="paid_test",
            seed_universe_path=self.universe_csv,
        )
        max_weight = float(result.max_weight)
        # Default constraints have a position cap; verify it is respected
        assert max_weight <= 0.35 + 1e-4  # generous bound covering all profiles

    def test_construction_result_persists_snapshot(self):
        result = construct_portfolio(
            storage=self.storage,
            as_of=_AS_OF,
            portfolio_id="paid_test",
            seed_universe_path=self.universe_csv,
        )
        assert result.snapshot_path.exists()
        df = pd.read_parquet(result.snapshot_path)
        assert len(df) == result.holdings_count

    def test_weights_dataframe_has_required_columns(self):
        result = construct_portfolio(
            storage=self.storage,
            as_of=_AS_OF,
            portfolio_id="paid_test",
            seed_universe_path=self.universe_csv,
        )
        required = {"portfolio_id", "symbol", "target_weight", "composite_score", "rank", "country"}
        assert required.issubset(set(result.weights.columns))


# ---------------------------------------------------------------------------
# Normalization invariants: free vs. paid
# ---------------------------------------------------------------------------


class TestNormalizationInvariants:
    """Outputs from different providers share the same schema after normalization.

    This validates that the refresh pipeline's normalization layer produces
    schema-compatible outputs regardless of provider, so downstream modules
    work unchanged after a vendor swap.
    """

    def test_price_snapshot_schema_from_paid_matches_free(
        self,
        tmp_storage,
        mini_universe_csv,  # noqa: ARG002
    ):
        """A paid-provider price snapshot has the same columns as free-provider data."""
        # Write one snapshot via mock paid provider
        paid = MockPaidPriceProvider()
        bars = paid.get_prices("TEST", _START, _END)
        from alpha_holdings.data.refresh import _price_bar_to_snapshot_row

        rows = [_price_bar_to_snapshot_row(b) for b in bars]
        from datetime import datetime as dt

        now = dt.now(tz=UTC)
        path = tmp_storage.write_normalized_snapshot(dataset="test_prices", as_of=now, rows=rows)
        df = pd.read_parquet(path)
        # These columns must be present for scoring to work
        required = {"security_id", "date", "open", "high", "low", "close", "volume", "source"}
        assert required.issubset(set(df.columns))

    def test_fundamentals_snapshot_schema_from_paid_matches_free(self, tmp_storage):
        """A paid-provider fundamentals snapshot has the same columns as free data."""
        paid = MockPaidFundamentalsProvider()
        snaps = paid.get_fundamentals("TEST")
        from alpha_holdings.data.refresh import _fundamental_snapshot_to_row

        rows = [_fundamental_snapshot_to_row(s) for s in snaps]
        now = datetime.now(tz=UTC)
        path = tmp_storage.write_normalized_snapshot(
            dataset="test_fundamentals", as_of=now, rows=rows
        )
        df = pd.read_parquet(path)
        required = {
            "security_id",
            "period_end_date",
            "period_type",
            "revenue",
            "net_income",
            "debt_to_equity",
            "free_cash_flow",
            "source",
        }
        assert required.issubset(set(df.columns))


# ---------------------------------------------------------------------------
# Provider swap: free → paid produces comparable but richer results
# ---------------------------------------------------------------------------


class TestProviderSwapComparison:
    """Run the pipeline with free stubs, then paid stubs, and compare.

    This tests PLAN verification item 5: 'Simulate a future vendor migration
    by running the same downstream scoring and construction tests against a
    mock paid adapter with the same contracts.'
    """

    @staticmethod
    def _run_pipeline(
        tmp_path: Path,
        price_provider: PriceProvider,
        fundamentals_provider: FundamentalsProvider,
        label: str,
    ) -> tuple[LocalStorageBackend, pd.DataFrame, pd.DataFrame]:
        """Run refresh → score → construct and return storage + results."""
        storage = LocalStorageBackend(
            root_path=tmp_path / f"data_{label}",
            database_path=tmp_path / f"{label}.duckdb",
        )
        csv_path = tmp_path / f"universe_{label}.csv"
        with csv_path.open("w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(
                [
                    "symbol",
                    "security_id",
                    "isin",
                    "name",
                    "country",
                    "currency",
                    "region",
                    "benchmark",
                    "sector",
                ]
            )
            for sym in _SYMBOLS:
                writer.writerow(
                    [
                        sym,
                        f"US_{sym}",
                        f"US0000{sym}",
                        f"{sym} Inc.",
                        "US",
                        "USD",
                        "US",
                        "SPY",
                        "Technology",
                    ]
                )

        import alpha_holdings.data.refresh as refresh_mod

        original = refresh_mod._default_fundamentals_provider

        def _inject() -> FundamentalsProvider:
            return fundamentals_provider

        refresh_mod._default_fundamentals_provider = _inject
        try:
            refresh_prices(
                universe_path=csv_path,
                start_date=_START,
                end_date=_END,
                storage=storage,
                preferred_source=label,
                providers={label: price_provider},
            )
        finally:
            refresh_mod._default_fundamentals_provider = original

        scores = score_equities_from_snapshots(
            storage=storage,
            as_of=_AS_OF,
            lookback_days=20,
            min_avg_dollar_volume=0.0,
            seed_universe_path=csv_path,
        )
        construction = construct_portfolio(
            storage=storage,
            as_of=_AS_OF,
            portfolio_id=f"{label}_test",
            seed_universe_path=csv_path,
        )
        return storage, scores.scores, construction.weights

    def test_both_pipelines_produce_valid_scores(self, tmp_path):
        """Free and paid providers both produce scoreable, constructable output."""
        # Free-like stubs (only price, no fundamentals)
        _, free_scores, free_weights = self._run_pipeline(
            tmp_path,
            MockPaidPriceProvider(),
            _NoOpFundamentals(),
            label="free_stub",
        )
        # Paid stubs (price + rich fundamentals)
        _, paid_scores, paid_weights = self._run_pipeline(
            tmp_path,
            MockPaidPriceProvider(),
            MockPaidFundamentalsProvider(),
            label="paid_stub",
        )

        # Both must produce scores for all symbols
        assert set(free_scores["symbol"]) == set(_SYMBOLS)
        assert set(paid_scores["symbol"]) == set(_SYMBOLS)

        # Both must produce valid weights
        assert set(free_weights["symbol"]) == set(_SYMBOLS)
        assert set(paid_weights["symbol"]) == set(_SYMBOLS)

        # Weights sum to 1.0 in both cases
        assert free_weights["target_weight"].sum() == pytest.approx(1.0, abs=1e-4)
        assert paid_weights["target_weight"].sum() == pytest.approx(1.0, abs=1e-4)

    def test_paid_fundamentals_change_rankings(self, tmp_path):
        """Adding paid fundamentals changes score rankings (proves factors work)."""
        _, free_scores, _ = self._run_pipeline(
            tmp_path,
            MockPaidPriceProvider(),
            _NoOpFundamentals(),
            label="free_rank",
        )
        _, paid_scores, _ = self._run_pipeline(
            tmp_path,
            MockPaidPriceProvider(),
            MockPaidFundamentalsProvider(),
            label="paid_rank",
        )

        free_ranking = free_scores.sort_values("rank")["symbol"].tolist()
        paid_ranking = paid_scores.sort_values("rank")["symbol"].tolist()

        # Rankings should differ because paid fundamentals contribute new factors
        assert free_ranking != paid_ranking

    def test_score_schema_identical_across_providers(self, tmp_path):
        """Both free and paid pipelines produce DataFrames with identical columns."""
        _, free_scores, _ = self._run_pipeline(
            tmp_path,
            MockPaidPriceProvider(),
            _NoOpFundamentals(),
            label="free_schema",
        )
        _, paid_scores, _ = self._run_pipeline(
            tmp_path,
            MockPaidPriceProvider(),
            MockPaidFundamentalsProvider(),
            label="paid_schema",
        )

        assert set(free_scores.columns) == set(paid_scores.columns)

    def test_weight_schema_identical_across_providers(self, tmp_path):
        """Both free and paid pipelines produce portfolio weights with identical columns."""
        _, _, free_weights = self._run_pipeline(
            tmp_path,
            MockPaidPriceProvider(),
            _NoOpFundamentals(),
            label="free_wgt",
        )
        _, _, paid_weights = self._run_pipeline(
            tmp_path,
            MockPaidPriceProvider(),
            MockPaidFundamentalsProvider(),
            label="paid_wgt",
        )

        assert set(free_weights.columns) == set(paid_weights.columns)


# ---------------------------------------------------------------------------
# Helper: no-op fundamentals provider (simulates free data without EDGAR)
# ---------------------------------------------------------------------------


class _NoOpFundamentals(FundamentalsProvider):
    """Provider that always fails — simulates missing fundamentals coverage."""

    capabilities = frozenset({ProviderCapability.FUNDAMENTALS})

    @property
    def source_id(self) -> str:
        return "noop"

    def get_fundamentals(self, ticker: str, *, limit: int = 8) -> list[FundamentalSnapshot]:  # noqa: ARG002
        raise RuntimeError(f"No fundamentals for {ticker}")
