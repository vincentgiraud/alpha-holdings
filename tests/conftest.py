"""Pytest fixtures for alpha-holdings tests."""

from datetime import datetime

import pytest

from alpha_holdings.domain import (
    DataQuality,
    FireVariant,
    InvestorProfile,
    Security,
    WithdrawalPattern,
)


@pytest.fixture
def data_quality():
    """Create a sample DataQuality object."""
    return DataQuality(
        source="test",
        as_of_date=datetime(2025, 1, 31),
        publish_date=datetime(2025, 2, 1),
        currency="USD",
        data_flags=["test"],
        notes="Test data",
    )


@pytest.fixture
def sample_security(data_quality):
    """Create a sample Security object."""
    return Security(
        internal_id="sec_001",
        ticker="AAPL",
        isin="US0378331005",
        name="Apple Inc.",
        security_type="equity",
        exchange="NASDAQ",
        currency="USD",
        country="US",
        sector="Information Technology",
        industry="Consumer Electronics",
        quality=data_quality,
    )


@pytest.fixture
def fat_fire_profile():
    """Create a Fat FIRE investor profile."""
    return InvestorProfile(
        profile_id="profile_fat_fire_001",
        fire_variant=FireVariant.FAT_FIRE,
        risk_appetite=4,
        horizon_years=20,
        withdrawal_pattern=WithdrawalPattern.REGULAR_DRAWDOWN,
        target_real_return_pct=4.0,
        crypto_enabled=True,
        name="Fat FIRE, 20yr, Risk 4",
    )


@pytest.fixture
def lean_fire_profile():
    """Create a Lean FIRE investor profile."""
    return InvestorProfile(
        profile_id="profile_lean_fire_001",
        fire_variant=FireVariant.LEAN_FIRE,
        risk_appetite=3,
        horizon_years=10,
        withdrawal_pattern=WithdrawalPattern.REGULAR_DRAWDOWN,
        target_real_return_pct=3.0,
        crypto_enabled=False,
        name="Lean FIRE, 10yr, Risk 3",
    )


@pytest.fixture
def conservative_profile():
    """Create a conservative retirement complement profile."""
    return InvestorProfile(
        profile_id="profile_retirement_comp_001",
        fire_variant=FireVariant.RETIREMENT_COMPLEMENT,
        risk_appetite=2,
        horizon_years=5,
        withdrawal_pattern=WithdrawalPattern.REGULAR_DRAWDOWN,
        target_real_return_pct=2.0,
        crypto_enabled=False,
        name="Retirement Complement, 5yr, Risk 2",
    )
