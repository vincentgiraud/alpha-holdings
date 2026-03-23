"""Investor profile models and constraint resolution for portfolio construction.

The InvestorProfile encodes investor demographics, goals, and preferences.
The ProfileToConstraints resolver maps profiles to concrete portfolio engine defaults.
"""

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


class FireVariant(StrEnum):
    """FIRE (Financial Independence, Retire Early) variants."""

    FAT_FIRE = "fat_fire"  # High spending target, aggressive returns needed
    LEAN_FIRE = "lean_fire"  # Minimal spending target, lower return needs
    BARISTA_FIRE = "barista_fire"  # Part-time income covers living expenses
    COAST_FIRE = "coast_fire"  # Stop saving but continue working; portfolio compounds
    RETIREMENT_COMPLEMENT = "retirement_complement"  # Supplement existing retirement income


class WithdrawalPattern(StrEnum):
    """Portfolio withdrawal/spending pattern."""

    LUMP_SUM = "lump_sum"  # Single large withdrawal at target date
    REGULAR_DRAWDOWN = "regular_drawdown"  # Monthly/quarterly withdrawals (systematic)
    COMPOUND_ONLY = "compound_only"  # No withdrawals; accumulation phase only


class AssetClass(StrEnum):
    """Top-level asset classes for multi-asset allocation."""

    EQUITY = "equity"
    BOND = "bond"
    CRYPTO = "crypto"


class InvestorProfile(BaseModel):
    """Investor profile containing demographics, goals, and preferences.

    This model encodes the investor's time horizon, risk tolerance, income needs,
    and crypto preferences. It is the primary input to the AssetAllocator and
    ProfileToConstraints resolver.
    """

    profile_id: str = Field(..., description="Unique identifier for this profile")
    fire_variant: FireVariant = Field(..., description="FIRE variant / goal type")
    risk_appetite: int = Field(
        ..., description="Risk appetite on 1-5 scale (1=very conservative, 5=very aggressive)"
    )
    horizon_years: int = Field(
        ..., description="Time horizon until first withdrawal or target date (years)"
    )
    withdrawal_pattern: WithdrawalPattern = Field(..., description="How portfolio will be accessed")
    target_real_return_pct: float | None = Field(
        None, description="Annual real (inflation-adjusted) return target (%)"
    )
    crypto_enabled: bool = Field(default=False, description="Whether crypto sleeve is permitted")
    name: str | None = Field(None, description="Human-readable profile name")

    @field_validator("risk_appetite")
    @classmethod
    def validate_risk_appetite(cls, v: int) -> int:
        """Ensure risk_appetite is between 1 and 5."""
        if not 1 <= v <= 5:
            raise ValueError("risk_appetite must be between 1 and 5")
        return v

    @field_validator("horizon_years")
    @classmethod
    def validate_horizon(cls, v: int) -> int:
        """Ensure horizon is positive."""
        if v <= 0:
            raise ValueError("horizon_years must be positive")
        return v

    model_config = ConfigDict(arbitrary_types_allowed=True)


class AssetClassAllocation(BaseModel):
    """Target weight bands for each asset class sleeve."""

    asset_class: AssetClass = Field(..., description="Asset class (equity, bond, crypto)")
    min_weight: Decimal = Field(..., description="Minimum target weight (0.0 to 1.0)")
    target_weight: Decimal = Field(..., description="Target weight (0.0 to 1.0)")
    max_weight: Decimal = Field(..., description="Maximum target weight (0.0 to 1.0)")

    @field_validator("min_weight", "target_weight", "max_weight")
    @classmethod
    def validate_weights(cls, v: Decimal) -> Decimal:
        """Ensure weights are between 0 and 1."""
        if not Decimal(0) <= v <= Decimal(1):
            raise ValueError("Weights must be between 0 and 1")
        return v

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @field_serializer("min_weight", "target_weight", "max_weight")
    def serialize_decimal_fields(self, value: Decimal) -> str:
        return str(value)


class PortfolioConstraints(BaseModel):
    """Concrete portfolio construction constraints resolved from InvestorProfile."""

    profile_id: str = Field(..., description="Source profile ID")
    max_single_name_weight: Decimal = Field(
        ..., description="Max weight for any single security (0.0 to 1.0)"
    )
    sector_deviation_band: Decimal = Field(
        ..., description="±sector deviation vs. benchmark (e.g., 0.05 = ±5pp)"
    )
    country_deviation_band: Decimal = Field(
        ..., description="±country deviation vs. benchmark (e.g., 0.05 = ±5pp)"
    )
    max_annual_turnover: Decimal = Field(..., description="Annual turnover cap (e.g., 0.50 = 50%)")
    min_holdings_count: int = Field(..., description="Minimum number of holdings in portfolio")
    max_portfolio_volatility: Decimal | None = Field(
        None, description="Target portfolio volatility (std dev)"
    )
    max_drawdown_tolerance: Decimal | None = Field(None, description="Maximum acceptable drawdown")
    rebalance_cadence: str = Field(
        default="monthly", description="Rebalance frequency (daily, weekly, monthly, quarterly)"
    )
    scoring_factor_holding_period_bias: str | None = Field(
        None, description="Bias score toward long-term or short-term factors"
    )

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @field_serializer(
        "max_single_name_weight",
        "sector_deviation_band",
        "country_deviation_band",
        "max_annual_turnover",
        "max_portfolio_volatility",
        "max_drawdown_tolerance",
    )
    def serialize_decimal_fields(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)


