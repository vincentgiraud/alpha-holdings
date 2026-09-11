from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import pandas as pd
from click.testing import CliRunner

from alpha_holdings import fundamentals as fundamentals_module
from alpha_holdings import llm
from alpha_holdings.cli import cli
from alpha_holdings.fundamentals import (
    calculate_cagr,
    get_technical_flags,
    passes_quality_filter,
    validate_company_identity,
)
from alpha_holdings.models import (
    AllocationEntry,
    Company,
    EntryMethod,
    Fundamentals,
    FundamentalsResult,
    InstrumentPosition,
    InstrumentType,
    MarketCapCategory,
    MarketDataStatus,
    MacroRegime,
    MacroRegimeType,
    PortfolioAllocation,
    PortfolioSleeve,
    PriceBasis,
    RiskAppetite,
    RiskProfile,
    SubTheme,
    SupplyChainTier,
    ThemeThesis,
    ThemeScore,
    TimeHorizon,
    ValuationLevel,
)
from alpha_holdings.scoring import (
    CandidateRejectedError,
    ScoringValidationError,
    assess_valuation,
    score_candidates,
    score_company,
    score_fundamentals,
)
from alpha_holdings.snapshots import RunSnapshotRepository, build_discovery_snapshot


NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)


def _company(ticker: str = "ACME", name: str = "Acme Energy") -> Company:
    return Company(
        ticker=ticker,
        name=name,
        role_in_theme="Grid equipment supplier",
        rationale="Benefits from transmission investment",
        market_cap_category=MarketCapCategory.MID,
        supply_chain_tier=SupplyChainTier.TIER_2_DIRECT_ENABLER,
        sector="Industrials",
    )


def _theme() -> ThemeThesis:
    return ThemeThesis(
        name="Grid Modernization",
        thesis_summary="Electricity demand requires grid investment.",
        why_now="Load growth is accelerating.",
        bull_case="Investment remains elevated.",
        bear_case="Permitting delays projects.",
        confidence_score=8,
        sub_themes=[
            SubTheme(
                name="Equipment",
                description="Grid equipment",
                companies=[_company()],
            )
        ],
    )


def _complete_fundamentals(
    ticker: str = "ACME",
    name: str = "Acme Energy Corporation",
    price: float = 125,
) -> Fundamentals:
    return Fundamentals(
        ticker=ticker,
        provider_symbol=ticker,
        name=name,
        quote_type="EQUITY",
        source="fixture-market-data",
        sector="Industrials",
        market_cap=5_000_000_000,
        current_price=price,
        avg_daily_volume=100_000,
        revenue_growth_3yr_cagr=12,
        gross_margin=40,
        operating_margin=20,
        forward_pe=20,
        fetched_at=NOW,
    )


class SequenceScoringProvider:
    name = "fixture-ai"
    model = "fixture-model-v1"

    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)

    def score(self, prompt: str) -> str:
        return self.responses.pop(0)


def test_quality_filter_rejects_fundamentals_without_required_data() -> None:
    passes, reason = passes_quality_filter(Fundamentals(ticker="EMPTY"))

    assert passes is False
    assert reason == (
        "Incomplete fundamentals: missing required market_cap, current_price, "
        "avg_daily_volume; 0/8 scoring metrics available (minimum 4)"
    )


def test_quality_filter_rejects_unavailable_market_data_outcomes() -> None:
    observation = FundamentalsResult(
        ticker="MISS",
        status=MarketDataStatus.UNAVAILABLE,
        observed_at=NOW,
        reason="provider offline",
    )

    passes, reason = passes_quality_filter(observation)

    assert passes is False
    assert reason == "Market data unavailable: provider offline"


def test_fundamental_score_keeps_fixed_weights_when_metrics_are_missing() -> None:
    growth_only = Fundamentals(ticker="ACME", revenue_growth_3yr_cagr=20)
    growth_and_zero_roe = Fundamentals(
        ticker="ACME",
        revenue_growth_3yr_cagr=20,
        roe=0,
    )

    assert score_fundamentals(growth_only) == 51.5
    assert score_fundamentals(growth_and_zero_roe) == 46.5


def test_fundamental_score_treats_non_finite_metrics_as_missing() -> None:
    fundamentals = Fundamentals(ticker="ACME", revenue_growth_cagr=float("nan"))

    assert score_fundamentals(fundamentals) == 50.0


