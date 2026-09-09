from __future__ import annotations

from types import SimpleNamespace

from click.testing import CliRunner

from alpha_holdings import fundamentals, snapshots
from alpha_holdings.cli import cli
from alpha_holdings.models import (
    Company,
    Fundamentals,
    MarketCapCategory,
    SubTheme,
    SupplyChainTier,
    ThemeThesis,
)


class FailingProvider:
    def fetch(self, ticker: str) -> Fundamentals:
        raise ConnectionError("market-data provider offline")


class PartiallyAvailableProvider:
    def fetch(self, ticker: str) -> Fundamentals:
        if ticker == "GOOD":
            return Fundamentals(
                ticker=ticker,
                current_price=80.0,
                high_52w=100.0,
                drawdown_from_peak=-20.0,
                market_cap=10_000_000_000,
                avg_daily_volume=1_000_000,
                revenue_growth_cagr=12,
                gross_margin=40,
                operating_margin=20,
                forward_pe=20,
            )
        raise TimeoutError("market-data request timed out")


class PartiallyAvailableWatchlistProvider:
    def fetch(self, ticker: str) -> Fundamentals:
        if ticker == "GOOD":
            return Fundamentals(
                ticker=ticker,
                current_price=95.0,
                high_52w=100.0,
                drawdown_from_peak=-5.0,
                market_cap=10_000_000_000,
                avg_daily_volume=1_000_000,
                revenue_growth_cagr=12,
                gross_margin=40,
                operating_margin=20,
                forward_pe=20,
            )
        raise TimeoutError("market-data request timed out")


def _theme(*tickers: str) -> ThemeThesis:
    companies = [
        Company(
            ticker=ticker,
            name=f"{ticker} Corp",
            role_in_theme="Test company",
            rationale="Test rationale",
            market_cap_category=MarketCapCategory.LARGE,
            supply_chain_tier=SupplyChainTier.TIER_2_DIRECT_ENABLER,
            sector="Technology",
        )
        for ticker in tickers
    ]
    return ThemeThesis(
        name="Test Theme",
        thesis_summary="Test thesis",
        why_now="Test timing",
        bull_case="Test upside",
        bear_case="Test downside",
        confidence_score=8,
        sub_themes=[SubTheme(name="Test", description="Test", companies=companies)],
    )


def _snapshot(theme: ThemeThesis):
    return SimpleNamespace(
        themes=[theme],
        candidate_scores={
            theme.name: [
                {
                    "ticker": company.full_ticker,
                    "composite_score": 80.0,
                    "fundamental_score": 80.0,
                    "thesis_alignment_score": 80.0,
                    "pricing_gap_score": 80.0,
                    "alignment_reasoning": "Strong fit.",
                    "pricing_gap_reasoning": "Reasonable valuation.",
                    "revenue_exposure_reasoning": "Relevant revenue exposure.",
                }
                for company in theme.all_companies
            ]
        },
        allocation=SimpleNamespace(
            entries=[SimpleNamespace(theme=theme.name, tickers=[c.full_ticker for c in theme.all_companies])],
            model_dump=lambda **kwargs: {"entries": []},
        ),
    )


def test_opportunities_reports_unavailable_analysis_and_failed_tickers(
    monkeypatch,
) -> None:
    monkeypatch.setattr(fundamentals, "DEFAULT_PROVIDER", FailingProvider())
    theme = _theme("FAIL", "MISS")
    monkeypatch.setattr(
        snapshots, "RunSnapshotRepository",
        lambda: SimpleNamespace(load_latest=lambda: _snapshot(theme)),
    )
    runner = CliRunner()

    with runner.isolated_filesystem():
        result = runner.invoke(cli, ["opportunities"])

    assert result.exit_code == 1
    assert "Analysis unavailable" in result.output
    assert "FAIL" in result.output
    assert "MISS" in result.output
    assert "No opportunities detected" not in result.output


def test_opportunities_keeps_valid_signals_and_marks_partial_analysis(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        fundamentals, "DEFAULT_PROVIDER", PartiallyAvailableProvider()
    )
    theme = _theme("GOOD", "BAD")
    monkeypatch.setattr(
        snapshots, "RunSnapshotRepository",
        lambda: SimpleNamespace(load_latest=lambda: _snapshot(theme)),
    )
    runner = CliRunner()

    with runner.isolated_filesystem():
        result = runner.invoke(cli, ["opportunities"])

    assert result.exit_code == 0
    assert "Partial analysis" in result.output
    assert "BAD (unavailable)" in result.output
    assert "GOOD" in result.output
    assert any(label in result.output for label in ("ON SALE", "STABILIZED", "RECOVERING"))


def test_watchlist_excludes_failed_tickers_and_marks_partial_analysis(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        fundamentals, "DEFAULT_PROVIDER", PartiallyAvailableWatchlistProvider()
    )
    theme = _theme("GOOD", "BAD")
    monkeypatch.setattr(
        snapshots, "RunSnapshotRepository",
        lambda: SimpleNamespace(load_latest=lambda: _snapshot(theme)),
    )
    runner = CliRunner()

    with runner.isolated_filesystem():
        result = runner.invoke(cli, ["watchlist"])

    assert result.exit_code == 0
    assert "Partial analysis" in result.output
    assert "BAD (unavailable)" in result.output
    assert result.output.count("BAD") == 1
    assert "GOOD" in result.output
    assert "Watchlist" in result.output


def test_explain_reads_scores_from_the_versioned_snapshot(monkeypatch) -> None:
    theme = _theme("GOOD")
    monkeypatch.setattr(
        snapshots, "RunSnapshotRepository",
        lambda: SimpleNamespace(load_latest=lambda: _snapshot(theme)),
    )

    with CliRunner().isolated_filesystem():
        result = CliRunner().invoke(cli, ["explain"])

    assert result.exit_code == 0
    assert "Test Theme" in result.output
    assert "Strong fit." in result.output
