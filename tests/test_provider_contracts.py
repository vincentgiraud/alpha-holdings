"""Provider contract tests.

These tests enforce the invariants that every adapter — free or future paid —
must satisfy.  They work against *stub* implementations rather than real
network calls so they run offline and deterministically.

Three kinds of checks:
1. **Structural** — adapters expose the right ABCs, ``source_id``, and
   ``capabilities``.
2. **Output schema** — returned objects are valid Pydantic models with all
   mandatory fields populated.
3. **Capability gating** — requesting a capability the provider does not
   support raises ``ProviderCapabilityError`` instead of silently degrading.

When you add a new adapter, subclass the ``_AdapterContractBase`` mix-in
below and parametrize it with your adapter instance.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from alpha_holdings.data.providers.base import (
    BaseProvider,
    BenchmarkProvider,
    FundamentalsProvider,
    FXProvider,
    PriceProvider,
    ProviderCapability,
    ProviderCapabilityError,
    ReferenceDataProvider,
)
from alpha_holdings.domain.models import (
    BenchmarkConstituent,
    CorporateAction,
    DataQuality,
    FundamentalSnapshot,
    IdentifierMap,
    PriceBar,
    Security,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TODAY = date(2024, 1, 15)
_START = date(2024, 1, 2)
_END = date(2024, 1, 5)

_QUALITY = DataQuality(source="stub", as_of_date=datetime.now(tz=UTC))


def _bar(security_id: str = "STUB", d: date = _START) -> PriceBar:
    return PriceBar(
        security_id=security_id,
        date=datetime(d.year, d.month, d.day, tzinfo=UTC),
        open=Decimal("100.00"),
        high=Decimal("101.00"),
        low=Decimal("99.50"),
        close=Decimal("100.50"),
        volume=100_000,
        quality=_QUALITY,
    )


def _fundamental(security_id: str = "STUB") -> FundamentalSnapshot:
    return FundamentalSnapshot(
        security_id=security_id,
        period_end_date=datetime(2023, 12, 31, tzinfo=UTC),
        period_type="FY",
        revenue=Decimal("1_000_000"),
        quality=_QUALITY,
    )


def _security(ticker: str = "STUB") -> Security:
    return Security(
        internal_id=ticker,
        ticker=ticker,
        name="Stub Inc.",
        security_type="equity",
        exchange="STUB",
        country="US",
        quality=_QUALITY,
    )


def _constituent(benchmark_id: str = "SPY") -> BenchmarkConstituent:
    return BenchmarkConstituent(
        benchmark_id=benchmark_id,
        security_id="STUB",
        effective_date=datetime(_TODAY.year, _TODAY.month, _TODAY.day, tzinfo=UTC),
        weight=Decimal("0.01"),
        quality=_QUALITY,
    )


# ---------------------------------------------------------------------------
# Minimal stub implementations (all-in-memory, no I/O)
# ---------------------------------------------------------------------------


class StubPriceProvider(PriceProvider):
    capabilities = frozenset({ProviderCapability.PRICES, ProviderCapability.CORPORATE_ACTIONS})

    @property
    def source_id(self) -> str:
        return "stub_price"

    def _quality(self) -> DataQuality:
        return DataQuality(source=self.source_id, as_of_date=datetime.now(tz=UTC))

    def get_prices(self, ticker, start, end, *, adjusted=True):
        _ = (end, adjusted)
        bar = _bar(ticker, start)
        return [bar.model_copy(update={"quality": self._quality()})]

    def get_corporate_actions(self, ticker, start, end):
        _ = end
        return [
            CorporateAction(
                security_id=ticker,
                action_date=datetime(start.year, start.month, start.day, tzinfo=UTC),
                action_type="dividend",
                value=Decimal("0.50"),
                quality=self._quality(),
            )
        ]


class StubFundamentalsProvider(FundamentalsProvider):
    capabilities = frozenset({ProviderCapability.FUNDAMENTALS})

    @property
    def source_id(self) -> str:
        return "stub_fundamentals"

    def _quality(self) -> DataQuality:
        return DataQuality(source=self.source_id, as_of_date=datetime.now(tz=UTC))

    def get_fundamentals(self, ticker, *, limit=8):
        snap = _fundamental(ticker)
        stamped = snap.model_copy(update={"quality": self._quality()})
        return [stamped] * min(limit, 2)


class StubReferenceDataProvider(ReferenceDataProvider):
    capabilities = frozenset({ProviderCapability.REFERENCE_DATA})

    @property
    def source_id(self) -> str:
        return "stub_reference"

    def _quality(self) -> DataQuality:
        return DataQuality(source=self.source_id, as_of_date=datetime.now(tz=UTC))

    def get_security(self, ticker):
        sec = _security(ticker)
        return sec.model_copy(update={"quality": self._quality()})


class StubFXProvider(FXProvider):
    capabilities = frozenset({ProviderCapability.FX})

    @property
    def source_id(self) -> str:
        return "stub_fx"

    def get_fx_rate(self, base, quote, as_of):
        _ = (base, quote, as_of)
        return 1.08  # arbitrary EUR/USD proxy


class StubBenchmarkProvider(BenchmarkProvider):
    capabilities = frozenset({ProviderCapability.BENCHMARK_CONSTITUENTS})

    @property
    def source_id(self) -> str:
        return "stub_benchmark"

    def get_constituents(self, benchmark_id, as_of):
        _ = as_of
        return [_constituent(benchmark_id)]


# ---------------------------------------------------------------------------
# Contract: source_id and capabilities
# ---------------------------------------------------------------------------


class TestProviderIdentity:
    """Every provider must expose a non-empty source_id and a capabilities set."""

    @pytest.mark.parametrize(
        "provider",
        [
            StubPriceProvider(),
            StubFundamentalsProvider(),
            StubReferenceDataProvider(),
            StubFXProvider(),
            StubBenchmarkProvider(),
        ],
    )
    def test_source_id_is_non_empty_string(self, provider: BaseProvider):
        assert isinstance(provider.source_id, str)
        assert len(provider.source_id) > 0

    @pytest.mark.parametrize(
        "provider",
        [
            StubPriceProvider(),
            StubFundamentalsProvider(),
            StubReferenceDataProvider(),
            StubFXProvider(),
            StubBenchmarkProvider(),
        ],
    )
    def test_capabilities_is_non_empty_frozenset(self, provider: BaseProvider):
        assert isinstance(provider.capabilities, frozenset)
        assert len(provider.capabilities) > 0

    @pytest.mark.parametrize(
        "provider",
        [
            StubPriceProvider(),
            StubFundamentalsProvider(),
            StubReferenceDataProvider(),
            StubFXProvider(),
            StubBenchmarkProvider(),
        ],
    )
    def test_capabilities_elements_are_provider_capability_enum(self, provider: BaseProvider):
        for cap in provider.capabilities:
            assert isinstance(cap, ProviderCapability)


# ---------------------------------------------------------------------------
# Contract: PriceProvider output schema
# ---------------------------------------------------------------------------


class TestPriceProviderContract:
    def setup_method(self):
        self.provider = StubPriceProvider()

    def test_get_prices_returns_list(self):
        bars = self.provider.get_prices("STUB", _START, _END)
        assert isinstance(bars, list)

    def test_get_prices_returns_price_bar_instances(self):
        bars = self.provider.get_prices("STUB", _START, _END)
        for bar in bars:
            assert isinstance(bar, PriceBar)

    def test_price_bar_mandatory_fields_present(self):
        bar = self.provider.get_prices("STUB", _START, _END)[0]
        assert bar.security_id
        assert bar.close > Decimal(0)
        assert bar.volume >= 0
        assert bar.quality.source == self.provider.source_id

    def test_price_bar_quality_source_matches_provider(self):
        bars = self.provider.get_prices("STUB", _START, _END)
        for bar in bars:
            assert bar.quality.source == self.provider.source_id

    def test_get_corporate_actions_returns_list(self):
        actions = self.provider.get_corporate_actions("STUB", _START, _END)
        assert isinstance(actions, list)

    def test_corporate_action_instances(self):
        actions = self.provider.get_corporate_actions("STUB", _START, _END)
        for action in actions:
            assert isinstance(action, CorporateAction)

    def test_corporate_action_type_is_known_value(self):
        actions = self.provider.get_corporate_actions("STUB", _START, _END)
        allowed = {"dividend", "split", "merger", "spinoff", "other"}
        for action in actions:
            assert action.action_type in allowed


# ---------------------------------------------------------------------------
# Contract: FundamentalsProvider output schema
# ---------------------------------------------------------------------------


class TestFundamentalsProviderContract:
    def setup_method(self):
        self.provider = StubFundamentalsProvider()

    def test_get_fundamentals_returns_list(self):
        snaps = self.provider.get_fundamentals("STUB")
        assert isinstance(snaps, list)

    def test_returns_at_most_limit_snapshots(self):
        snaps = self.provider.get_fundamentals("STUB", limit=1)
        assert len(snaps) <= 1

    def test_fundamental_snapshot_instances(self):
        snaps = self.provider.get_fundamentals("STUB")
        for snap in snaps:
            assert isinstance(snap, FundamentalSnapshot)

    def test_period_type_is_valid(self):
        snaps = self.provider.get_fundamentals("STUB")
        allowed = {"FY", "Q1", "Q2", "Q3", "Q4", "Q?"}
        for snap in snaps:
            assert snap.period_type in allowed

    def test_missing_fields_are_none_not_zero(self):
        # Providers must not substitute 0 for unavailable numeric fields.
        # This stub always sets revenue; it must leave other fields as None.
        snap = self.provider.get_fundamentals("STUB")[0]
        # debt_to_equity is not set in the stub → must be None, not 0
        assert snap.debt_to_equity is None

    def test_quality_source_matches_provider(self):
        snaps = self.provider.get_fundamentals("STUB")
        for snap in snaps:
            assert snap.quality.source == self.provider.source_id


# ---------------------------------------------------------------------------
# Contract: ReferenceDataProvider output schema
# ---------------------------------------------------------------------------


class TestReferenceDataProviderContract:
    def setup_method(self):
        self.provider = StubReferenceDataProvider()

    def test_get_security_returns_security(self):
        sec = self.provider.get_security("STUB")
        assert isinstance(sec, Security)

    def test_security_has_non_empty_ticker(self):
        sec = self.provider.get_security("STUB")
        assert sec.ticker

    def test_security_has_non_empty_name(self):
        sec = self.provider.get_security("STUB")
        assert sec.name

    def test_security_country_is_iso_alpha2(self):
        sec = self.provider.get_security("STUB")
        assert len(sec.country) == 2

    def test_security_currency_is_iso_4217(self):
        sec = self.provider.get_security("STUB")
        assert len(sec.currency) == 3

    def test_quality_source_matches_provider(self):
        sec = self.provider.get_security("STUB")
        assert sec.quality.source == self.provider.source_id


# ---------------------------------------------------------------------------
# Contract: FXProvider output schema
# ---------------------------------------------------------------------------


class TestFXProviderContract:
    def setup_method(self):
        self.provider = StubFXProvider()

    def test_get_fx_rate_returns_positive_float(self):
        rate = self.provider.get_fx_rate("USD", "EUR", _TODAY)
        assert isinstance(rate, float)
        assert rate > 0


# ---------------------------------------------------------------------------
# Contract: BenchmarkProvider output schema
# ---------------------------------------------------------------------------


class TestBenchmarkProviderContract:
    def setup_method(self):
        self.provider = StubBenchmarkProvider()

    def test_get_constituents_returns_list(self):
        constituents = self.provider.get_constituents("SPY", _TODAY)
        assert isinstance(constituents, list)

    def test_constituent_instances(self):
        constituents = self.provider.get_constituents("SPY", _TODAY)
        for c in constituents:
            assert isinstance(c, BenchmarkConstituent)

    def test_weights_sum_at_most_one(self):
        constituents = self.provider.get_constituents("SPY", _TODAY)
        total = sum(c.weight for c in constituents)
        assert total <= Decimal("1.0001"), f"Weights sum to {total}"

    def test_individual_weight_is_positive(self):
        constituents = self.provider.get_constituents("SPY", _TODAY)
        for c in constituents:
            assert c.weight > 0


# ---------------------------------------------------------------------------
# Contract: capability gating
# ---------------------------------------------------------------------------


class TestCapabilityGating:
    """Providers that lack a capability must raise ProviderCapabilityError."""

    def test_price_provider_without_corporate_actions_raises(self):
        class NoCorporateActions(PriceProvider):
            capabilities = frozenset({ProviderCapability.PRICES})

            @property
            def source_id(self):
                return "no_ca"

            def get_prices(self, ticker, start, end, *, adjusted=True):
                _ = (ticker, start, end, adjusted)
                return []

        provider = NoCorporateActions()
        with pytest.raises(ProviderCapabilityError):
            provider.get_corporate_actions("STUB", _START, _END)

    def test_reference_provider_without_identifier_map_raises(self):
        class NoIdMap(ReferenceDataProvider):
            capabilities = frozenset({ProviderCapability.REFERENCE_DATA})

            @property
            def source_id(self):
                return "no_idmap"

            def get_security(self, ticker):
                return _security(ticker)

        provider = NoIdMap()
        with pytest.raises((ProviderCapabilityError, NotImplementedError)):
            provider.get_identifier_map("STUB")


# ---------------------------------------------------------------------------
# Contract: resolve_ticker
# ---------------------------------------------------------------------------


class TestResolveTickerContract:
    """The base resolve_ticker is a passthrough; Yahoo overrides with mapping."""

    def test_base_provider_returns_canonical(self):
        provider = StubPriceProvider()
        assert provider.resolve_ticker("AAPL") == "AAPL"
        assert provider.resolve_ticker("BRK.B", country="US") == "BRK.B"

    def test_yahoo_share_class_dot_to_hyphen(self):
        from alpha_holdings.data.providers.free.yahoo import YahooPriceProvider

        yahoo = YahooPriceProvider()
        assert yahoo.resolve_ticker("BRK.B", country="US") == "BRK-B"
        assert yahoo.resolve_ticker("BF.B", country="US") == "BF-B"

    def test_yahoo_swiss_suffix(self):
        from alpha_holdings.data.providers.free.yahoo import YahooPriceProvider

        yahoo = YahooPriceProvider()
        assert yahoo.resolve_ticker("NOVN", country="CH") == "NOVN.SW"
        assert yahoo.resolve_ticker("NESN", country="CH") == "NESN.SW"

    def test_yahoo_us_no_suffix(self):
        from alpha_holdings.data.providers.free.yahoo import YahooPriceProvider

        yahoo = YahooPriceProvider()
        assert yahoo.resolve_ticker("AAPL", country="US") == "AAPL"
        assert yahoo.resolve_ticker("MSFT", country="") == "MSFT"

    def test_yahoo_other_exchange_suffixes(self):
        from alpha_holdings.data.providers.free.yahoo import YahooPriceProvider

        yahoo = YahooPriceProvider()
        assert yahoo.resolve_ticker("SAP", country="DE") == "SAP.DE"
        assert yahoo.resolve_ticker("SHEL", country="GB") == "SHEL.L"
        assert yahoo.resolve_ticker("RY", country="CA") == "RY.TO"

    def test_yahoo_japan_no_suffix(self):
        from alpha_holdings.data.providers.free.yahoo import YahooPriceProvider

        yahoo = YahooPriceProvider()
        # JP excluded: Yahoo uses numeric codes for Tokyo (e.g. 6758.T not SONY.T)
        assert yahoo.resolve_ticker("SONY", country="JP") == "SONY"


# ---------------------------------------------------------------------------
# Contract: free adapters declare correct capabilities (structural check only)
# ---------------------------------------------------------------------------


class TestFreeAdapterCapabilities:
    """Verify the free adapters have the right capability declarations.

    These tests do not make network calls.
    """

    def test_yahoo_declares_prices_and_reference_data(self):
        from alpha_holdings.data.providers.free.yahoo import YahooPriceProvider

        p = YahooPriceProvider()
        assert ProviderCapability.PRICES in p.capabilities
        assert ProviderCapability.CORPORATE_ACTIONS in p.capabilities
        assert ProviderCapability.REFERENCE_DATA in p.capabilities

    def test_stooq_declares_only_prices(self):
        from alpha_holdings.data.providers.free.stooq import StooqPriceProvider

        p = StooqPriceProvider()
        assert ProviderCapability.PRICES in p.capabilities
        assert ProviderCapability.FUNDAMENTALS not in p.capabilities
        assert ProviderCapability.REFERENCE_DATA not in p.capabilities

    def test_edgar_declares_only_fundamentals(self):
        from alpha_holdings.data.providers.free.edgar import EdgarFundamentalsProvider

        p = EdgarFundamentalsProvider()
        assert ProviderCapability.FUNDAMENTALS in p.capabilities
        assert ProviderCapability.PRICES not in p.capabilities

    def test_yahoo_source_id(self):
        from alpha_holdings.data.providers.free.yahoo import YahooPriceProvider

        assert YahooPriceProvider().source_id == "yahoo"

    def test_stooq_source_id(self):
        from alpha_holdings.data.providers.free.stooq import StooqPriceProvider

        assert StooqPriceProvider().source_id == "stooq"

    def test_edgar_source_id(self):
        from alpha_holdings.data.providers.free.edgar import EdgarFundamentalsProvider

        assert EdgarFundamentalsProvider().source_id == "edgar"


# ===================================================================
# Mock paid adapter implementations (simulating Bloomberg / FactSet)
# ===================================================================
#
# These exercise the same ABCs as the free stubs but with richer
# capabilities and fuller data surface, validating that a future paid
# vendor can be introduced as an additive adapter-level change.
# ===================================================================


class MockPaidPriceProvider(PriceProvider):
    """Simulates a paid price provider with full capabilities."""

    capabilities = frozenset({ProviderCapability.PRICES, ProviderCapability.CORPORATE_ACTIONS})

    @property
    def source_id(self) -> str:
        return "mock_bloomberg"

    def _quality(self) -> DataQuality:
        return DataQuality(source=self.source_id, as_of_date=datetime.now(tz=UTC))

    def get_prices(self, ticker, start, end, *, adjusted=True):  # noqa: ARG002
        """Return multiple bars spanning the requested range."""
        from datetime import timedelta

        bars = []
        d = start
        while d <= end:
            bars.append(
                PriceBar(
                    security_id=ticker,
                    date=datetime(d.year, d.month, d.day, tzinfo=UTC),
                    open=Decimal("150.00"),
                    high=Decimal("152.25"),
                    low=Decimal("149.10"),
                    close=Decimal("151.80"),
                    volume=2_500_000,
                    quality=self._quality(),
                )
            )
            d += timedelta(days=1)
        return bars

    def get_corporate_actions(self, ticker, start, end):
        return [
            CorporateAction(
                security_id=ticker,
                action_date=datetime(start.year, start.month, start.day, tzinfo=UTC),
                action_type="dividend",
                value=Decimal("0.82"),
                quality=self._quality(),
            ),
            CorporateAction(
                security_id=ticker,
                action_date=datetime(end.year, end.month, end.day, tzinfo=UTC),
                action_type="split",
                value=Decimal("4.0"),
                quality=self._quality(),
            ),
        ]

    def resolve_ticker(self, canonical: str, *, country: str = "") -> str:  # noqa: ARG002
        """Paid providers typically use their own symbology prefix."""
        return f"BBG:{canonical}"


class MockPaidFundamentalsProvider(FundamentalsProvider):
    """Simulates a paid fundamentals provider with richer field coverage."""

    capabilities = frozenset({ProviderCapability.FUNDAMENTALS})

    @property
    def source_id(self) -> str:
        return "mock_factset"

    def _quality(self) -> DataQuality:
        return DataQuality(source=self.source_id, as_of_date=datetime.now(tz=UTC))

    def get_fundamentals(self, ticker, *, limit=8):
        snap = FundamentalSnapshot(
            security_id=ticker,
            period_end_date=datetime(2023, 12, 31, tzinfo=UTC),
            period_type="FY",
            revenue=Decimal("50_000_000"),
            net_income=Decimal("8_000_000"),
            operating_income=Decimal("10_000_000"),
            eps=Decimal("4.50"),
            book_value_per_share=Decimal("25.00"),
            debt_to_equity=Decimal("0.60"),
            current_ratio=Decimal("1.80"),
            free_cash_flow=Decimal("12_000_000"),
            shares_outstanding=Decimal("1_800_000"),
            quality=self._quality(),
        )
        return [snap] * min(limit, 4)


class MockPaidReferenceDataProvider(ReferenceDataProvider):
    """Simulates a paid reference data provider with identifier mapping."""

    capabilities = frozenset(
        {ProviderCapability.REFERENCE_DATA, ProviderCapability.CLASSIFICATIONS}
    )

    @property
    def source_id(self) -> str:
        return "mock_lseg"

    def _quality(self) -> DataQuality:
        return DataQuality(source=self.source_id, as_of_date=datetime.now(tz=UTC))

    def get_security(self, ticker):
        return Security(
            internal_id=f"LSEG-{ticker}",
            ticker=ticker,
            name=f"{ticker} Corp (LSEG)",
            security_type="equity",
            exchange="XNYS",
            country="US",
            currency="USD",
            isin=f"US0000000{ticker[:3].upper()}",
            quality=self._quality(),
        )

    def get_identifier_map(self, ticker):
        return IdentifierMap(
            internal_id=f"LSEG-{ticker}",
            ticker_map={"lseg": ticker, "bloomberg": f"{ticker} US Equity"},
            isin=f"US0000000{ticker[:3].upper()}",
            quality=self._quality(),
        )


class MockPaidFXProvider(FXProvider):
    """Simulates a paid FX provider."""

    capabilities = frozenset({ProviderCapability.FX})

    @property
    def source_id(self) -> str:
        return "mock_bloomberg_fx"

    def get_fx_rate(self, base, quote, as_of):  # noqa: ARG002
        rates = {("USD", "EUR"): 0.92, ("EUR", "USD"): 1.09, ("GBP", "USD"): 1.27}
        return rates.get((base, quote), 1.0)


class MockPaidBenchmarkProvider(BenchmarkProvider):
    """Simulates a paid benchmark provider with multi-constituent output."""

    capabilities = frozenset({ProviderCapability.BENCHMARK_CONSTITUENTS})

    @property
    def source_id(self) -> str:
        return "mock_bloomberg_idx"

    def _quality(self) -> DataQuality:
        return DataQuality(source=self.source_id, as_of_date=datetime.now(tz=UTC))

    def get_constituents(self, benchmark_id, as_of):
        constituents = []
        for i, ticker in enumerate(["AAPL", "MSFT", "GOOGL"]):
            constituents.append(
                BenchmarkConstituent(
                    benchmark_id=benchmark_id,
                    security_id=ticker,
                    effective_date=datetime(as_of.year, as_of.month, as_of.day, tzinfo=UTC),
                    weight=Decimal(str(round(0.20 - i * 0.05, 2))),
                    quality=self._quality(),
                )
            )
        return constituents


# ---------------------------------------------------------------------------
# Mock paid composite provider (multi-interface, simulates all-in-one vendor)
# ---------------------------------------------------------------------------


class MockPaidCompositeProvider(PriceProvider, FundamentalsProvider, ReferenceDataProvider):
    """Simulates a vendor like Bloomberg that bundles multiple data types."""

    capabilities = frozenset(
        {
            ProviderCapability.PRICES,
            ProviderCapability.CORPORATE_ACTIONS,
            ProviderCapability.FUNDAMENTALS,
            ProviderCapability.REFERENCE_DATA,
        }
    )

    @property
    def source_id(self) -> str:
        return "mock_composite"

    def _quality(self) -> DataQuality:
        return DataQuality(source=self.source_id, as_of_date=datetime.now(tz=UTC))

    def get_prices(self, ticker, start, end, *, adjusted=True):  # noqa: ARG002
        return [
            PriceBar(
                security_id=ticker,
                date=datetime(start.year, start.month, start.day, tzinfo=UTC),
                open=Decimal("200.00"),
                high=Decimal("205.00"),
                low=Decimal("198.00"),
                close=Decimal("203.50"),
                volume=5_000_000,
                quality=self._quality(),
            )
        ]

    def get_corporate_actions(self, ticker, start, end):  # noqa: ARG002
        return [
            CorporateAction(
                security_id=ticker,
                action_date=datetime(start.year, start.month, start.day, tzinfo=UTC),
                action_type="dividend",
                value=Decimal("1.00"),
                quality=self._quality(),
            )
        ]

    def get_fundamentals(self, ticker, *, limit=8):
        snap = FundamentalSnapshot(
            security_id=ticker,
            period_end_date=datetime(2023, 12, 31, tzinfo=UTC),
            period_type="FY",
            revenue=Decimal("100_000_000"),
            quality=self._quality(),
        )
        return [snap] * min(limit, 2)

    def get_security(self, ticker):
        return Security(
            internal_id=f"COMP-{ticker}",
            ticker=ticker,
            name=f"{ticker} Corp (Composite)",
            security_type="equity",
            exchange="XNAS",
            country="US",
            currency="USD",
            quality=self._quality(),
        )


# ---------------------------------------------------------------------------
# Contract tests: mock paid adapters (structural / identity)
# ---------------------------------------------------------------------------

_MOCK_PAID_PROVIDERS = [
    MockPaidPriceProvider(),
    MockPaidFundamentalsProvider(),
    MockPaidReferenceDataProvider(),
    MockPaidFXProvider(),
    MockPaidBenchmarkProvider(),
    MockPaidCompositeProvider(),
]


class TestMockPaidProviderIdentity:
    """Mock paid adapters satisfy the same identity contracts as free stubs."""

    @pytest.mark.parametrize("provider", _MOCK_PAID_PROVIDERS)
    def test_source_id_is_non_empty_string(self, provider: BaseProvider):
        assert isinstance(provider.source_id, str)
        assert len(provider.source_id) > 0

    @pytest.mark.parametrize("provider", _MOCK_PAID_PROVIDERS)
    def test_capabilities_is_non_empty_frozenset(self, provider: BaseProvider):
        assert isinstance(provider.capabilities, frozenset)
        assert len(provider.capabilities) > 0

    @pytest.mark.parametrize("provider", _MOCK_PAID_PROVIDERS)
    def test_capabilities_contain_valid_enums(self, provider: BaseProvider):
        for cap in provider.capabilities:
            assert isinstance(cap, ProviderCapability)

    @pytest.mark.parametrize("provider", _MOCK_PAID_PROVIDERS)
    def test_source_ids_are_distinct_from_free_stubs(self, provider: BaseProvider):
        free_ids = {
            "stub_price",
            "stub_fundamentals",
            "stub_reference",
            "stub_fx",
            "stub_benchmark",
        }
        assert provider.source_id not in free_ids


# ---------------------------------------------------------------------------
# Contract tests: mock paid PriceProvider
# ---------------------------------------------------------------------------


class TestMockPaidPriceContract:
    def setup_method(self):
        self.provider = MockPaidPriceProvider()

    def test_get_prices_returns_list_of_price_bars(self):
        bars = self.provider.get_prices("AAPL", _START, _END)
        assert isinstance(bars, list)
        assert all(isinstance(b, PriceBar) for b in bars)

    def test_price_bars_span_date_range(self):
        bars = self.provider.get_prices("AAPL", _START, _END)
        assert len(bars) >= 2  # multi-day range

    def test_quality_source_matches_provider(self):
        bars = self.provider.get_prices("AAPL", _START, _END)
        for bar in bars:
            assert bar.quality.source == self.provider.source_id

    def test_corporate_actions_return_multiple_types(self):
        actions = self.provider.get_corporate_actions("AAPL", _START, _END)
        types = {a.action_type for a in actions}
        assert "dividend" in types
        assert "split" in types

    def test_resolve_ticker_applies_vendor_symbology(self):
        assert self.provider.resolve_ticker("AAPL") == "BBG:AAPL"
        assert self.provider.resolve_ticker("NESN", country="CH") == "BBG:NESN"


# ---------------------------------------------------------------------------
# Contract tests: mock paid FundamentalsProvider
# ---------------------------------------------------------------------------


class TestMockPaidFundamentalsContract:
    def setup_method(self):
        self.provider = MockPaidFundamentalsProvider()

    def test_get_fundamentals_returns_snapshots(self):
        snaps = self.provider.get_fundamentals("AAPL")
        assert isinstance(snaps, list)
        assert all(isinstance(s, FundamentalSnapshot) for s in snaps)

    def test_respects_limit(self):
        snaps = self.provider.get_fundamentals("AAPL", limit=2)
        assert len(snaps) <= 2

    def test_paid_adapter_provides_richer_coverage(self):
        snap = self.provider.get_fundamentals("AAPL")[0]
        assert snap.revenue is not None
        assert snap.net_income is not None
        assert snap.operating_income is not None
        assert snap.eps is not None
        assert snap.book_value_per_share is not None
        assert snap.debt_to_equity is not None
        assert snap.current_ratio is not None
        assert snap.free_cash_flow is not None

    def test_quality_source_matches_provider(self):
        snaps = self.provider.get_fundamentals("AAPL")
        for snap in snaps:
            assert snap.quality.source == self.provider.source_id


# ---------------------------------------------------------------------------
# Contract tests: mock paid ReferenceDataProvider
# ---------------------------------------------------------------------------


class TestMockPaidReferenceDataContract:
    def setup_method(self):
        self.provider = MockPaidReferenceDataProvider()

    def test_get_security_returns_security(self):
        sec = self.provider.get_security("AAPL")
        assert isinstance(sec, Security)

    def test_security_has_isin(self):
        sec = self.provider.get_security("AAPL")
        assert sec.isin is not None
        assert len(sec.isin) > 0

    def test_security_has_currency(self):
        sec = self.provider.get_security("AAPL")
        assert sec.currency == "USD"

    def test_get_identifier_map_works_when_supported(self):
        id_map = self.provider.get_identifier_map("AAPL")
        assert isinstance(id_map, IdentifierMap)
        assert "lseg" in id_map.ticker_map
        assert id_map.isin is not None

    def test_quality_source_matches_provider(self):
        sec = self.provider.get_security("AAPL")
        assert sec.quality.source == self.provider.source_id


# ---------------------------------------------------------------------------
# Contract tests: mock paid FXProvider
# ---------------------------------------------------------------------------


class TestMockPaidFXContract:
    def setup_method(self):
        self.provider = MockPaidFXProvider()

    def test_get_fx_rate_returns_positive_float(self):
        rate = self.provider.get_fx_rate("USD", "EUR", _TODAY)
        assert isinstance(rate, float)
        assert rate > 0

    def test_returns_different_rates_per_pair(self):
        usd_eur = self.provider.get_fx_rate("USD", "EUR", _TODAY)
        gbp_usd = self.provider.get_fx_rate("GBP", "USD", _TODAY)
        assert usd_eur != gbp_usd

    def test_unknown_pair_returns_fallback(self):
        rate = self.provider.get_fx_rate("JPY", "CHF", _TODAY)
        assert isinstance(rate, float)
        assert rate > 0


# ---------------------------------------------------------------------------
# Contract tests: mock paid BenchmarkProvider
# ---------------------------------------------------------------------------


class TestMockPaidBenchmarkContract:
    def setup_method(self):
        self.provider = MockPaidBenchmarkProvider()

    def test_get_constituents_returns_multiple(self):
        constituents = self.provider.get_constituents("SPY", _TODAY)
        assert len(constituents) >= 2

    def test_constituents_are_valid_instances(self):
        constituents = self.provider.get_constituents("SPY", _TODAY)
        for c in constituents:
            assert isinstance(c, BenchmarkConstituent)

    def test_weights_sum_at_most_one(self):
        constituents = self.provider.get_constituents("SPY", _TODAY)
        total = sum(c.weight for c in constituents)
        assert total <= Decimal("1.0001")

    def test_quality_source_matches_provider(self):
        constituents = self.provider.get_constituents("SPY", _TODAY)
        for c in constituents:
            assert c.quality.source == self.provider.source_id


# ---------------------------------------------------------------------------
# Contract tests: composite multi-interface provider
# ---------------------------------------------------------------------------


class TestCompositeProviderContract:
    """A single provider instance can serve multiple data types."""

    def setup_method(self):
        self.provider = MockPaidCompositeProvider()

    def test_is_price_provider(self):
        assert isinstance(self.provider, PriceProvider)
        assert ProviderCapability.PRICES in self.provider.capabilities

    def test_is_fundamentals_provider(self):
        assert isinstance(self.provider, FundamentalsProvider)
        assert ProviderCapability.FUNDAMENTALS in self.provider.capabilities

    def test_is_reference_data_provider(self):
        assert isinstance(self.provider, ReferenceDataProvider)
        assert ProviderCapability.REFERENCE_DATA in self.provider.capabilities

    def test_prices_and_fundamentals_from_same_source_id(self):
        bars = self.provider.get_prices("AAPL", _START, _END)
        snaps = self.provider.get_fundamentals("AAPL")
        assert bars[0].quality.source == snaps[0].quality.source == self.provider.source_id

    def test_corporate_actions_supported(self):
        actions = self.provider.get_corporate_actions("AAPL", _START, _END)
        assert len(actions) > 0
        assert all(isinstance(a, CorporateAction) for a in actions)

    def test_security_lookup_works(self):
        sec = self.provider.get_security("AAPL")
        assert isinstance(sec, Security)
        assert sec.quality.source == self.provider.source_id


# ---------------------------------------------------------------------------
# Parity tests: free vs. paid adapter output compatibility
# ---------------------------------------------------------------------------


class TestFreeVsPaidOutputParity:
    """Verify that free and paid adapters produce schema-compatible outputs.

    These tests confirm a future vendor migration is an adapter-level swap:
    downstream scoring/construction code will work unchanged because both
    adapter families produce the same model types with the same mandatory
    field population rules.
    """

    def test_price_bar_schema_parity(self):
        free = StubPriceProvider().get_prices("AAPL", _START, _END)[0]
        paid = MockPaidPriceProvider().get_prices("AAPL", _START, _END)[0]
        assert type(free) is type(paid) is PriceBar
        # Both must populate the same mandatory fields
        for field in ("security_id", "date", "open", "high", "low", "close", "volume"):
            assert getattr(free, field) is not None
            assert getattr(paid, field) is not None

    def test_fundamental_snapshot_schema_parity(self):
        free = StubFundamentalsProvider().get_fundamentals("AAPL")[0]
        paid = MockPaidFundamentalsProvider().get_fundamentals("AAPL")[0]
        assert type(free) is type(paid) is FundamentalSnapshot
        # Both must populate security_id, period_end_date, period_type, quality
        for field in ("security_id", "period_end_date", "period_type", "quality"):
            assert getattr(free, field) is not None
            assert getattr(paid, field) is not None

    def test_security_schema_parity(self):
        free = StubReferenceDataProvider().get_security("AAPL")
        paid = MockPaidReferenceDataProvider().get_security("AAPL")
        assert type(free) is type(paid) is Security
        for field in ("internal_id", "ticker", "name", "security_type", "exchange", "country"):
            assert getattr(free, field) is not None
            assert getattr(paid, field) is not None

    def test_benchmark_constituent_schema_parity(self):
        free = StubBenchmarkProvider().get_constituents("SPY", _TODAY)
        paid = MockPaidBenchmarkProvider().get_constituents("SPY", _TODAY)
        for c in free + paid:
            assert isinstance(c, BenchmarkConstituent)
            assert c.benchmark_id is not None
            assert c.security_id is not None
            assert c.weight > 0

    def test_paid_fundamentals_strictly_richer_than_free(self):
        """Paid adapter fills optional fields that free adapter leaves None."""
        free = StubFundamentalsProvider().get_fundamentals("AAPL")[0]
        paid = MockPaidFundamentalsProvider().get_fundamentals("AAPL")[0]
        # Free stub only sets revenue; paid sets many more
        assert free.debt_to_equity is None
        assert paid.debt_to_equity is not None
        assert free.net_income is None
        assert paid.net_income is not None
        assert free.free_cash_flow is None
        assert paid.free_cash_flow is not None
