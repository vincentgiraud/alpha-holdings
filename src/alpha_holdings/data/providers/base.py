"""Abstract provider interfaces for alpha-holdings data layer.

Every adapter — free (Yahoo, Stooq, EDGAR) or future paid (Bloomberg, FactSet,
LSEG) — must implement the relevant ABCs defined here. Scoring and portfolio
code must depend only on these interfaces so vendor swaps are adapter-level
changes rather than system-wide rewrites.

Capability gaps are surfaced explicitly:
- Each provider declares which capabilities it supports via `capabilities`.
- Callers that require a capability the adapter cannot supply will receive a
  `ProviderCapabilityError` rather than silently degraded data.
"""

from abc import ABC, abstractmethod
from datetime import date
from enum import Enum, auto

from alpha_holdings.domain.models import (
    BenchmarkConstituent,
    CorporateAction,
    FundamentalSnapshot,
    IdentifierMap,
    PriceBar,
    Security,
)

# ---------------------------------------------------------------------------
# Capability registry
# ---------------------------------------------------------------------------


class ProviderCapability(Enum):
    PRICES = auto()
    CORPORATE_ACTIONS = auto()
    FUNDAMENTALS = auto()
    REFERENCE_DATA = auto()
    CLASSIFICATIONS = auto()
    FX = auto()
    BENCHMARK_CONSTITUENTS = auto()


class ProviderCapabilityError(NotImplementedError):
    """Raised when a provider does not support a requested capability."""


# ---------------------------------------------------------------------------
# Base provider
# ---------------------------------------------------------------------------


class BaseProvider(ABC):
    """Common base for all data providers."""

    #: Subclasses must declare which capabilities they support.
    capabilities: frozenset[ProviderCapability] = frozenset()

    @property
    @abstractmethod
    def source_id(self) -> str:
        """Unique string identifier for this provider (e.g. 'yahoo', 'bloomberg')."""

    def _require(self, capability: ProviderCapability) -> None:
        if capability not in self.capabilities:
            raise ProviderCapabilityError(
                f"Provider '{self.source_id}' does not support capability {capability.name}. "
                "Check provider.capabilities before calling this method."
            )

    def resolve_ticker(self, canonical: str, *, country: str = "") -> str:  # noqa: ARG002
        """Map a canonical symbol to a provider-native ticker.

        The default implementation returns the canonical symbol unchanged.
        Provider subclasses override this when their ticker format differs
        from the canonical universe symbols (e.g. Yahoo needs exchange
        suffixes for non-US names and uses hyphens for share classes).
        """
        return canonical


# ---------------------------------------------------------------------------
# Price provider
# ---------------------------------------------------------------------------


class PriceProvider(BaseProvider):
    """Provides OHLCV price history and corporate actions."""

    capabilities = frozenset({ProviderCapability.PRICES})

    @abstractmethod
    def get_prices(
        self,
        ticker: str,
        start: date,
        end: date,
        *,
        adjusted: bool = True,
    ) -> list[PriceBar]:
        """Return daily OHLCV bars for *ticker* between *start* and *end* inclusive.

        Args:
            ticker: Source-native ticker symbol.
            start: First date (inclusive).
            end:   Last date (inclusive).
            adjusted: When True, return split/dividend-adjusted prices if the
                      source supports it. When False or the source cannot supply
                      adjusted data, return raw prices and populate the
                      ``data_flags`` field of each bar's ``DataQuality`` with
                      ``'unadjusted'``.
        """

    def get_corporate_actions(
        self,
        ticker: str,  # noqa: ARG002
        start: date,  # noqa: ARG002
        end: date,  # noqa: ARG002
    ) -> list[CorporateAction]:
        """Return dividends and splits for *ticker* in the date range.

        Providers that cannot supply corporate actions should raise
        ``ProviderCapabilityError`` (or simply not override this method; the
        default implementation raises it automatically).
        """
        self._require(ProviderCapability.CORPORATE_ACTIONS)
        raise NotImplementedError  # pragma: no cover


# ---------------------------------------------------------------------------
# Fundamentals provider
# ---------------------------------------------------------------------------


class FundamentalsProvider(BaseProvider):
    """Provides periodic fundamental snapshots."""

    capabilities = frozenset({ProviderCapability.FUNDAMENTALS})

    @abstractmethod
    def get_fundamentals(
        self,
        ticker: str,
        *,
        limit: int = 8,
    ) -> list[FundamentalSnapshot]:
        """Return the most-recent *limit* fundamental snapshots for *ticker*.

        Snapshots are ordered newest-first. Providers that can only offer
        annual data should return annual periods and populate
        ``period_type='FY'``; quarterly providers use ``'Q1'``-``'Q4'``.
        Missing individual fields must be ``None`` rather than zero.
        """


# ---------------------------------------------------------------------------
# Reference data provider
# ---------------------------------------------------------------------------


class ReferenceDataProvider(BaseProvider):
    """Provides security metadata and identifier mappings."""

    capabilities = frozenset({ProviderCapability.REFERENCE_DATA})

    @abstractmethod
    def get_security(self, ticker: str) -> Security:
        """Return canonical ``Security`` metadata for *ticker*."""

    def get_identifier_map(self, ticker: str) -> IdentifierMap:  # noqa: ARG002
        """Return cross-vendor identifier map for *ticker*.

        Providers that cannot supply multi-vendor mappings should raise
        ``ProviderCapabilityError``.
        """
        self._require(ProviderCapability.REFERENCE_DATA)
        raise NotImplementedError  # pragma: no cover


# ---------------------------------------------------------------------------
# FX provider
# ---------------------------------------------------------------------------


class FXProvider(BaseProvider):
    """Provides foreign exchange rates."""

    capabilities = frozenset({ProviderCapability.FX})

    @abstractmethod
    def get_fx_rate(
        self,
        base: str,
        quote: str,
        as_of: date,
    ) -> float:
        """Return the spot FX rate for *base*/*quote* on *as_of*.

        Args:
            base:  ISO 4217 base currency (e.g. 'USD').
            quote: ISO 4217 quote currency (e.g. 'EUR').
            as_of: The date for which the rate is requested.

        Returns:
            Float exchange rate (how many units of *quote* per 1 *base*).
        """


# ---------------------------------------------------------------------------
# Benchmark provider
# ---------------------------------------------------------------------------


class BenchmarkProvider(BaseProvider):
    """Provides benchmark constituent data."""

    capabilities = frozenset({ProviderCapability.BENCHMARK_CONSTITUENTS})

    @abstractmethod
    def get_constituents(
        self,
        benchmark_id: str,
        as_of: date,
    ) -> list[BenchmarkConstituent]:
        """Return benchmark constituents for *benchmark_id* as of *as_of*.

        Providers that cannot supply point-in-time constituents must populate
        each record's ``DataQuality.data_flags`` with ``'degraded_snapshot'``
        rather than silently returning stale data.
        """
