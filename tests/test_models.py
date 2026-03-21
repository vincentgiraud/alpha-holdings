"""Tests for domain models and contracts."""

import pytest
from decimal import Decimal
from datetime import datetime

from alpha_holdings.domain import (
    Security,
    IdentifierMap,
    PriceBar,
    DataQuality,
    Holding,
    TargetWeight,
)


class TestSecurity:
    """Test Security model validation and serialization."""

    def test_security_creation(self, sample_security):
        """Test creating a Security object."""
        assert sample_security.ticker == "AAPL"
        assert sample_security.security_type == "equity"

    def test_security_serialization(self, sample_security):
        """Test Security serialization to dict."""
        data = sample_security.model_dump()
        assert data["ticker"] == "AAPL"
        assert "quality" in data

    def test_security_required_fields(self, data_quality):
        """Test that required fields are enforced."""
        with pytest.raises(Exception):
            Security(
                internal_id="test",
                ticker="TEST",
                # Missing required fields
                quality=data_quality,
            )


class TestPriceBar:
    """Test PriceBar model for OHLCV price data."""

    def test_pricebar_creation(self, data_quality):
        """Test creating a PriceBar."""
        bar = PriceBar(
            security_id="sec_001",
            date=datetime(2025, 1, 31),
            open=Decimal("150.00"),
            high=Decimal("155.00"),
            low=Decimal("149.00"),
            close=Decimal("152.50"),
            volume=1000000,
            quality=data_quality,
        )
        assert bar.close == Decimal("152.50")
        assert bar.volume == 1000000

    def test_pricebar_decimal_serialization(self, data_quality):
        """Test that Decimal fields serialize correctly."""
        bar = PriceBar(
            security_id="sec_001",
            date=datetime(2025, 1, 31),
            open=Decimal("150.00"),
            high=Decimal("155.00"),
            low=Decimal("149.00"),
            close=Decimal("152.50"),
            volume=1000000,
            quality=data_quality,
        )
        data = bar.model_dump_json()
        assert "150.00" in data or "150" in data


class TestHolding:
    """Test Holding model for portfolio positions."""

    def test_holding_creation(self, data_quality):
        """Test creating a Holding."""
        holding = Holding(
            portfolio_id="port_001",
            security_id="sec_001",
            as_of_date=datetime(2025, 1, 31),
            shares=Decimal("100"),
            book_cost_per_share=Decimal("150.00"),
            current_price=Decimal("152.50"),
            market_value=Decimal("15250"),
            quality=data_quality,
        )
        assert holding.shares == Decimal("100")
        assert holding.market_value == Decimal("15250")


class TestTargetWeight:
    """Test TargetWeight model for portfolio construction."""

    def test_target_weight_creation(self, data_quality):
        """Test creating a TargetWeight."""
        target = TargetWeight(
            portfolio_id="port_001",
            security_id="sec_001",
            target_date=datetime(2025, 2, 28),
            target_weight=Decimal("0.05"),
            min_weight=Decimal("0.02"),
            max_weight=Decimal("0.08"),
            reason="score_rank_5",
            quality=data_quality,
        )
        assert target.target_weight == Decimal("0.05")
        assert target.min_weight == Decimal("0.02")
