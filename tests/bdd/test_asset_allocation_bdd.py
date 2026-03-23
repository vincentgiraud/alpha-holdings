"""BDD scenarios for asset allocation behavior."""

from decimal import Decimal

from pytest_bdd import given, scenarios, then, when

from alpha_holdings.domain import FireVariant, InvestorProfile, WithdrawalPattern
from alpha_holdings.portfolio.asset_allocation import AssetAllocator

scenarios("features/asset_allocation.feature")


@given(
    'an investor profile with fire variant "fat_fire", risk appetite 3, horizon 20 years, '
    'withdrawal pattern "regular_drawdown", and crypto enabled true',
    target_fixture="profile",
)
def given_investor_profile() -> InvestorProfile:
    """Create a profile where crypto is enabled but risk is too low for crypto sleeve."""
    return InvestorProfile(
        profile_id="bdd_profile_001",
        fire_variant=FireVariant.FAT_FIRE,
        risk_appetite=3,
        horizon_years=20,
        withdrawal_pattern=WithdrawalPattern.REGULAR_DRAWDOWN,
        crypto_enabled=True,
    )


@given(
    'an investor profile with fire variant "fat_fire", risk appetite 4, horizon 20 years, '
    'withdrawal pattern "regular_drawdown", and crypto enabled true',
    target_fixture="profile",
)
def given_high_risk_crypto_profile() -> InvestorProfile:
    """Create a profile where crypto should be included by policy."""
    return InvestorProfile(
        profile_id="bdd_profile_002",
        fire_variant=FireVariant.FAT_FIRE,
        risk_appetite=4,
        horizon_years=20,
        withdrawal_pattern=WithdrawalPattern.REGULAR_DRAWDOWN,
        crypto_enabled=True,
    )


@given(
    'two investor profiles with identical fire variant "fat_fire", risk appetite 4, '
    'withdrawal pattern "regular_drawdown", and crypto enabled false',
    target_fixture="profile_pair",
)
def given_profile_pair() -> dict[str, InvestorProfile]:
    """Create two similar profiles to compare horizon-driven bond allocation."""
    return {
        "first": InvestorProfile(
            profile_id="bdd_profile_long",
            fire_variant=FireVariant.FAT_FIRE,
            risk_appetite=4,
            horizon_years=20,
            withdrawal_pattern=WithdrawalPattern.REGULAR_DRAWDOWN,
            crypto_enabled=False,
        ),
        "second": InvestorProfile(
            profile_id="bdd_profile_short",
            fire_variant=FireVariant.FAT_FIRE,
            risk_appetite=4,
            horizon_years=20,
            withdrawal_pattern=WithdrawalPattern.REGULAR_DRAWDOWN,
            crypto_enabled=False,
        ),
    }


@given("the first profile has horizon 20 years")
def given_first_horizon(profile_pair: dict[str, InvestorProfile]) -> None:
    """Pin the long-horizon profile to 20 years."""
    profile_pair["first"].horizon_years = 20


@given("the second profile has horizon 5 years")
def given_second_horizon(profile_pair: dict[str, InvestorProfile]) -> None:
    """Pin the short-horizon profile to 5 years."""
    profile_pair["second"].horizon_years = 5


@when("I compute asset allocation", target_fixture="allocation")
def when_compute_asset_allocation(profile: InvestorProfile):
    """Run allocation for the scenario profile."""
    return AssetAllocator.allocate(profile)


@then('the allocation includes "equity" and "bond" sleeves')
def then_includes_core_sleeves(allocation):
    """Ensure core sleeves are always present."""
    asset_types = {sleeve.asset_class.value for sleeve in allocation.asset_classes}
    assert "equity" in asset_types
    assert "bond" in asset_types


@then('the allocation excludes "crypto" sleeve')
def then_excludes_crypto(allocation):
    """Ensure crypto is absent when risk gate fails."""
    asset_types = {sleeve.asset_class.value for sleeve in allocation.asset_classes}
    assert "crypto" not in asset_types


@then('the allocation includes "crypto" sleeve')
def then_includes_crypto(allocation):
    """Ensure crypto is present when risk and flag gates both pass."""
    asset_types = {sleeve.asset_class.value for sleeve in allocation.asset_classes}
    assert "crypto" in asset_types


@then("the target weights sum to 1.0 within tolerance 0.01")
def then_weights_sum_to_one(allocation):
    """Ensure weights remain normalized after gating logic."""
    total_target = sum(sleeve.target_weight for sleeve in allocation.asset_classes)
    assert Decimal("0.99") <= total_target <= Decimal("1.01")


@when("I compute both asset allocations", target_fixture="allocation_pair")
def when_compute_both_asset_allocations(profile_pair: dict[str, InvestorProfile]):
    """Run allocation for both profiles used in comparison scenario."""
    return {
        "first": AssetAllocator.allocate(profile_pair["first"]),
        "second": AssetAllocator.allocate(profile_pair["second"]),
    }


@then("the second profile bond target weight is greater than the first profile bond target weight")
def then_second_has_higher_bond_weight(allocation_pair) -> None:
    """Verify shorter horizon shifts allocation toward bonds."""

    def bond_weight(allocation) -> Decimal:
        for sleeve in allocation.asset_classes:
            if sleeve.asset_class.value == "bond":
                return sleeve.target_weight
        raise AssertionError("Bond sleeve missing from allocation")

    first_bond = bond_weight(allocation_pair["first"])
    second_bond = bond_weight(allocation_pair["second"])

    assert second_bond > first_bond
