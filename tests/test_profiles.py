"""Tests for investor profile and portfolio constraints."""

import pytest
from decimal import Decimal

from alpha_holdings.domain import (
    ProfileToConstraints,
    FireVariant,
)


class TestProfileToConstraints:
    """Test ProfileToConstraints resolver."""

    def test_resolve_fat_fire(self, fat_fire_profile):
        """Test constraint resolution for Fat FIRE profile."""
        constraints = ProfileToConstraints.resolve(fat_fire_profile)
        assert constraints.profile_id == fat_fire_profile.profile_id
        assert constraints.max_single_name_weight == Decimal("0.05")
        assert constraints.rebalance_cadence == "monthly"
        # Fat FIRE with risk 4 should not have tight constraints
        assert constraints.max_annual_turnover == Decimal("0.50")

    def test_resolve_lean_fire(self, lean_fire_profile):
        """Test constraint resolution for Lean FIRE profile."""
        constraints = ProfileToConstraints.resolve(lean_fire_profile)
        assert constraints.profile_id == lean_fire_profile.profile_id
        # Lean FIRE has risk 3, should have moderate constraints
        assert constraints.min_holdings_count >= 30

    def test_resolve_conservative(self, conservative_profile):
        """Test constraint resolution for conservative profile."""
        constraints = ProfileToConstraints.resolve(conservative_profile)
        # Conservative profile (risk 2) should tighten constraints
        assert constraints.max_annual_turnover == Decimal("0.30")
        assert constraints.min_holdings_count == 50  # Retirement-complement gets stricter
        assert constraints.max_portfolio_volatility == Decimal("0.10")
        assert constraints.max_drawdown_tolerance == Decimal("0.10")


class TestAssetAllocator:
    """Test AssetAllocator for multi-asset allocation."""

    def test_allocate_fat_fire_with_crypto(self, fat_fire_profile):
        """Test asset allocation for Fat FIRE with crypto enabled."""
        from alpha_holdings.portfolio.asset_allocation import AssetAllocator

        result = AssetAllocator.allocate(fat_fire_profile)
        assert result.profile_id == fat_fire_profile.profile_id
        # Should have equity, bond, and crypto allocations
        assert len(result.asset_classes) == 3
        asset_types = [ac.asset_class for ac in result.asset_classes]
        assert "equity" in asset_types
        assert "bond" in asset_types
        assert "crypto" in asset_types

    def test_allocate_lean_fire_no_crypto(self, lean_fire_profile):
        """Test asset allocation for Lean FIRE without crypto."""
        from alpha_holdings.portfolio.asset_allocation import AssetAllocator

        result = AssetAllocator.allocate(lean_fire_profile)
        # Should have only equity and bond
        assert len(result.asset_classes) == 2
        asset_types = [ac.asset_class for ac in result.asset_classes]
        assert "equity" in asset_types
        assert "bond" in asset_types
        assert "crypto" not in asset_types

    def test_allocation_weights_sum_to_one(self, fat_fire_profile):
        """Test that allocation weights sum to ~1.0."""
        from alpha_holdings.portfolio.asset_allocation import AssetAllocator

        result = AssetAllocator.allocate(fat_fire_profile)
        total_target = sum(ac.target_weight for ac in result.asset_classes)
        # Should be close to 1.0 (within rounding)
        assert Decimal("0.99") <= total_target <= Decimal("1.01")