def test_company_identity_rejects_a_real_but_unrelated_symbol() -> None:
    fundamentals = Fundamentals(
        ticker="ACME",
        provider_symbol="ACME",
        name="Unrelated Mining Corporation",
        quote_type="EQUITY",
    )

    matches, reason = validate_company_identity(_company(), fundamentals)

    assert matches is False
    assert reason == "provider name 'Unrelated Mining Corporation' does not match 'Acme Energy'"


def test_company_identity_rejects_an_internally_mismatched_ticker() -> None:
    fundamentals = Fundamentals(
        ticker="OTHER",
        provider_symbol="ACME",
        name="Acme Energy Corporation",
        quote_type="EQUITY",
    )

    matches, reason = validate_company_identity(_company(), fundamentals)

    assert matches is False
    assert reason == "fundamentals ticker 'OTHER' does not match 'ACME'"


def test_company_identity_does_not_match_on_a_generic_name_token() -> None:
    fundamentals = Fundamentals(
        ticker="ACME",
        provider_symbol="ACME",
        name="Beta Energy Corporation",
        quote_type="EQUITY",
    )

    matches, _ = validate_company_identity(_company(), fundamentals)

    assert matches is False


def test_scoring_retries_invalid_ai_output_and_records_evidence() -> None:
    malformed = json.dumps(
        {
            "ticker": "ACME",
            "alignment_score": 140,
            "pricing_gap_score": 70,
            "revenue_exposure": 80,
            "alignment_reasoning": "Strong grid exposure.",
            "pricing_gap_reasoning": "Valuation remains reasonable.",
            "revenue_exposure_reasoning": "Most revenue serves grid customers.",
            "sources": ["https://example.com/acme"],
        }
    )
    valid = json.dumps(
        {
            "ticker": "ACME",
            "alignment_score": 90,
            "pricing_gap_score": 70,
            "revenue_exposure": 80,
            "alignment_reasoning": "Strong grid exposure.",
            "pricing_gap_reasoning": "Valuation remains reasonable.",
            "revenue_exposure_reasoning": "Most revenue serves grid customers.",
            "sources": ["https://example.com/acme"],
        }
    )

    score = score_company(
        _company(),
        _theme(),
        _complete_fundamentals(),
        provider=SequenceScoringProvider(malformed, valid),
        clock=lambda: NOW,
    )

    assert score.ticker == "ACME"
    assert score.thesis_alignment_score == 90
    assert score.pricing_gap_score == 70
    assert score.revenue_exposure_score == 80
    assert score.score_as_of == NOW
    assert score.evidence_sources == ["https://example.com/acme"]
    assert score.scoring_provider == "fixture-ai"
    assert score.scoring_model == "fixture-model-v1"


def test_scoring_rejects_blank_provider_provenance() -> None:
    class BlankProvenanceProvider(SequenceScoringProvider):
        name = " "
        model = ""

    valid = json.dumps(
        {
            "ticker": "ACME",
            "alignment_score": 90,
            "pricing_gap_score": 70,
            "revenue_exposure": 80,
            "alignment_reasoning": "Strong grid exposure.",
            "pricing_gap_reasoning": "Valuation remains reasonable.",
            "revenue_exposure_reasoning": "Most revenue serves grid customers.",
            "sources": ["https://example.com/acme"],
        }
    )

    with pytest.raises(ScoringValidationError, match="provider provenance"):
        score_company(
            _company(),
            _theme(),
            _complete_fundamentals(),
            provider=BlankProvenanceProvider(valid),
            clock=lambda: NOW,
        )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "ticker": "OTHER",
            "alignment_score": 80,
            "pricing_gap_score": 70,
            "revenue_exposure": 60,
            "alignment_reasoning": "Strong exposure.",
            "pricing_gap_reasoning": "Reasonable valuation.",
            "revenue_exposure_reasoning": "Material revenue exposure.",
            "sources": ["https://example.com/evidence"],
        },
        {
            "ticker": "ACME",
            "alignment_score": 80,
            "pricing_gap_score": 70,
            "revenue_exposure": 60,
            "alignment_reasoning": "",
            "pricing_gap_reasoning": "Reasonable valuation.",
            "revenue_exposure_reasoning": "Material revenue exposure.",
            "sources": [],
        },
    ],
)
def test_scoring_rejects_ai_output_without_identity_reasoning_or_provenance(
    payload,
) -> None:
    encoded = json.dumps(payload)

    with pytest.raises(ScoringValidationError, match="valid score for ACME"):
        score_company(
            _company(),
            _theme(),
            _complete_fundamentals(),
            provider=SequenceScoringProvider(encoded, encoded),
            clock=lambda: NOW,
        )


