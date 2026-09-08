from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from alpha_holdings.allocation import allocate
from alpha_holdings.models import (
    AllocationEntry,
    Company,
    EntryMethod,
    Fundamentals,
    InstrumentType,
    MacroRegime,
    MacroRegimeType,
    MarketCapCategory,
    PortfolioAllocation,
    PortfolioSleeve,
    RiskAppetite,
    RiskProfile,
    SubTheme,
    SupplyChainTier,
    ThemeScore,
    ThemeThesis,
    TimeHorizon,
)


NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)


def _profile(
    appetite: RiskAppetite = RiskAppetite.MODERATE,
) -> RiskProfile:
    return RiskProfile(appetite=appetite, time_horizon=TimeHorizon.MEDIUM)


def _regime(regime: MacroRegimeType = MacroRegimeType.NEUTRAL) -> MacroRegime:
    return MacroRegime(
        regime=regime,
        confidence=7,
        drivers=["Stable growth"],
    )


def _priced_etf(ticker: str, price: float = 100) -> Fundamentals:
    return Fundamentals(
        ticker=ticker,
        provider_symbol=ticker,
        name=f"{ticker} Fund",
        quote_type="ETF",
        source="fixture-market-data",
        current_price=price,
        fetched_at=NOW,
    )


def _priced_stock(ticker: str, price: float = 100) -> Fundamentals:
    return Fundamentals(
        ticker=ticker,
        provider_symbol=ticker,
        name=f"{ticker} Corporation",
        quote_type="EQUITY",
        source="fixture-market-data",
        current_price=price,
        fetched_at=NOW,
    )


def _theme(name: str, ticker: str, score: float = 80) -> tuple[ThemeThesis, ThemeScore]:
    company = Company(
        ticker=ticker,
        name=f"{ticker} Corporation",
        role_in_theme="Supplier",
        rationale="Validated beneficiary",
        market_cap_category=MarketCapCategory.LARGE,
        supply_chain_tier=SupplyChainTier.TIER_2_DIRECT_ENABLER,
        sector="Industrials",
    )
    theme = ThemeThesis(
        name=name,
        thesis_summary="Long-term demand growth.",
        why_now="Investment is accelerating.",
        bull_case="Demand compounds.",
        bear_case="Demand slows.",
        confidence_score=8,
        sub_themes=[
            SubTheme(name="Suppliers", description="Direct suppliers", companies=[company])
        ],
    )
    theme_score = ThemeScore(
        ticker=ticker,
        fundamental_score=score,
        thesis_alignment_score=score,
        pricing_gap_score=score,
        revenue_exposure_score=score,
        composite_score=score,
        entry_method=EntryMethod.DCA,
        alignment_reasoning="Validated alignment.",
        pricing_gap_reasoning="Validated pricing.",
        revenue_exposure_reasoning="Validated exposure.",
        score_as_of=NOW,
        evidence_sources=[f"https://example.com/{ticker.lower()}"],
        scoring_provider="fixture-ai",
        scoring_model="fixture-model",
    )
    return theme, theme_score


def _theme_with_tickers(
    name: str,
    ticker_scores: list[tuple[str, float]],
) -> tuple[ThemeThesis, list[ThemeScore]]:
    fixtures = [_theme(name, ticker, score) for ticker, score in ticker_scores]
    theme = fixtures[0][0]
    theme.sub_themes[0].companies = [
        fixture_theme.all_companies[0] for fixture_theme, _score in fixtures
    ]
    return theme, [score for _theme_fixture, score in fixtures]


def test_no_eligible_theme_preserves_capital_in_the_configured_core() -> None:
    allocation = allocate(
        [],
        {},
        {},
        _profile(),
        _regime(),
        fund_data={"VT": _priced_etf("VT", 125)},
        capital=10_000,
        clock=lambda: NOW,
    )

    assert allocation.core_pct == 100
    assert allocation.defensive_pct == 0
    assert allocation.cash_pct == 0
    assert len(allocation.positions) == 1
    position = allocation.positions[0]
    assert position.ticker == "VT"
    assert position.instrument_type is InstrumentType.ETF
    assert position.sleeve is PortfolioSleeve.CORE
    assert position.weight_pct == 100
    assert position.entry_price == 125
    assert position.price_timestamp == NOW
    assert allocation.capital == 10_000


