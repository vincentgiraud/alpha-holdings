"""Asset allocation resolver: derives portfolio sleeve weights from InvestorProfile.

The AssetAllocator is a two-tier system:
  Tier 1: Profile -> Asset class allocation (equity, bond, crypto bands)
  Tier 2: Per-sleeve security selection (independent for each sleeve)

This separation allows bond and crypto sleeves to evolve independently of equity.
"""

from decimal import Decimal

from alpha_holdings.domain.investor_profile import (
    AssetAllocatorResult,
    AssetClass,
    AssetClassAllocation,
    FireVariant,
    InvestorProfile,
)


class AssetAllocator:
    """Derives target equity/bond/crypto allocation bands from InvestorProfile.

    Allocation examples from the plan:
    - fat_fire 20yr risk 4-5 -> 85% equity / 12% bond / 3% crypto
    - lean_fire 10yr risk 3 -> 80% equity / 20% bond / 0% crypto
    - retirement_complement 5yr risk 2 -> 50% equity / 45% bond / 5% optional crypto

    Bond sleeve is always present. Crypto is opt-in only when crypto_enabled=true
    and risk_appetite >= 4.
    """

    @staticmethod
    def allocate(profile: InvestorProfile) -> AssetAllocatorResult:
        """Compute asset class allocation bands from profile.

        Args:
            profile: InvestorProfile with demographic and preference data.

        Returns:
            AssetAllocatorResult with equity, bond, crypto weight bands.
        """
        allocations = []

        # Determine equity and bond split based on horizon, risk, and FIRE variant
        equity_target, bond_target, crypto_target = AssetAllocator._compute_sleeve_targets(profile)

        # Equity sleeve (or crypto may displace some equity if present)
        equity_min = max(Decimal("0.50"), equity_target - Decimal("0.10"))
        equity_max = min(Decimal("1.00"), equity_target + Decimal("0.10"))
        allocations.append(
            AssetClassAllocation(
                asset_class=AssetClass.EQUITY,
                min_weight=equity_min,
                target_weight=equity_target,
                max_weight=equity_max,
            )
        )

        # Bond sleeve (always present, weight floor rises as horizon shrinks)
        allocations.append(
            AssetClassAllocation(
                asset_class=AssetClass.BOND,
                min_weight=bond_target - Decimal("0.05"),
                target_weight=bond_target,
                max_weight=bond_target + Decimal("0.10"),
            )
        )

        # Crypto sleeve (opt-in, only if enabled and risk_appetite >= 4)
        if profile.crypto_enabled and profile.risk_appetite >= 4 and crypto_target > 0:
            allocations.append(
                AssetClassAllocation(
                    asset_class=AssetClass.CRYPTO,
                    min_weight=Decimal("0"),
                    target_weight=crypto_target,
                    max_weight=crypto_target,
                )
            )

        description = (
            f"{profile.fire_variant} | {profile.horizon_years}yr | "
            f"risk {profile.risk_appetite} | "
            f"equity {equity_target:.1%} | bond {bond_target:.1%} | crypto {crypto_target:.1%}"
        )

        return AssetAllocatorResult(
            profile_id=profile.profile_id, asset_classes=allocations, description=description
        )

    @staticmethod
    def _compute_sleeve_targets(profile: InvestorProfile) -> tuple[Decimal, Decimal, Decimal]:
        """Compute target weights for equity, bond, crypto based on profile.

        Returns:
            Tuple of (equity_target, bond_target, crypto_target) as Decimals in [0, 1].
        """
        # Start with base equity/bond split based on risk appetite
        if profile.risk_appetite >= 5:
            base_equity = Decimal("0.90")
            base_bond = Decimal("0.10")
        elif profile.risk_appetite == 4:
            base_equity = Decimal("0.80")
            base_bond = Decimal("0.20")
        elif profile.risk_appetite == 3:
            base_equity = Decimal("0.70")
            base_bond = Decimal("0.30")
        elif profile.risk_appetite == 2:
            base_equity = Decimal("0.50")
            base_bond = Decimal("0.50")
        else:  # risk_appetite == 1
            base_equity = Decimal("0.30")
            base_bond = Decimal("0.70")

        # Adjust for time horizon: shorter horizons → more bonds
        if profile.horizon_years < 5:
            bond_adjustment = Decimal("0.15")
            base_equity -= bond_adjustment
            base_bond += bond_adjustment
        elif profile.horizon_years < 10:
            bond_adjustment = Decimal("0.05")
            base_equity -= bond_adjustment
            base_bond += bond_adjustment

        # Special handling for FIRE variants
        if profile.fire_variant == FireVariant.LEAN_FIRE:
            # Lean-fire needs to generate returns with less volatility
            base_equity = max(base_equity - Decimal("0.10"), Decimal("0.50"))
            base_bond = min(base_bond + Decimal("0.10"), Decimal("0.50"))

        elif profile.fire_variant == FireVariant.RETIREMENT_COMPLEMENT:
            # Already has other income; de-risk further
            base_equity = max(base_equity - Decimal("0.15"), Decimal("0.30"))
            base_bond = min(base_bond + Decimal("0.15"), Decimal("0.70"))

        elif profile.fire_variant == FireVariant.BARISTA_FIRE:
            # Part-time income covers spending; can take more growth risk
            if profile.risk_appetite >= 3:
                base_equity = min(base_equity + Decimal("0.05"), Decimal("0.95"))
                base_bond = max(base_bond - Decimal("0.05"), Decimal("0.05"))

        # Crypto allocation (opt-in)
        crypto_target = Decimal("0")
        if profile.crypto_enabled and profile.risk_appetite >= 4:
            if profile.risk_appetite == 5:
                crypto_target = Decimal("0.10")
            elif profile.risk_appetite == 4:
                crypto_target = Decimal("0.05")

            # Scale back equity/bond to make room for crypto
            scale_factor = Decimal("1.0") - crypto_target
            base_equity = base_equity * scale_factor
            base_bond = base_bond * scale_factor

        # Ensure they sum to 1.0
        total = base_equity + base_bond + crypto_target
        if total > Decimal("1.0"):
            base_bond = Decimal("1.0") - base_equity - crypto_target

        return base_equity, base_bond, crypto_target