def test_scoring_propagates_a_non_retryable_codex_failure() -> None:
    class UsageLimitedProvider:
        name = "codex-cli"
        model = "test-model"

        def score(self, prompt: str) -> str:
            del prompt
            raise llm.CodexCLIError("You've hit your usage limit", retryable=False)

    with pytest.raises(llm.CodexCLIError, match="usage limit"):
        score_company(
            _company(),
            _theme(),
            _complete_fundamentals(),
            provider=UsageLimitedProvider(),
            clock=lambda: NOW,
        )


def test_cagr_uses_the_actual_elapsed_time_between_observations() -> None:
    growth, years = calculate_cagr(
        100,
        date(2023, 6, 30),
        121,
        date(2025, 6, 30),
    )

    assert years == pytest.approx(2.0, abs=0.01)
    assert growth == pytest.approx(10.0, abs=0.02)


def test_yfinance_revenue_cagr_uses_the_provider_observation_dates(
    monkeypatch,
) -> None:
    class FakeTicker:
        info = {
            "symbol": "ACME",
            "shortName": "Acme Energy Corporation",
            "quoteType": "EQUITY",
            "marketCap": 5_000_000_000,
            "regularMarketPrice": 125,
            "averageDailyVolume10Day": 100_000,
        }
        financials = pd.DataFrame(
            {
                datetime(2025, 6, 30): [121.0],
                datetime(2023, 6, 30): [100.0],
            },
            index=["Total Revenue"],
        )

        def history(self, **_kwargs):
            return pd.DataFrame()

    monkeypatch.setattr(
        fundamentals_module.yf,
        "Ticker",
        lambda _ticker: FakeTicker(),
    )

    fundamentals = fundamentals_module.YahooFinanceProvider().fetch("ACME")

    assert fundamentals.revenue_growth_cagr == pytest.approx(10.0, abs=0.02)
    assert fundamentals.revenue_growth_period_years == pytest.approx(2.0, abs=0.01)


def test_negative_earnings_multiples_are_not_classified_as_cheap() -> None:
    valuation = assess_valuation(
        Fundamentals(ticker="LOSS", forward_pe=-8, peg_ratio=-0.5)
    )

    assert valuation.level is ValuationLevel.FAIR
    assert valuation.summary == "Insufficient positive earnings multiples for valuation."


def test_negative_multiples_do_not_satisfy_the_completeness_policy() -> None:
    fundamentals = Fundamentals(
        ticker="LOSS",
        market_cap=5_000_000_000,
        current_price=20,
        avg_daily_volume=100_000,
        revenue_growth_cagr=5,
        gross_margin=30,
        forward_pe=-8,
        peg_ratio=-0.5,
    )

    passes, reason = passes_quality_filter(fundamentals)

    assert passes is False
    assert "2/8 scoring metrics available (minimum 4)" in reason


def test_proxy_metrics_are_labelled_as_proxies_not_direct_history_or_revisions() -> None:
    flags = get_technical_flags(
        Fundamentals(
            ticker="ACME",
            forward_to_trailing_pe_ratio=1.4,
            forward_pe_vs_price_history_proxy=65,
        )
    )

    assert flags == [
        "⚠ Forward/trailing P/E ratio 1.4x — not a direct earnings-revision measure",
        "🏷️ Forward P/E is 65% of the price/current-EPS proxy — not historical P/E",
    ]


def test_score_company_rejects_identity_mismatch_before_ai_scoring() -> None:
    unrelated = _complete_fundamentals().model_copy(
        update={"name": "Unrelated Mining Corporation"}
    )

    with pytest.raises(
        CandidateRejectedError,
        match="provider name 'Unrelated Mining Corporation' does not match 'Acme Energy'",
    ):
        score_company(
            _company(),
            _theme(),
            unrelated,
            provider=SequenceScoringProvider(),
            clock=lambda: NOW,
        )


