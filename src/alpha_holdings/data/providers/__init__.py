"""Provider adapters for alpha-holdings data layer.

Free adapters (Yahoo Finance, Stooq, SEC EDGAR) live in ``free/``.
Future paid adapters (Bloomberg, FactSet, LSEG) will live in ``paid/``.
All adapters must conform to the ABCs in ``base.py``.
"""

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

__all__ = [
    "BaseProvider",
    "BenchmarkProvider",
    "FXProvider",
    "FundamentalsProvider",
    "PriceProvider",
    "ProviderCapability",
    "ProviderCapabilityError",
    "ReferenceDataProvider",
]
