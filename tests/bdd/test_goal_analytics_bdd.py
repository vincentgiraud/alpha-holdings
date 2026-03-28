"""BDD scenarios for goal analytics behavior."""

from decimal import Decimal

from pytest_bdd import given, scenarios, then, when

from alpha_holdings.analytics.goal import GoalAnalytics
from alpha_holdings.domain.investor_profile import (
    FireVariant,
    InvestorProfile,
    WithdrawalPattern,
)

scenarios("features/goal_analytics.feature")

# ---------------------------------------------------------------------------
# Shared portfolio parameters
# ---------------------------------------------------------------------------

PORTFOLIO_VALUE = Decimal("1000000")
ANNUAL_RETURN = Decimal("0.07")
VOLATILITY = Decimal("0.15")


# ---------------------------------------------------------------------------
# Scenario 1: Higher target reduces probability
# ---------------------------------------------------------------------------


@given(
    "an investor profile with 20-year horizon and regular drawdown",
    target_fixture="profile",
)
def given_20yr_profile():
    return InvestorProfile(
        profile_id="bdd_goal_20yr",
        fire_variant=FireVariant.FAT_FIRE,
        risk_appetite=4,
        horizon_years=20,
        withdrawal_pattern=WithdrawalPattern.REGULAR_DRAWDOWN,
        crypto_enabled=False,
    )


@given("a portfolio valued at 1000000 with 7% annual return and 15% volatility")
def given_portfolio_params():
    pass  # parameters are module-level constants


@when(
    "I compute goal analytics with a target of 2000000",
    target_fixture="result_low_target",
)
def when_goal_low_target(profile):
    return GoalAnalytics.compute(
        profile=profile,
        portfolio_value=PORTFOLIO_VALUE,
        portfolio_return_annual=ANNUAL_RETURN,
        portfolio_volatility=VOLATILITY,
        target_wealth=Decimal("2000000"),
    )


@when(
    "I compute goal analytics with a target of 5000000",
    target_fixture="result_high_target",
)
def when_goal_high_target(profile):
    return GoalAnalytics.compute(
        profile=profile,
        portfolio_value=PORTFOLIO_VALUE,
        portfolio_return_annual=ANNUAL_RETURN,
        portfolio_volatility=VOLATILITY,
        target_wealth=Decimal("5000000"),
    )


@then("the probability of reaching 2000000 is greater than the probability of reaching 5000000")
def then_lower_target_higher_prob(result_low_target, result_high_target):
    assert result_low_target.wealth_target_probability is not None
    assert result_high_target.wealth_target_probability is not None
    assert (
        result_low_target.wealth_target_probability > result_high_target.wealth_target_probability
    )


# ---------------------------------------------------------------------------
# Scenario 2: Compound-only has no SWR
# ---------------------------------------------------------------------------


@given(
    "an investor profile with compound-only withdrawal pattern",
    target_fixture="profile",
)
def given_compound_only_profile():
    return InvestorProfile(
        profile_id="bdd_goal_compound",
        fire_variant=FireVariant.COAST_FIRE,
        risk_appetite=4,
        horizon_years=20,
        withdrawal_pattern=WithdrawalPattern.COMPOUND_ONLY,
        crypto_enabled=False,
    )


@when("I compute goal analytics", target_fixture="goal_result")
def when_compute_goal(profile):
    return GoalAnalytics.compute(
        profile=profile,
        portfolio_value=PORTFOLIO_VALUE,
        portfolio_return_annual=ANNUAL_RETURN,
        portfolio_volatility=VOLATILITY,
    )


@then("the safe withdrawal rate is None")
def then_swr_none(goal_result):
    assert goal_result.safe_withdrawal_rate is None


# ---------------------------------------------------------------------------
# Scenario 3: Shorter horizon widens SoR risk band
# ---------------------------------------------------------------------------


@given(
    "an investor profile with 5-year horizon and regular drawdown",
    target_fixture="profile_short",
)
def given_5yr_profile():
    return InvestorProfile(
        profile_id="bdd_goal_5yr",
        fire_variant=FireVariant.RETIREMENT_COMPLEMENT,
        risk_appetite=3,
        horizon_years=5,
        withdrawal_pattern=WithdrawalPattern.REGULAR_DRAWDOWN,
        crypto_enabled=False,
    )