def test_allocation_requires_the_profile_minimum_number_of_funded_themes() -> None:
    first, first_score = _theme("Grid", "GRID")
    second, second_score = _theme("Water", "WATR")

    allocation = allocate(
        [first, second],
        {"Grid": [first_score], "Water": [second_score]},
        {},
        _profile(RiskAppetite.MODERATE),
        _regime(),
        fund_data={
            "VT": _priced_etf("VT"),
            "GRID": _priced_stock("GRID"),
            "WATR": _priced_stock("WATR"),
        },
        clock=lambda: NOW,
    )

    assert [(position.ticker, position.weight_pct) for position in allocation.positions] == [
        ("VT", 100)
    ]
    assert allocation.residual_reason == (
        "2 validated themes is below the moderate minimum of 3; 32.0% moved to core"
    )


def test_allocation_totals_one_hundred_and_routes_company_cap_residual_to_core() -> None:
    fixtures = [
        _theme("Grid", "GRID", 90),
        _theme("Water", "WATR", 80),
        _theme("Robotics", "ROBO", 70),
    ]
    themes = [theme for theme, _score in fixtures]
    scores = {theme.name: [score] for theme, score in fixtures}

    allocation = allocate(
        themes,
        scores,
        {},
        _profile(RiskAppetite.MODERATE),
        _regime(),
        fund_data={
            "VT": _priced_etf("VT"),
            "GRID": _priced_stock("GRID", 50),
            "WATR": _priced_stock("WATR", 60),
            "ROBO": _priced_stock("ROBO", 70),
        },
        capital=10_000,
        clock=lambda: NOW,
    )

    weights = {position.ticker: position.weight_pct for position in allocation.positions}
    assert weights == {"GRID": 8.0, "ROBO": 8.0, "VT": 76.0, "WATR": 8.0}
    assert sum(weights.values()) == 100
    assert allocation.core_pct == 76
    assert allocation.defensive_pct == 0
    assert allocation.cash_pct == 0
    assert allocation.effective_allocation_modifier == 0.8
    assert allocation.residual_reason == "8.0% exceeded thematic capacity and moved to core"
    assert all(position.ticker not in {theme.name for theme in themes} for position in allocation.positions)
    assert {
        position.ticker: position.capital_amount for position in allocation.positions
    } == {"GRID": 800.0, "ROBO": 800.0, "VT": 7_600.0, "WATR": 800.0}


def test_portfolio_allocation_rejects_a_position_total_outside_tolerance() -> None:
    position = allocate(
        [],
        {},
        {},
        _profile(),
        _regime(),
        fund_data={"VT": _priced_etf("VT")},
        clock=lambda: NOW,
    ).positions[0].model_copy(update={"weight_pct": 99.8})

    with pytest.raises(ValidationError, match="positions must total 100%"):
        PortfolioAllocation(
            risk_profile=_profile(),
            macro_regime=_regime(),
            positions=[position],
            core_pct=99.8,
        )


def test_duplicate_company_exposure_across_themes_shares_one_portfolio_cap() -> None:
    grid, grid_score = _theme("Grid", "SHARED", 90)
    water, water_score = _theme("Water", "SHARED", 80)
    robotics, robotics_score = _theme("Robotics", "ROBO", 70)

    allocation = allocate(
        [grid, water, robotics],
        {
            "Grid": [grid_score],
            "Water": [water_score],
            "Robotics": [robotics_score],
        },
        {},
        _profile(RiskAppetite.MODERATE),
        _regime(),
        fund_data={
            "VT": _priced_etf("VT"),
            "SHARED": _priced_stock("SHARED"),
            "ROBO": _priced_stock("ROBO"),
        },
        clock=lambda: NOW,
    )

    weights = {position.ticker: position.weight_pct for position in allocation.positions}
    assert weights == {"ROBO": 8.0, "SHARED": 8.0, "VT": 84.0}
    assert [position.ticker for position in allocation.positions].count("SHARED") == 1


def test_bear_regime_uses_persisted_deterministic_sleeve_policy() -> None:
    fixtures = [
        _theme("Grid", "GRID", 90),
        _theme("Water", "WATR", 80),
        _theme("Robotics", "ROBO", 70),
    ]
    themes = [theme for theme, _score in fixtures]
    scores = {theme.name: [score] for theme, score in fixtures}
    regime = MacroRegime(
        regime=MacroRegimeType.BEAR,
        confidence=9,
        drivers=["Contraction"],
        allocation_modifier=0.0,
    )

    allocation = allocate(
        themes,
        scores,
        {},
        _profile(RiskAppetite.MODERATE),
        regime,
        fund_data={
            "VT": _priced_etf("VT"),
            "BND": _priced_etf("BND", 75),
            "GRID": _priced_stock("GRID"),
            "WATR": _priced_stock("WATR"),
            "ROBO": _priced_stock("ROBO"),
        },
        clock=lambda: NOW,
    )

    assert allocation.effective_allocation_modifier == 0.5
    assert sum(
        position.weight_pct
        for position in allocation.positions
        if position.sleeve is PortfolioSleeve.THEMATIC
    ) == 20
    assert allocation.defensive_pct == 16
    assert allocation.core_pct == 64
    assert allocation.cash_pct == 0
    assert sum(position.weight_pct for position in allocation.positions) == 100


