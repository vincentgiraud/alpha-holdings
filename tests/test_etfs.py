from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from alpha_holdings import etfs as etfs_module
from alpha_holdings import llm
from alpha_holdings.etfs import ETFMarketEvidence, fetch_validated_etf, find_etf
from alpha_holdings.models import (
    AllocationEntry,
    Company,
    EntryMethod,
    ETFRecommendationType,
    Fundamentals,
    FundamentalsResult,
    InstrumentPosition,
    InstrumentType,
    MacroRegime,
    MacroRegimeType,
    MarketCapCategory,
    MarketDataStatus,
    PortfolioAllocation,
    PortfolioSleeve,
    RiskAppetite,
    RiskProfile,
    SubTheme,
    SupplyChainTier,
    ThemeThesis,
    TimeHorizon,
)
from alpha_holdings.snapshots import build_discovery_snapshot


NOW = datetime(2026, 9, 8, 16, tzinfo=UTC)


def _theme() -> ThemeThesis:
    companies = [
        Company(
            ticker=ticker,
            name=name,
            role_in_theme="Grid supplier",
            rationale="Direct beneficiary",
            market_cap_category=MarketCapCategory.LARGE,
            supply_chain_tier=SupplyChainTier.TIER_2_DIRECT_ENABLER,
            sector="Industrials",
        )
        for ticker, name in (("ACME", "Acme Energy"), ("BETA", "Beta Grid"))
    ]
    return ThemeThesis(
        name="Grid Modernization",
        thesis_summary="Grid spending is accelerating.",
        why_now="Utilities are raising capital budgets.",
        bull_case="Investment compounds.",
        bear_case="Projects are delayed.",
        confidence_score=8,
        sub_themes=[
            SubTheme(
                name="Equipment",
                description="Grid equipment suppliers",
                companies=companies,
            )
        ],
    )


def _evidence(
    ticker: str,
    *,
    provider_symbol: str | None = None,
    holdings: dict[str, float] | None = None,
) -> ETFMarketEvidence:
    return ETFMarketEvidence(
        ticker=ticker,
        provider_symbol=provider_symbol or ticker,
        name=f"{ticker} Fund",
        quote_type="ETF",
        average_daily_volume=100_000,
        total_assets=500_000_000,
        expense_ratio=0.004,
        adjusted_close=50,
        price_as_of=NOW,
        source="fixture-etf-provider",
        holdings=holdings or {"OTHER": 100},
    )


def test_find_etf_evaluates_every_candidate_and_selects_the_best_valid_fund(
    monkeypatch,
) -> None:
    candidates = [
        {"etf_ticker": "BAD", "etf_name": "Bad Fund", "reasoning": "First"},
        {"etf_ticker": "GRID", "etf_name": "Grid Fund", "reasoning": "Second"},
        {"etf_ticker": "WIDE", "etf_name": "Wide Fund", "reasoning": "Third"},
    ]
    monkeypatch.setattr(llm, "respond_text", lambda *_args, **_kwargs: json.dumps(candidates))

    class FakeProvider:
        def __init__(self) -> None:
            self.requested: list[str] = []

        def fetch(self, ticker: str) -> ETFMarketEvidence:
            self.requested.append(ticker)
            if ticker == "BAD":
                return _evidence("BAD", provider_symbol="OTHER")
            if ticker == "GRID":
                return _evidence("GRID", holdings={"ACME": 60, "OTHER": 40})
            return _evidence("WIDE", holdings={"OTHER": 100})

    provider = FakeProvider()

    recommendation = find_etf(_theme(), provider=provider)

    assert provider.requested == ["BAD", "GRID", "WIDE"]
    assert recommendation.etf_ticker == "GRID"
    assert recommendation.recommendation is ETFRecommendationType.ETF_SUFFICIENT
    assert recommendation.overlap_pct == 50
    assert [
        (evaluation.ticker, evaluation.is_valid)
        for evaluation in recommendation.candidate_evaluations
    ] == [("BAD", False), ("GRID", True), ("WIDE", True)]
    assert [
        evaluation.theme_coverage_pct
        for evaluation in recommendation.candidate_evaluations
    ] == [0, 50, 0]