def test_candidate_scoring_excludes_unavailable_candidates_with_reasons() -> None:
    available = FundamentalsResult(
        ticker="ACME",
        status=MarketDataStatus.AVAILABLE,
        data=_complete_fundamentals(),
        observed_at=NOW,
        as_of=NOW,
        age=0,
    )
    unavailable = FundamentalsResult(
        ticker="BETA",
        status=MarketDataStatus.UNAVAILABLE,
        observed_at=NOW,
        reason="provider offline",
    )
    valid_score = json.dumps(
        {
            "ticker": "ACME",
            "alignment_score": 90,
            "pricing_gap_score": 70,
            "revenue_exposure": 80,
            "alignment_reasoning": "Strong grid exposure.",
            "pricing_gap_reasoning": "Valuation remains reasonable.",
            "revenue_exposure_reasoning": "Most revenue serves grid customers.",
            "sources": ["https://example.com/acme"],
        }
    )

    theme = _theme().model_copy(deep=True)
    theme.sub_themes[0].companies.append(_company("BETA", "Beta Grid"))

    result = score_candidates(
        [theme],
        {"ACME": available, "BETA": unavailable},
        provider=SequenceScoringProvider(valid_score),
        clock=lambda: NOW,
    )

    assert [company.full_ticker for company in result.themes[0].all_companies] == [
        "ACME"
    ]
    assert [score.ticker for score in result.scores["Grid Modernization"]] == [
        "ACME"
    ]
    assert result.rejections == {"BETA": "Market data unavailable: provider offline"}


def test_candidate_scoring_removes_themes_without_validated_candidates() -> None:
    rejected_theme = _theme().model_copy(deep=True)
    rejected_theme.name = "Rejected Theme"
    rejected_theme.sub_themes[0].companies = [_company("BETA", "Beta Grid")]
    unavailable = FundamentalsResult(
        ticker="BETA",
        status=MarketDataStatus.UNAVAILABLE,
        observed_at=NOW,
        reason="provider offline",
    )

    result = score_candidates([rejected_theme], {"BETA": unavailable})

    assert result.themes == []
    assert result.scores == {}
    assert result.rejections == {"BETA": "Market data unavailable: provider offline"}