def test_theme_caps_and_overlap_penalties_redistribute_the_full_eligible_budget() -> None:
    grid, grid_scores = _theme_with_tickers(
        "Grid", [("GRID1", 100), ("GRID2", 95)]
    )
    water, water_scores = _theme_with_tickers(
        "Water", [("WATR1", 70), ("WATR2", 65)]
    )
    robotics, robotics_scores = _theme_with_tickers(
        "Robotics", [("ROBO1", 60), ("ROBO2", 55)]
    )
    fund_data = {"VT": _priced_etf("VT")}
    for ticker in ("GRID1", "GRID2", "WATR1", "WATR2", "ROBO1", "ROBO2"):
        fund_data[ticker] = _priced_stock(ticker)

    allocation = allocate(
        [grid, water, robotics],
        {
            "Grid": grid_scores,
            "Water": water_scores,
            "Robotics": robotics_scores,
        },
        {},
        _profile(RiskAppetite.MODERATE),
        _regime(),
        fund_data=fund_data,
        clock=lambda: NOW,
    )

    assert sum(entry.pct_allocation for entry in allocation.entries) == 32
    assert all(entry.pct_allocation <= 15 for entry in allocation.entries)
    assert allocation.core_pct == 68
    assert allocation.residual_reason is None


def test_unavailable_core_preserves_all_capital_as_an_explicit_cash_position() -> None:
    allocation = allocate(
        [],
        {},
        {},
        _profile(),
        _regime(),
        fund_data={},
        capital=5_000,
        clock=lambda: NOW,
        base_currency="EUR",
    )

    assert allocation.core_pct == 0
    assert allocation.cash_pct == 100
    assert [(position.ticker, position.sleeve, position.weight_pct) for position in allocation.positions] == [
        ("CASH", PortfolioSleeve.CASH, 100)
    ]
    assert allocation.positions[0].currency == "EUR"
    assert "configured core was unavailable" in allocation.residual_reason


def test_legacy_entries_must_match_authoritative_thematic_positions() -> None:
    core_position = allocate(
        [],
        {},
        {},
        _profile(),
        _regime(),
        fund_data={"VT": _priced_etf("VT")},
        clock=lambda: NOW,
    ).positions[0]

    with pytest.raises(ValidationError, match="entries must match thematic positions"):
        PortfolioAllocation(
            risk_profile=_profile(),
            macro_regime=_regime(),
            positions=[core_position],
            entries=[
                AllocationEntry(
                    theme="Grid",
                    vehicle="GRID",
                    vehicle_type="stocks",
                    pct_allocation=10,
                    entry_method=EntryMethod.DCA,
                    rationale="Invalid legacy projection",
                )
            ],
            core_pct=100,
        )


def test_minimum_eligible_themes_each_receive_weight_before_conviction_weighting() -> None:
    grid, grid_score = _theme("Grid", "SHARED", 100)
    water, water_score = _theme("Water", "SHARED", 1)
    robotics, robotics_score = _theme("Robotics", "SHARED", 1)

    allocation = allocate(
        [grid, water, robotics],
        {
            "Grid": [grid_score],
            "Water": [water_score],
            "Robotics": [robotics_score],
        },
        {},
        _profile(RiskAppetite.MODERATE),
        _regime(),
        fund_data={
            "VT": _priced_etf("VT"),
            "SHARED": _priced_stock("SHARED"),
        },
        clock=lambda: NOW,
    )

    assert {entry.theme for entry in allocation.entries} == {
        "Grid",
        "Robotics",
        "Water",
    }
    assert all(entry.pct_allocation > 0 for entry in allocation.entries)
    assert next(
        position.weight_pct
        for position in allocation.positions
        if position.ticker == "SHARED"
    ) == 8