def test_find_etf_records_provider_evidence_for_a_rejected_non_etf(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        llm,
        "respond_text",
        lambda *_args, **_kwargs: json.dumps(
            [
                {
                    "etf_ticker": "ACME",
                    "etf_name": "Not Actually A Fund",
                    "reasoning": "Incorrect suggestion",
                }
            ]
        ),
    )

    class StockProvider:
        def fetch(self, ticker: str) -> ETFMarketEvidence:
            return _evidence(ticker).model_copy(update={"quote_type": "EQUITY"})

    recommendation = find_etf(_theme(), provider=StockProvider())

    assert recommendation.recommendation is ETFRecommendationType.NO_GOOD_ETF
    evaluation = recommendation.candidate_evaluations[0]
    assert evaluation.rejection_reasons == ["provider quote type is not ETF"]
    assert evaluation.evidence is not None
    assert evaluation.evidence.quote_type == "EQUITY"
    assert evaluation.evidence.average_daily_volume == 100_000
    assert evaluation.evidence.total_assets == 500_000_000
    assert evaluation.evidence.expense_ratio == 0.004
    assert evaluation.evidence.adjusted_close == 50
    assert evaluation.evidence.price_as_of == NOW
    assert evaluation.evidence.source == "fixture-etf-provider"


@pytest.mark.parametrize(
    ("weight_column", "weights"),
    (
        ("Holding Percent", [0.40, 0.35]),
        ("% Of Net Assets", [40.0, 35.0]),
    ),
)
def test_find_etf_normalizes_named_holdings_and_retains_unknown_weight(
    monkeypatch,
    weight_column,
    weights,
) -> None:
    monkeypatch.setattr(
        llm,
        "respond_text",
        lambda *_args, **_kwargs: json.dumps(
            [
                {
                    "etf_ticker": "GRID",
                    "etf_name": "Grid Fund",
                    "reasoning": "Relevant holdings",
                }
            ]
        ),
    )

    class FakeTicker:
        info = {
            "symbol": "GRID",
            "shortName": "Grid Fund",
            "quoteType": "ETF",
            "averageDailyVolume10Day": 100_000,
            "totalAssets": 500_000_000,
            "annualReportExpenseRatio": 0.004,
        }
        funds_data = type(
            "FundsData",
            (),
            {
                "top_holdings": pd.DataFrame(
                    {
                        "Name": ["Acme Energy", "Microsoft"],
                        weight_column: weights,
                    },
                    index=["ACME", "MSFT"],
                )
            },
        )()

        def history(self, **_kwargs):
            return pd.DataFrame({"Adj Close": [50.0]}, index=[pd.Timestamp(NOW)])

    monkeypatch.setattr(etfs_module.yf, "Ticker", lambda _ticker: FakeTicker())

    recommendation = find_etf(_theme())

    assert recommendation.recommendation is ETFRecommendationType.ETF_SUFFICIENT
    assert recommendation.holdings == {
        "ACME": 40.0,
        "MSFT": 35.0,
        "UNKNOWN/OTHER": 25.0,
    }
    assert recommendation.holdings_coverage_pct == 75
    assert recommendation.unknown_weight_pct == 25
    assert recommendation.adjusted_entry_price == 50
    assert recommendation.price_as_of == NOW
    assert recommendation.price_source == "yfinance"
    assert recommendation.selected_evidence is not None
    market_data = recommendation.to_market_data(observed_at=NOW)
    assert market_data.status is MarketDataStatus.AVAILABLE
    assert market_data.as_of == NOW
    assert market_data.data is not None
    assert market_data.data.current_price == 50
    assert market_data.data.source == "yfinance"


def test_find_etf_fails_closed_when_candidate_schema_is_not_a_list(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        llm,
        "respond_text",
        lambda *_args, **_kwargs: json.dumps(
            {"etf_ticker": "GRID", "etf_name": "Grid Fund"}
        ),
    )

    recommendation = find_etf(_theme())

    assert recommendation.recommendation is ETFRecommendationType.NO_GOOD_ETF
    assert recommendation.etf_ticker is None