def test_discovery_snapshot_keeps_prices_for_the_full_scored_cohort(tmp_path) -> None:
    theme = _theme().model_copy(deep=True)
    theme.sub_themes[0].companies.append(_company("BETA", "Beta Grid"))
    acme = _complete_fundamentals()
    beta = _complete_fundamentals("BETA", "Beta Grid PLC", 80)
    market_data = {
        ticker: FundamentalsResult(
            ticker=ticker,
            status=MarketDataStatus.AVAILABLE,
            data=data,
            observed_at=NOW,
            as_of=NOW,
            age=0,
        )
        for ticker, data in {"ACME": acme, "BETA": beta}.items()
    }
    core = _complete_fundamentals("VT", "Vanguard Total World Stock ETF", 100)
    core.quote_type = "ETF"
    core.price_basis = PriceBasis.ADJUSTED_CLOSE
    market_data["VT"] = FundamentalsResult(
        ticker="VT",
        status=MarketDataStatus.AVAILABLE,
        data=core,
        observed_at=NOW,
        as_of=NOW,
        age=0,
    )
    scores = {
        theme.name: [
            ThemeScore(
                ticker=ticker,
                fundamental_score=70,
                thesis_alignment_score=80,
                pricing_gap_score=60,
                revenue_exposure_score=75,
                composite_score=71,
                alignment_reasoning="Strong exposure.",
                pricing_gap_reasoning="Reasonable valuation.",
                revenue_exposure_reasoning="Material revenue exposure.",
                score_as_of=NOW,
                evidence_sources=[f"https://example.com/{ticker.lower()}"],
                scoring_provider="fixture-ai",
                scoring_model="fixture-v1",
            )
            for ticker in ("ACME", "BETA")
        ]
    }
    allocation = PortfolioAllocation(
        risk_profile=RiskProfile(
            appetite=RiskAppetite.MODERATE,
            time_horizon=TimeHorizon.MEDIUM,
        ),
        macro_regime=MacroRegime(
            regime=MacroRegimeType.NEUTRAL,
            confidence=7,
            drivers=["Stable growth"],
            allocation_modifier=0.8,
        ),
        entries=[
            AllocationEntry(
                theme=theme.name,
                vehicle="ACME",
                vehicle_type="stocks",
                pct_allocation=10,
                entry_method=EntryMethod.DCA,
                rationale="Highest score",
                entry_prices={"ACME": 125},
            )
        ],
        positions=[
            InstrumentPosition(
                ticker="ACME",
                instrument_type=InstrumentType.STOCK,
                sleeve=PortfolioSleeve.THEMATIC,
                weight_pct=10,
                currency="USD",
                entry_price=125,
                price_timestamp=NOW,
            ),
            InstrumentPosition(
                ticker="VT",
                instrument_type=InstrumentType.ETF,
                sleeve=PortfolioSleeve.CORE,
                weight_pct=90,
                currency="USD",
                entry_price=100,
                price_timestamp=NOW,
            ),
        ],
        core_pct=90,
        generated_at=NOW,
    )

    snapshot = build_discovery_snapshot(
        themes=[theme],
        scores=scores,
        market_data=market_data,
        allocation=allocation,
        created_at=NOW,
        model_configuration={"scoring_model": "fixture-v1"},
    )
    repository = RunSnapshotRepository(tmp_path)
    repository.save(snapshot)
    restored = repository.load(snapshot.run_id)

    assert restored is not None
    assert [score.ticker for score in restored.candidate_scores[theme.name]] == [
        "ACME",
        "BETA",
    ]
    assert set(restored.prices) == {"ACME", "BETA", "VT"}
    assert restored.prices["BETA"].price == 80
    assert restored.prices["BETA"].observed_at == NOW
    assert restored.instrument_metadata["BETA"].name == "Beta Grid PLC"
    assert restored.provenance["market_data"]["BETA"]["source"] == (
        "fixture-market-data"
    )
    assert [(position.ticker, position.weight_pct) for position in restored.positions] == [
        ("ACME", 10),
        ("VT", 90),
    ]
    assert restored.instrument_metadata["VT"].instrument_type is InstrumentType.ETF


def test_discovery_snapshot_rejects_a_score_without_evidence() -> None:
    fundamentals = _complete_fundamentals()
    market_data = {
        "ACME": FundamentalsResult(
            ticker="ACME",
            status=MarketDataStatus.AVAILABLE,
            data=fundamentals,
            observed_at=NOW,
            as_of=NOW,
            age=0,
        )
    }
    score = ThemeScore(
        ticker="ACME",
        fundamental_score=70,
        thesis_alignment_score=80,
        pricing_gap_score=60,
        composite_score=71,
    )
    allocation = PortfolioAllocation(
        risk_profile=RiskProfile(
            appetite=RiskAppetite.MODERATE,
            time_horizon=TimeHorizon.MEDIUM,
        ),
        macro_regime=MacroRegime(
            regime=MacroRegimeType.NEUTRAL,
            confidence=7,
            drivers=["Stable growth"],
            allocation_modifier=0.8,
        ),
        core_pct=100,
        generated_at=NOW,
    )

    with pytest.raises(ValueError, match="Scored candidate ACME is missing evidence"):
        build_discovery_snapshot(
            themes=[_theme()],
            scores={_theme().name: [score]},
            market_data=market_data,
            allocation=allocation,
            created_at=NOW,
        )