def test_unavailable_non_thematic_instrument_explains_weight_moved_to_cash() -> None:
    grid, grid_scores = _theme_with_tickers(
        "Grid", [("GRID1", 90), ("GRID2", 85)]
    )
    water, water_scores = _theme_with_tickers(
        "Water", [("WATR1", 80), ("WATR2", 75)]
    )
    robotics, robotics_scores = _theme_with_tickers(
        "Robotics", [("ROBO1", 70), ("ROBO2", 65)]
    )
    fund_data = {
        ticker: _priced_stock(ticker)
        for ticker in ("GRID1", "GRID2", "WATR1", "WATR2", "ROBO1", "ROBO2")
    }

    allocation = allocate(
        [grid, water, robotics],
        {
            "Grid": grid_scores,
            "Water": water_scores,
            "Robotics": robotics_scores,
        },
        {},
        _profile(RiskAppetite.MODERATE),
        _regime(),
        fund_data=fund_data,
        clock=lambda: NOW,
    )

    assert allocation.cash_pct == 68
    assert allocation.residual_reason == (
        "Configured core VT was unavailable; 68.0% moved to cash"
    )


def test_position_amount_rounding_preserves_every_cent_of_capital() -> None:
    fixtures = [
        _theme("Grid", "GRID"),
        _theme("Water", "WATR"),
        _theme("Robotics", "ROBO"),
        _theme("Storage", "STOR"),
    ]
    allocation = allocate(
        [theme for theme, _score in fixtures],
        {theme.name: [score] for theme, score in fixtures},
        {},
        _profile(RiskAppetite.MODERATE),
        _regime(),
        fund_data={
            "VT": _priced_etf("VT"),
            **{
                ticker: _priced_stock(ticker)
                for ticker in ("GRID", "WATR", "ROBO", "STOR")
            },
        },
        capital=200.02,
        clock=lambda: NOW,
    )

    assert sum(
        position.capital_amount or 0 for position in allocation.positions
    ) == 200.02


def test_distinct_listings_of_one_issuer_share_the_company_cap() -> None:
    primary, primary_score = _theme("Grid", "SHRA", 90)
    secondary, secondary_score = _theme("Water", "SHRB", 80)
    robotics, robotics_score = _theme("Robotics", "ROBO", 70)
    primary.all_companies[0].name = "Taiwan Semiconductor Manufacturing"
    secondary.all_companies[0].name = (
        "Taiwan Semiconductor Manufacturing Sponsored ADR"
    )

    allocation = allocate(
        [primary, secondary, robotics],
        {
            "Grid": [primary_score],
            "Water": [secondary_score],
            "Robotics": [robotics_score],
        },
        {},
        _profile(RiskAppetite.MODERATE),
        _regime(),
        fund_data={
            "VT": _priced_etf("VT"),
            "SHRA": _priced_stock("SHRA").model_copy(
                update={"name": "Taiwan Semiconductor Manufacturing Company Limited"}
            ),
            "SHRB": _priced_stock("SHRB").model_copy(
                update={
                    "name": (
                        "Taiwan Semiconductor Manufacturing Company Limited "
                        "Sponsored ADR"
                    )
                }
            ),
            "ROBO": _priced_stock("ROBO"),
        },
        clock=lambda: NOW,
    )

    shared_exposure = sum(
        position.weight_pct
        for position in allocation.positions
        if position.ticker in {"SHRA", "SHRB"}
    )
    assert shared_exposure == 8


def test_transitively_matching_issuer_aliases_share_one_company_cap() -> None:
    primary, primary_score = _theme("Grid", "PRIM", 90)
    bridge, bridge_score = _theme("Water", "BRDG", 80)
    secondary, secondary_score = _theme("Robotics", "SECD", 70)
    primary.all_companies[0].name = "Primary Manufacturing"
    primary.all_companies[0].issuer_id = "issuer-one"
    bridge.all_companies[0].name = "Bridge Technologies"
    bridge.all_companies[0].issuer_id = "issuer-one"
    secondary.all_companies[0].name = "Bridge Technologies"
    secondary.all_companies[0].issuer_id = "issuer-two"

    allocation = allocate(
        [primary, bridge, secondary],
        {
            "Grid": [primary_score],
            "Water": [bridge_score],
            "Robotics": [secondary_score],
        },
        {},
        _profile(RiskAppetite.MODERATE),
        _regime(),
        fund_data={
            "VT": _priced_etf("VT"),
            "PRIM": _priced_stock("PRIM").model_copy(
                update={"name": "Primary Manufacturing Corporation"}
            ),
            "BRDG": _priced_stock("BRDG").model_copy(
                update={"name": "Bridge Technologies Corporation"}
            ),
            "SECD": _priced_stock("SECD").model_copy(
                update={"name": "Bridge Technologies Sponsored ADR"}
            ),
        },
        clock=lambda: NOW,
    )

    connected_exposure = sum(
        position.weight_pct
        for position in allocation.positions
        if position.ticker in {"PRIM", "BRDG", "SECD"}
    )
    assert connected_exposure == 8