def test_find_etf_fails_closed_when_provider_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        llm,
        "respond_text",
        lambda *_args, **_kwargs: json.dumps(
            [{"etf_ticker": "GRID", "etf_name": "Grid Fund"}]
        ),
    )

    class UnavailableProvider:
        def fetch(self, _ticker: str) -> ETFMarketEvidence:
            raise RuntimeError("upstream timeout")

    recommendation = find_etf(_theme(), provider=UnavailableProvider())

    assert recommendation.recommendation is ETFRecommendationType.NO_GOOD_ETF
    assert recommendation.candidate_evaluations[0].rejection_reasons == [
        "provider unavailable: upstream timeout"
    ]


def test_validated_etf_market_data_separates_price_time_from_retrieval_time() -> None:
    price_time = NOW - timedelta(hours=18)

    class FakeProvider:
        def fetch(self, ticker: str) -> ETFMarketEvidence:
            return _evidence(ticker).model_copy(update={"price_as_of": price_time})

    result = fetch_validated_etf("GRID", provider=FakeProvider(), clock=lambda: NOW)

    assert result.observed_at == NOW
    assert result.as_of == price_time
    assert result.age == 18 * 60 * 60
    assert result.data is not None
    assert result.data.fetched_at == NOW
    assert result.data.price_as_of == price_time


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        (
            {"average_daily_volume": 49_999},
            "average daily volume is below policy minimum",
        ),
        ({"total_assets": 49_999_999}, "assets are below policy minimum"),
        (
            {"expense_ratio": 0.0101},
            "expense ratio is unavailable or above policy maximum",
        ),
        ({"adjusted_close": None}, "adjusted entry price is unavailable"),
        (
            {"price_as_of": None},
            "adjusted entry price is missing its as-of timestamp",
        ),
        ({"source": None}, "provider source is unavailable"),
    ],
)
def test_find_etf_requires_complete_investability_evidence(
    monkeypatch,
    updates,
    reason,
) -> None:
    monkeypatch.setattr(
        llm,
        "respond_text",
        lambda *_args, **_kwargs: json.dumps(
            [{"etf_ticker": "GRID", "etf_name": "Grid Fund"}]
        ),
    )

    class IncompleteProvider:
        def fetch(self, ticker: str) -> ETFMarketEvidence:
            return _evidence(ticker).model_copy(update=updates)

    recommendation = find_etf(_theme(), provider=IncompleteProvider())

    assert recommendation.recommendation is ETFRecommendationType.NO_GOOD_ETF
    assert reason in recommendation.candidate_evaluations[0].rejection_reasons


def test_find_etf_rejects_an_unrecognized_holdings_weight_schema(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        llm,
        "respond_text",
        lambda *_args, **_kwargs: json.dumps(
            [{"etf_ticker": "GRID", "etf_name": "Grid Fund"}]
        ),
    )

    class FakeTicker:
        info = {
            "symbol": "GRID",
            "shortName": "Grid Fund",
            "quoteType": "ETF",
            "averageDailyVolume10Day": 100_000,
            "totalAssets": 500_000_000,
            "annualReportExpenseRatio": 0.004,
        }
        funds_data = type(
            "FundsData",
            (),
            {
                "top_holdings": pd.DataFrame(
                    {
                        "Name": ["Acme Energy"],
                        "Unexpected Weight": [40.0],
                    },
                    index=["ACME"],
                )
            },
        )()

        def history(self, **_kwargs):
            return pd.DataFrame({"Adj Close": [50.0]}, index=[pd.Timestamp(NOW)])

    monkeypatch.setattr(etfs_module.yf, "Ticker", lambda _ticker: FakeTicker())

    recommendation = find_etf(_theme())

    assert recommendation.recommendation is ETFRecommendationType.NO_GOOD_ETF
    assert recommendation.candidate_evaluations[0].rejection_reasons == [
        "holdings data is unavailable"
    ]


@pytest.mark.parametrize("weight", [0, -1, float("nan")])
def test_find_etf_rejects_invalid_normalized_holding_weights(
    monkeypatch,
    weight,
) -> None:
    monkeypatch.setattr(
        llm,
        "respond_text",
        lambda *_args, **_kwargs: json.dumps(
            [{"etf_ticker": "GRID", "etf_name": "Grid Fund"}]
        ),
    )

    class InvalidHoldingsProvider:
        def fetch(self, ticker: str) -> ETFMarketEvidence:
            return _evidence(ticker, holdings={"ACME": weight})

    recommendation = find_etf(_theme(), provider=InvalidHoldingsProvider())

    assert recommendation.recommendation is ETFRecommendationType.NO_GOOD_ETF
    assert recommendation.candidate_evaluations[0].rejection_reasons == [
        "holdings weights must be finite and positive"
    ]


