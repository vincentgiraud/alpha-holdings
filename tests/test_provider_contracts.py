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