def test_discover_publishes_a_versioned_scored_cohort(monkeypatch) -> None:
    class FakeTicker:
        def __init__(self, ticker: str) -> None:
            is_etf = ticker in {"VT", "GRIDETF"}
            self.info = {
                "symbol": ticker,
                "shortName": (
                    "Vanguard Total World Stock ETF"
                    if ticker == "VT"
                    else "Grid Infrastructure ETF"
                    if ticker == "GRIDETF"
                    else "Acme Energy Corporation"
                ),
                "quoteType": "ETF" if is_etf else "EQUITY",
                "sector": "Industrials",
                "marketCap": 5_000_000_000,
                "regularMarketPrice": 125,
                "fiftyTwoWeekHigh": 150,
                "grossMargins": 0.4,
                "operatingMargins": 0.2,
                "forwardPE": 20,
                "pegRatio": 1.2,
                "debtToEquity": 40,
                "returnOnEquity": 0.15,
                "freeCashflow": 250_000_000,
                "averageDailyVolume10Day": 100_000,
                "totalAssets": 500_000_000 if is_etf else None,
                "annualReportExpenseRatio": 0.004 if is_etf else None,
            }
            self.financials = pd.DataFrame()
            self.funds_data = type(
                "FundsData",
                (),
                {
                    "top_holdings": pd.DataFrame(
                        {"Holding Percent": [1.0]},
                        index=["ACME"],
                    )
                },
            )()

        def history(self, **kwargs):
            if kwargs.get("auto_adjust") is False:
                return pd.DataFrame(
                    {"Adj Close": [125.0]},
                    index=[pd.Timestamp(NOW)],
                )
            return pd.DataFrame()

    def fake_ai(prompt: str, **_kwargs) -> str:
        if "most important current events" in prompt:
            return json.dumps(
                [
                    {
                        "headline": "Grid investment rises",
                        "summary": "Electricity demand is accelerating.",
                        "source": "https://example.com/macro",
                        "date": "2026-09-08",
                        "tags": ["grid"],
                    }
                ]
            )
        if "overall economic environment" in prompt:
            return json.dumps(
                {
                    "regime": "neutral",
                    "confidence": 7,
                    "drivers": ["Stable growth"],
                    "allocation_modifier": 0.8,
                }
            )
        if "significant emerging" in prompt:
            return json.dumps([_theme().model_dump(mode="json", exclude_none=True)])
        if "reviewing a first-pass" in prompt:
            return "[]"
        if "cross-theme dependencies" in prompt:
            return "[]"
        if "Evaluate THREE dimensions" in prompt:
            return json.dumps(
                {
                    "ticker": "ACME",
                    "alignment_score": 90,
                    "pricing_gap_score": 70,
                    "revenue_exposure": 80,
                    "alignment_reasoning": "Strong grid exposure.",
                    "pricing_gap_reasoning": "Valuation remains reasonable.",
                    "revenue_exposure_reasoning": "Most revenue serves grids.",
                    "sources": ["https://example.com/acme"],
                }
            )
        if "identify the 1-3 most relevant thematic ETFs" in prompt:
            return json.dumps(
                [
                    {
                        "etf_ticker": "GRIDETF",
                        "etf_name": "Grid Infrastructure ETF",
                        "reasoning": "Covers the validated grid candidate.",
                    }
                ]
            )
        raise AssertionError(f"Unexpected AI prompt: {prompt[:80]}")

    monkeypatch.setattr(llm, "respond_text", fake_ai)
    monkeypatch.setattr(fundamentals_module.yf, "Ticker", FakeTicker)
    monkeypatch.setattr(fundamentals_module, "DEFAULT_CLOCK", lambda: NOW)
    runner = CliRunner()

    with runner.isolated_filesystem():
        result = runner.invoke(cli, ["discover", "--capital", "10000"])
        snapshot = RunSnapshotRepository().load_latest()
        legacy_files = [
            *Path("data/allocations").glob("*.json"),
            *Path("data/themes").glob("*.json"),
            *Path("data/scores").glob("*.json"),
        ]

    assert result.exit_code == 0, result.output
    assert snapshot is not None
    assert snapshot.completeness.is_complete is True
    assert [score.ticker for score in snapshot.candidate_scores[_theme().name]] == [
        "ACME"
    ]
    assert snapshot.prices["ACME"].price == 125
    assert snapshot.prices["ACME"].observed_at == NOW
    assert [(position.ticker, position.weight_pct) for position in snapshot.positions] == [
        ("VT", 100)
    ]
    assert snapshot.positions[0].capital_amount == 10_000
    assert "GRIDETF" in snapshot.provenance["market_data"]
    assert snapshot.allocation.core_pct == 100
    assert snapshot.allocation.capital == 10_000
    assert snapshot.model_configuration["scoring_reasoning_effort"] == "low"
    assert all(
        "vehicle" not in entry
        for entry in snapshot.allocation.model_dump(mode="json")["entries"]
    )
    assert "100.0%" in result.output
    assert "$10,000.00" in result.output
    assert "VT / VOO" not in result.output
    assert legacy_files == []
