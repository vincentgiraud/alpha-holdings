"""Tests for goal-aware analytics."""

import pytest
from decimal import Decimal
from datetime import datetime

from alpha_holdings.analytics.goal import GoalAnalytics, GoalAnalyticsResult


class TestGoalAnalytics:
    """Test goal-aware analytics computation."""

    def test_compute_simple_case(self, fat_fire_profile):
        """Test computing goal analytics for a Fat FIRE profile."""
        result = GoalAnalytics.compute(
            profile=fat_fire_profile,
            portfolio_value=Decimal("500000"),
            portfolio_return_annual=Decimal("0.07"),
            portfolio_volatility=Decimal("0.12"),
            target_wealth=Decimal("1000000"),
        )
        assert result.profile_id == fat_fire_profile.profile_id
        assert result.wealth_target_probability is not None
        assert 0 <= result.wealth_target_probability <= 1
        assert result.sequence_of_returns_risk is not None

    def test_compute_safe_withdrawal_rate(self, fat_fire_profile):
        """Test SWR computation."""
        result = GoalAnalytics.compute(
            profile=fat_fire_profile,
            portfolio_value=Decimal("500000"),
            portfolio_return_annual=Decimal("0.07"),
            portfolio_volatility=Decimal("0.12"),
            target_wealth=Decimal("1000000"),
        )
        # Fat FIRE with regular drawdown should have a non-None SWR
        assert result.safe_withdrawal_rate is not None
        assert Decimal("0.02") <= result.safe_withdrawal_rate <= Decimal("0.06")

    def test_compute_compound_only_profile(self, fat_fire_profile):
        """Test computation for accumulation-only profile."""
        from alpha_holdings.domain import WithdrawalPattern

        compound_profile = fat_fire_profile
        compound_profile.withdrawal_pattern = WithdrawalPattern.COMPOUND_ONLY

        result = GoalAnalytics.compute(
            profile=compound_profile,
            portfolio_value=Decimal("500000"),
            portfolio_return_annual=Decimal("0.07"),
            portfolio_volatility=Decimal("0.12"),
        )
        # Should have no SWR for compound_only
        assert result.safe_withdrawal_rate is None
        assert result.wealth_target_probability is not None
