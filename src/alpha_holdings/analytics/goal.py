"""Goal-aware analytics: wealth probability, sequence-of-returns risk, safe withdrawal rates.

These analytics augment traditional portfolio metrics (return, risk, attribution) with
profile-specific analysis. They help investors understand whether their portfolio is
likely to meet their stated financial goals under various market scenarios.
"""

from decimal import Decimal
from typing import NamedTuple

from alpha_holdings.domain.investor_profile import InvestorProfile, WithdrawalPattern


class GoalAnalyticsResult(NamedTuple):
    """Goal-based portfolio analysis for a given profile.

    Attributes:
        profile_id: Source InvestorProfile ID
        wealth_target_probability: P(portfolio >= target) at horizon date
        sequence_of_returns_risk: Estimated range of returns accounting for withdrawal sequence risk
        safe_withdrawal_rate: Annual withdrawal rate sustainable over horizon (4% rule adjusted for profile)
        notes: Qualitative assessment and caveats
    """

    profile_id: str
    wealth_target_probability: Decimal | None = None  # 0.0 to 1.0
    sequence_of_returns_risk: tuple[Decimal, Decimal] | None = (
        None  # (5th percentile, 95th percentile)
    )
    safe_withdrawal_rate: Decimal | None = None  # 0.03 to 0.05 typical
    notes: str | None = None