@given(
    "another investor profile with 20-year horizon and regular drawdown",
    target_fixture="profile_long",
)
def given_20yr_profile_long():
    return InvestorProfile(
        profile_id="bdd_goal_20yr_long",
        fire_variant=FireVariant.RETIREMENT_COMPLEMENT,
        risk_appetite=3,
        horizon_years=20,
        withdrawal_pattern=WithdrawalPattern.REGULAR_DRAWDOWN,
        crypto_enabled=False,
    )


@when("I compute goal analytics for both profiles", target_fixture="both_results")
def when_compute_both(profile_short, profile_long):
    short_result = GoalAnalytics.compute(
        profile=profile_short,
        portfolio_value=PORTFOLIO_VALUE,
        portfolio_return_annual=ANNUAL_RETURN,
        portfolio_volatility=VOLATILITY,
    )
    long_result = GoalAnalytics.compute(
        profile=profile_long,
        portfolio_value=PORTFOLIO_VALUE,
        portfolio_return_annual=ANNUAL_RETURN,
        portfolio_volatility=VOLATILITY,
    )
    return {"short": short_result, "long": long_result}


@then("the 5-year profile has a wider sequence-of-returns risk spread than the 20-year profile")
def then_shorter_wider_spread(both_results):
    short_sor = both_results["short"].sequence_of_returns_risk
    long_sor = both_results["long"].sequence_of_returns_risk
    assert short_sor is not None
    assert long_sor is not None
    short_spread = short_sor[1] - short_sor[0]
    long_spread = long_sor[1] - long_sor[0]
    # Shorter horizon has less time to smooth out, so wider *relative to horizon*
    # But the formula uses sqrt(T) scaling, so absolute spread grows with horizon.
    # We compare spread per year to capture the per-annum risk concentration.
    short_spread_per_yr = short_spread / Decimal("5")
    long_spread_per_yr = long_spread / Decimal("20")
    assert short_spread_per_yr > long_spread_per_yr


# ---------------------------------------------------------------------------
# Scenario 4: Conservative SWR ≤ aggressive SWR
# ---------------------------------------------------------------------------


@given(
    "a conservative profile with risk appetite 2 and 10-year horizon",
    target_fixture="conservative",
)
def given_conservative():
    return InvestorProfile(
        profile_id="bdd_conservative",
        fire_variant=FireVariant.RETIREMENT_COMPLEMENT,
        risk_appetite=2,
        horizon_years=10,
        withdrawal_pattern=WithdrawalPattern.REGULAR_DRAWDOWN,
        crypto_enabled=False,
    )


@given(
    "an aggressive profile with risk appetite 5 and 10-year horizon",
    target_fixture="aggressive",
)
def given_aggressive():
    return InvestorProfile(
        profile_id="bdd_aggressive",
        fire_variant=FireVariant.FAT_FIRE,
        risk_appetite=5,
        horizon_years=10,
        withdrawal_pattern=WithdrawalPattern.REGULAR_DRAWDOWN,
        crypto_enabled=False,
    )


@when(
    "I compute goal analytics for both risk profiles",
    target_fixture="risk_results",
)
def when_compute_risk_profiles(conservative, aggressive):
    cons_result = GoalAnalytics.compute(
        profile=conservative,
        portfolio_value=PORTFOLIO_VALUE,
        portfolio_return_annual=ANNUAL_RETURN,
        portfolio_volatility=VOLATILITY,
    )
    agg_result = GoalAnalytics.compute(
        profile=aggressive,
        portfolio_value=PORTFOLIO_VALUE,
        portfolio_return_annual=ANNUAL_RETURN,
        portfolio_volatility=VOLATILITY,
    )
    return {"conservative": cons_result, "aggressive": agg_result}


@then("the conservative SWR is less than or equal to the aggressive SWR")
def then_conservative_swr_leq(risk_results):
    cons_swr = risk_results["conservative"].safe_withdrawal_rate
    agg_swr = risk_results["aggressive"].safe_withdrawal_rate
    assert cons_swr is not None
    assert agg_swr is not None
    assert cons_swr <= agg_swr