class AssetAllocatorResult(BaseModel):
    """Output of AssetAllocator: sleeve weight bands for construction."""

    profile_id: str = Field(..., description="Source profile ID")
    asset_classes: list[AssetClassAllocation] = Field(
        ..., description="Allocation bands per asset class"
    )
    description: str | None = Field(None, description="Allocation rationale")

    model_config = ConfigDict(arbitrary_types_allowed=True)


class ProfileToConstraints:
    """Resolver that maps InvestorProfile to PortfolioConstraints.

    This class encodes the business logic that translates investor preferences
    (FIRE variant, risk appetite, horizon) into concrete portfolio constraints.
    """

    # Default constraint values (can be overridden per profile)
    DEFAULT_MAX_SINGLE_NAME_WEIGHT = Decimal("0.05")  # 5%
    DEFAULT_SECTOR_DEVIATION = Decimal("0.05")  # ±5pp
    DEFAULT_COUNTRY_DEVIATION = Decimal("0.05")  # ±5pp
    DEFAULT_MAX_ANNUAL_TURNOVER = Decimal("0.50")  # 50%
    DEFAULT_MIN_HOLDINGS = 30
    DEFAULT_MAX_PORTFOLIO_VOLATILITY = Decimal("0.15")  # 15%

    @staticmethod
    def resolve(profile: InvestorProfile) -> PortfolioConstraints:
        """Map InvestorProfile to PortfolioConstraints.

        Rules:
        - Conservative profiles (low risk_appetite, short horizon) tighten turnover,
          widen deviation bands to protect against forced trading.
        - Coast-fire with long horizons loosen turnover (can rebalance less frequently).
        - Retirement-complement and near-withdrawal profiles tighten max drawdown.
        - Rebalance cadence depends on withdrawal pattern and horizon.
        """
        base = ProfileToConstraints.DEFAULT_MAX_SINGLE_NAME_WEIGHT
        sector_dev = ProfileToConstraints.DEFAULT_SECTOR_DEVIATION
        country_dev = ProfileToConstraints.DEFAULT_COUNTRY_DEVIATION
        max_turnover = ProfileToConstraints.DEFAULT_MAX_ANNUAL_TURNOVER
        min_holdings = ProfileToConstraints.DEFAULT_MIN_HOLDINGS
        max_vol = ProfileToConstraints.DEFAULT_MAX_PORTFOLIO_VOLATILITY
        max_dd = None
        rebalance_cadence = "monthly"
        score_bias = None

        # Tighten constraints for conservative profiles (low risk appetite)
        if profile.risk_appetite <= 2:
            max_turnover = Decimal("0.30")  # 30% annual turnover
            min_holdings = 40
            max_vol = Decimal("0.10")  # 10% volatility
            max_dd = Decimal("0.15")  # 15% max drawdown
            score_bias = "long_term"

        # Loosen constraints for coast-fire with long horizons
        if profile.fire_variant == FireVariant.COAST_FIRE and profile.horizon_years >= 15:
            max_turnover = Decimal("0.30")
            sector_dev = Decimal("0.07")  # ±7pp
            country_dev = Decimal("0.07")
            rebalance_cadence = "quarterly"

        # Tighten constraints for retirement-complement and near withdrawal
        if profile.fire_variant == FireVariant.RETIREMENT_COMPLEMENT or (
            profile.withdrawal_pattern == WithdrawalPattern.REGULAR_DRAWDOWN
            and profile.horizon_years < 5
        ):
            max_dd = Decimal("0.10")  # 10% max drawdown
            min_holdings = 50
            rebalance_cadence = "monthly"

        return PortfolioConstraints(
            profile_id=profile.profile_id,
            max_single_name_weight=base,
            sector_deviation_band=sector_dev,
            country_deviation_band=country_dev,
            max_annual_turnover=max_turnover,
            min_holdings_count=min_holdings,
            max_portfolio_volatility=max_vol,
            max_drawdown_tolerance=max_dd,
            rebalance_cadence=rebalance_cadence,
            scoring_factor_holding_period_bias=score_bias,
        )