class GoalAnalytics:
    """Compute goal-aware analytics given profile and portfolio metrics."""

    # Base safe withdrawal rate (4% rule for 30-year horizon)
    BASE_SAFE_WITHDRAWAL_RATE = Decimal("0.04")

    @staticmethod
    def compute(
        profile: InvestorProfile,
        portfolio_value: Decimal,
        portfolio_return_annual: Decimal,
        portfolio_volatility: Decimal,
        target_wealth: Decimal | None = None,
    ) -> GoalAnalyticsResult:
        """Compute goal analytics for a profile and portfolio state.

        Args:
            profile: InvestorProfile with goal and horizon data.
            portfolio_value: Current portfolio market value.
            portfolio_return_annual: Expected annual return (decimal, e.g., 0.07 for 7%).
            portfolio_volatility: Annual portfolio volatility (std dev).
            target_wealth: Optional target wealth at horizon; defaults to profile-based estimate.

        Returns:
            GoalAnalyticsResult object with probability, sequence risk, and SWR estimates.
        """
        if target_wealth is None:
            target_wealth = GoalAnalytics._estimate_target_wealth(profile, portfolio_value)

        prob_success = GoalAnalytics._compute_wealth_probability(
            portfolio_value,
            portfolio_return_annual,
            portfolio_volatility,
            target_wealth,
            profile.horizon_years,
        )

        sor_range = GoalAnalytics._estimate_sequence_of_returns_risk(
            portfolio_return_annual, portfolio_volatility, profile.horizon_years
        )

        swr = GoalAnalytics._compute_safe_withdrawal_rate(
            profile, portfolio_value, target_wealth, portfolio_return_annual, portfolio_volatility
        )

        notes = GoalAnalytics._generate_notes(profile, prob_success, swr)

        return GoalAnalyticsResult(
            profile_id=profile.profile_id,
            wealth_target_probability=prob_success,
            sequence_of_returns_risk=sor_range,
            safe_withdrawal_rate=swr,
            notes=notes,
        )

    @staticmethod
    def _estimate_target_wealth(
        profile: InvestorProfile, current_portfolio_value: Decimal
    ) -> Decimal:
        """Estimate a reasonable target wealth based on profile.

        For accumulation phases (no withdrawals), the target is current value compounded.
        For drawdown phases, the target is a sustainable ongoing balance.
        """
        if profile.withdrawal_pattern == WithdrawalPattern.COMPOUND_ONLY:
            # Compound at 6% real return (conservative for equity-heavy)
            annual_return = Decimal("0.06")
            target = (
                current_portfolio_value * (Decimal("1.0") + annual_return) ** profile.horizon_years
            )
            return target

        # For regular drawdown or lump sum, estimate a safe withdrawal amount
        # and ensure portfolio can sustain it for the horizon
        annual_withdrawal = current_portfolio_value * Decimal("0.04")  # 4% rule baseline
        target = annual_withdrawal * Decimal(profile.horizon_years) * Decimal("0.5")  # Conservative

        return target

    @staticmethod
    def _compute_wealth_probability(
        current_value: Decimal,
        annual_return: Decimal,
        volatility: Decimal,
        target_value: Decimal,
        horizon_years: int,
    ) -> Decimal:
        """Estimate probability of reaching target wealth using Monte Carlo approximation.

        This is a simplified approximation. In production, run full Monte Carlo with
        correlated asset returns and withdrawal sequences.

        Uses geometric Brownian motion: the probability that a portfolio reaches
        a target is approximated by comparing the expected geometric return
        distribution to the required return.
        """
        if current_value >= target_value:
            return Decimal("1.00")

        required_annual_return = (target_value / current_value) ** (
            Decimal("1.0") / Decimal(horizon_years)
        ) - Decimal("1.0")

        # Approximate: P(return >= required) assuming normal distribution
        # Z-score: (required - expected) / (volatility / sqrt(horizon))
        expected_return = annual_return
        std_error = volatility / (Decimal(horizon_years) ** Decimal("0.5"))

        z_score = (
            float((required_annual_return - expected_return) / std_error) if std_error > 0 else 0.0
        )

        # Normal CDF approximation (simple; use scipy.stats.norm in production)
        from math import erf

        prob = (1.0 + erf(z_score / (2**0.5))) / 2.0
        return Decimal(str(prob))

    @staticmethod
    def _estimate_sequence_of_returns_risk(
        annual_return: Decimal, volatility: Decimal, horizon_years: int
    ) -> tuple[Decimal, Decimal]:
        """Estimate 5th and 95th percentile portfolio returns over horizon.

        Accounts for sequence-of-returns risk: the order of returns matters if
        withdrawals occur, especially early negative returns cause lasting damage.

        In production, run full Monte Carlo to account for withdrawal timing.
        """
        # Annualized geometric return (accounting for volatility drag)
        geometric_return = annual_return - (volatility**2) / Decimal("2.0")

        # Range approximation (±1.65 std for 90% CI)
        total_volatility = volatility * (Decimal(horizon_years) ** Decimal("0.5"))
        range_width = Decimal("1.65") * total_volatility

        p5 = geometric_return * Decimal(horizon_years) - range_width
        p95 = geometric_return * Decimal(horizon_years) + range_width

        return (p5, p95)

    @staticmethod
    def _compute_safe_withdrawal_rate(
        profile: InvestorProfile,
        _portfolio_value: Decimal,
        _target_wealth: Decimal,
        annual_return: Decimal,
        volatility: Decimal,
    ) -> Decimal | None:
        """Compute sustainable withdrawal rate adjusted for profile.

        Base: 4% rule (Trinity study for 30-year horizon).
        Adjustments:
          - Shorter horizons: higher SWR
          - Longer horizons: lower SWR
          - Higher volatility: lower SWR
          - Lower returns: lower SWR
        """
        if profile.withdrawal_pattern == WithdrawalPattern.COMPOUND_ONLY:
            return None  # No withdrawals

        base_swr = GoalAnalytics.BASE_SAFE_WITHDRAWAL_RATE

        # Adjust for horizon
        if profile.horizon_years < 10:
            base_swr = base_swr + Decimal("0.01")  # 5% for shorter horizons
        elif profile.horizon_years > 40:
            base_swr = base_swr - Decimal("0.01")  # 3% for very long horizons

        # Adjust for volatility (higher volatility = lower safe rate)
        if volatility > Decimal("0.15"):
            base_swr = base_swr - Decimal("0.005")
        elif volatility < Decimal("0.10"):
            base_swr = base_swr + Decimal("0.005")

        # Adjust for return expectations
        if annual_return < Decimal("0.05"):
            base_swr = base_swr - Decimal("0.01")
        elif annual_return > Decimal("0.08"):
            base_swr = base_swr + Decimal("0.005")

        return base_swr

    @staticmethod
    def _generate_notes(
        profile: InvestorProfile, prob_success: Decimal, swr: Decimal | None
    ) -> str:
        """Generate qualitative assessment of goal feasibility."""
        notes = []

        if prob_success < Decimal("0.50"):
            notes.append(
                f"⚠️ Low probability of success ({prob_success:.1%}): "
                "consider increasing return expectations, extending horizon, or adjusting spending."
            )
        elif prob_success < Decimal("0.75"):
            notes.append(
                f"Moderate confidence ({prob_success:.1%}): Monitor quarterly and adjust as needed."
            )
        else:
            notes.append(f"Good probability of success ({prob_success:.1%}).")

        if swr is not None and swr < Decimal("0.03"):
            notes.append(
                "⚠️ Conservative safe withdrawal rate: portfolio may be insufficient for "
                "the planned withdrawal pattern given volatility and horizon."
            )

        if (
            profile.withdrawal_pattern == WithdrawalPattern.REGULAR_DRAWDOWN
            and profile.horizon_years < 5
        ):
            notes.append(
                "🔴 Near-term withdrawals: high sequence-of-returns risk. "
                "Consider building larger cash buffer."
            )

        if profile.crypto_enabled:
            notes.append("Info: Crypto allocation should be treated as speculative; monitor concentration risk.")

        return " ".join(notes) if notes else "Profile appears feasible with current parameters."
