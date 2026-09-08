from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from alpha_holdings import fundamentals, themes
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


def _save_scores(*tickers: str) -> None:
    score_dir = Path("data/scores")
    score_dir.mkdir(parents=True)
    scores = {
        "Test Theme": [
            {
                "ticker": ticker,
                "composite_score": 80.0,
                "fundamental_score": 80.0,
                "thesis_alignment_score": 80.0,
                "pricing_gap_score": 80.0,
            }
            for ticker in tickers
        ]
    }
    (score_dir / "20260908_scores.json").write_text(json.dumps(scores))


def test_opportunities_reports_unavailable_analysis_and_failed_tickers(
    monkeypatch,
) -> None:
    monkeypatch.setattr(fundamentals, "DEFAULT_PROVIDER", FailingProvider())
    runner = CliRunner()

    with runner.isolated_filesystem():
        themes.save_themes([_theme("FAIL", "MISS")])
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
    runner = CliRunner()

    with runner.isolated_filesystem():
        themes.save_themes([_theme("GOOD", "BAD")])
        result = runner.invoke(cli, ["opportunities"])

    assert result.exit_code == 0
    assert "Partial analysis" in result.output
    assert "BAD (unavailable)" in result.output
    assert "GOOD" in result.output
    assert "ON SALE" in result.output


def test_watchlist_excludes_failed_tickers_and_marks_partial_analysis(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        fundamentals, "DEFAULT_PROVIDER", PartiallyAvailableWatchlistProvider()
    )
    runner = CliRunner()

    with runner.isolated_filesystem():
        themes.save_themes([_theme("GOOD", "BAD")])
        _save_scores("GOOD", "BAD")
        result = runner.invoke(cli, ["watchlist"])

    assert result.exit_code == 0
    assert "Partial analysis" in result.output
    assert "BAD (unavailable)" in result.output
    assert result.output.count("BAD") == 1
    assert "GOOD" in result.output
    assert "Watchlist" in result.output