def test_snapshot_persists_funded_etf_audit_and_adjusted_entry_observation(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        llm,
        "respond_text",
        lambda *_args, **_kwargs: json.dumps(
            [
                {
                    "etf_ticker": "GRID",
                    "etf_name": "Grid Fund",
                    "reasoning": "Relevant holdings",
                }
            ]
        ),
    )

    class FakeProvider:
        def fetch(self, ticker: str) -> ETFMarketEvidence:
            return _evidence(ticker, holdings={"ACME": 60, "OTHER": 40})

    theme = _theme()
    recommendation = find_etf(theme, provider=FakeProvider())
    assert recommendation.selected_evidence is not None
    etf_market_data = recommendation.to_market_data(observed_at=NOW)
    core_data = FundamentalsResult(
        ticker="VT",
        status=MarketDataStatus.AVAILABLE,
        data=Fundamentals(
            ticker="VT",
            provider_symbol="VT",
            name="Vanguard Total World Stock ETF",
            quote_type="ETF",
            source="fixture-market-provider",
            current_price=125,
            price_basis="adjusted_close",
            fetched_at=NOW,
        ),
        observed_at=NOW,
        as_of=NOW,
        age=0,
    )
    positions = [
        InstrumentPosition(
            ticker="GRID",
            instrument_type=InstrumentType.ETF,
            sleeve=PortfolioSleeve.THEMATIC,
            weight_pct=32,
            currency="USD",
            entry_price=50,
            price_timestamp=NOW,
        ),
        InstrumentPosition(
            ticker="VT",
            instrument_type=InstrumentType.ETF,
            sleeve=PortfolioSleeve.CORE,
            weight_pct=68,
            currency="USD",
            entry_price=125,
            price_timestamp=NOW,
        ),
    ]
    allocation = PortfolioAllocation(
        risk_profile=RiskProfile(
            appetite=RiskAppetite.MODERATE,
            time_horizon=TimeHorizon.MEDIUM,
        ),
        macro_regime=MacroRegime(
            regime=MacroRegimeType.NEUTRAL,
            confidence=7,
            drivers=["Stable growth"],
        ),
        positions=positions,
        entries=[
            AllocationEntry(
                theme=theme.name,
                vehicle="GRID",
                tickers=["GRID"],
                vehicle_type="etf",
                pct_allocation=32,
                entry_method=EntryMethod.DCA,
                rationale="Validated thematic ETF",
                entry_prices={"GRID": 50},
            )
        ],
        core_pct=68,
    )

    snapshot = build_discovery_snapshot(
        themes=[theme],
        scores={},
        market_data={"GRID": etf_market_data, "VT": core_data},
        allocation=allocation,
        etf_recommendations={theme.name: recommendation},
        created_at=NOW,
    )

    persisted = snapshot.etf_recommendations[theme.name]
    assert persisted.etf_ticker == "GRID"
    assert persisted.candidate_evaluations[0].is_valid is True
    assert snapshot.prices["GRID"].price == 50
    assert snapshot.prices["GRID"].observed_at == NOW
    assert snapshot.prices["GRID"].source == "fixture-etf-provider"

    unadjusted_core = core_data.model_copy(deep=True)
    assert unadjusted_core.data is not None
    unadjusted_core.data.price_basis = None
    with pytest.raises(ValueError, match="VT is missing adjusted-close evidence"):
        build_discovery_snapshot(
            themes=[theme],
            scores={},
            market_data={"GRID": etf_market_data, "VT": unadjusted_core},
            allocation=allocation,
            etf_recommendations={theme.name: recommendation},
            created_at=NOW,
        )

    wrong_source = etf_market_data.model_copy(deep=True)
    assert wrong_source.data is not None
    wrong_source.data.source = "different-provider"
    with pytest.raises(ValueError, match="GRID does not match its adjusted price audit"):
        build_discovery_snapshot(
            themes=[theme],
            scores={},
            market_data={"GRID": wrong_source, "VT": core_data},
            allocation=allocation,
            etf_recommendations={theme.name: recommendation},
            created_at=NOW,
        )
