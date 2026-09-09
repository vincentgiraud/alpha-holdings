from __future__ import annotations

import json
from datetime import UTC, datetime

import pandas as pd
from click.testing import CliRunner

from alpha_holdings import holdings as holdings_module
from alpha_holdings.cli import cli
from alpha_holdings.holdings import (
    ETFComposition,
    ExposureAnalysis,
    FXQuote,
    Holding,
    HoldingsPortfolio,
    MarketQuote,
    YahooHoldingsProvider,
    get_existing_exposure,
    analyze_overlap,
    load_holdings,
)


NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)


def test_load_holdings_preserves_explicit_allocation_position_weights(tmp_path) -> None:
    allocation_path = tmp_path / "allocation.json"
    allocation_path.write_text(
        json.dumps(
            {
                "positions": [
                    {
                        "ticker": "VT",
                        "instrument_type": "etf",
                        "sleeve": "core",
                        "weight_pct": 90,
                        "currency": "USD",
                        "entry_price": 125,
                        "price_timestamp": NOW.isoformat(),
                    },
                    {
                        "ticker": "GRID",
                        "instrument_type": "etf",
                        "sleeve": "thematic",
                        "weight_pct": 10,
                        "currency": "USD",
                        "entry_price": 50,
                        "price_timestamp": NOW.isoformat(),
                    },
                ],
                "entries": [],
            }
        )
    )

    portfolio = load_holdings(allocation_path)

    assert [
        (
            holding.ticker,
            holding.weight_pct,
            holding.currency,
            holding.avg_cost,
            holding.price_as_of,
        )
        for holding in portfolio.holdings
    ] == [
        ("VT", 90, "USD", 125, NOW),
        ("GRID", 10, "USD", 50, NOW),
    ]


def test_load_holdings_discloses_legacy_group_weight_split(tmp_path) -> None:
    allocation_path = tmp_path / "legacy-allocation.json"
    allocation_path.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "theme": "Grid",
                        "vehicle": "ACME, BETA",
                        "pct_allocation": 30,
                        "entry_prices": {"ACME": 10, "BETA": 20},
                    }
                ]
            }
        )
    )

    portfolio = load_holdings(allocation_path)

    assert [(holding.ticker, holding.weight_pct) for holding in portfolio.holdings] == [
        ("ACME", 15),
        ("BETA", 15),
    ]
    assert portfolio.warnings == [
        "Legacy allocation entry 'Grid' has no per-ticker weights; "
        "its 30.0% was divided equally across 2 tickers."
    ]


def test_get_existing_exposure_aggregates_repeated_lots_before_normalizing() -> None:
    class FakeProvider:
        def get_quote(self, ticker: str, as_of: datetime) -> MarketQuote:
            return MarketQuote(
                ticker=ticker,
                price={"ACME": 10, "BETA": 12}[ticker],
                currency="USD",
                as_of=as_of,
                source="fixture",
            )

        def get_etf_composition(self, ticker: str, _as_of: datetime):
            return None

    portfolio = HoldingsPortfolio(
        holdings=[
            Holding(ticker="ACME", shares=1),
            Holding(ticker="ACME", shares=3),
            Holding(ticker="BETA", shares=5),
        ]
    )

    result = get_existing_exposure(
        portfolio,
        base_currency="USD",
        as_of=NOW,
        provider=FakeProvider(),
    )

    assert result.exposures == {"ACME": 40, "BETA": 60}
    assert sum(result.exposures.values()) == 100
    assert result.coverage_pct == 100
    assert result.warnings == []


def test_get_existing_exposure_converts_global_positions_with_dated_fx() -> None:
    class FakeProvider:
        def __init__(self) -> None:
            self.fx_dates: list[datetime] = []

        def get_quote(self, ticker: str, as_of: datetime) -> MarketQuote:
            return MarketQuote(
                ticker=ticker,
                price=100,
                currency={"USCO": "USD", "EUCO.DE": "EUR"}[ticker],
                as_of=as_of,
                source="fixture",
            )

        def get_fx_rate(
            self,
            from_currency: str,
            to_currency: str,
            as_of: datetime,
        ) -> FXQuote:
            self.fx_dates.append(as_of)
            return FXQuote(
                from_currency=from_currency,
                to_currency=to_currency,
                rate=1.2,
                as_of=as_of,
                source="fixture-fx",
            )

        def get_etf_composition(self, ticker: str, _as_of: datetime):
            return None

    provider = FakeProvider()
    portfolio = HoldingsPortfolio(
        holdings=[
            Holding(ticker="USCO", shares=1),
            Holding(ticker="EUCO.DE", shares=1),
        ]
    )

    result = get_existing_exposure(
        portfolio,
        base_currency="USD",
        as_of=NOW,
        provider=provider,
    )

    assert result.exposures == {"USCO": 45.4545, "EUCO.DE": 54.5455}
    assert provider.fx_dates == [NOW]
    assert result.warnings == []


def test_get_existing_exposure_reports_missing_quotes_without_equal_weighting() -> None:
    class PartialProvider:
        def get_quote(self, ticker: str, as_of: datetime) -> MarketQuote:
            if ticker == "MISSING":
                raise RuntimeError("provider offline")
            return MarketQuote(
                ticker=ticker,
                price=10,
                currency="USD",
                as_of=as_of,
                source="fixture",
            )

        def get_etf_composition(self, ticker: str, _as_of: datetime):
            return None

    result = get_existing_exposure(
        HoldingsPortfolio(
            holdings=[
                Holding(ticker="ACME", shares=10),
                Holding(ticker="MISSING", shares=1_000),
            ]
        ),
        as_of=NOW,
        provider=PartialProvider(),
    )

    assert result.exposures == {"ACME": 100}
    assert result.coverage_pct == 50
    assert result.warnings == [
        "MISSING: quote or FX unavailable (provider offline)."
    ]


def test_analyze_overlap_decomposes_both_etfs_and_retains_unknown_residuals() -> None:
    class ETFProvider:
        def get_etf_composition(self, ticker: str, _as_of: datetime):
            holdings = {
                "OLD": {"ACME": 40, "LEGACY": 30},
                "NEW": {"ACME": 20, "FUTURE": 50},
            }[ticker]
            return ETFComposition(
                ticker=ticker,
                holdings=holdings,
                as_of=NOW,
                source="fixture",
            )

    provider = ETFProvider()
    existing = get_existing_exposure(
        HoldingsPortfolio(holdings=[Holding(ticker="OLD", weight_pct=100)]),
        as_of=NOW,
        provider=provider,
    )
    proposed = get_existing_exposure(
        HoldingsPortfolio(holdings=[Holding(ticker="NEW", weight_pct=100)]),
        as_of=NOW,
        provider=provider,
    )

    overlaps = analyze_overlap(existing, proposed)

    assert existing.exposures == {
        "ACME": 40,
        "LEGACY": 30,
        "OLD:UNKNOWN/OTHER": 30,
    }
    assert proposed.exposures == {
        "ACME": 20,
        "FUTURE": 50,
        "NEW:UNKNOWN/OTHER": 30,
    }
    assert overlaps == [
        {
            "ticker": "ACME",
            "existing_pct": 40,
            "new_pct": 20,
            "combined_pct": 60,
        }
    ]


def test_get_existing_exposure_reads_named_etf_weight_field(monkeypatch) -> None:
    class FakeTicker:
        info = {"quoteType": "ETF"}
        funds_data = type(
            "FundsData",
            (),
            {
                "top_holdings": pd.DataFrame(
                    {
                        "Name": ["Acme", "Beta"],
                        "Holding Percent": [0.4, 0.35],
                    },
                    index=["ACME", "BETA"],
                )
            },
        )()

    monkeypatch.setattr(holdings_module.yf, "Ticker", lambda _ticker: FakeTicker())

    result = get_existing_exposure(
        HoldingsPortfolio(holdings=[Holding(ticker="GRID", weight_pct=100)]),
        as_of=NOW,
        provider=YahooHoldingsProvider(),
    )

    assert result.exposures == {
        "ACME": 40,
        "BETA": 35,
        "GRID:UNKNOWN/OTHER": 25,
    }


def test_get_existing_exposure_retains_failed_etf_as_unknown_with_warning() -> None:
    class FailedETFProvider:
        def get_etf_composition(self, ticker: str, _as_of: datetime):
            raise RuntimeError("provider offline")

    result = get_existing_exposure(
        HoldingsPortfolio(
            holdings=[
                Holding(ticker="GRID", weight_pct=100, instrument_type="etf")
            ]
        ),
        as_of=NOW,
        provider=FailedETFProvider(),
    )

    assert result.exposures == {"GRID:UNKNOWN/OTHER": 100}
    assert result.coverage_pct == 0
    assert result.warnings == [
        "GRID: ETF composition unavailable (provider offline)."
    ]


def test_get_existing_exposure_rejects_explicit_weights_above_one_hundred() -> None:
    class StockProvider:
        def get_etf_composition(self, ticker: str, _as_of: datetime):
            return None

    result = get_existing_exposure(
        HoldingsPortfolio(
            holdings=[
                Holding(ticker="ACME", weight_pct=50.005),
                Holding(ticker="ACME", weight_pct=50.005),
            ]
        ),
        as_of=NOW,
        provider=StockProvider(),
    )

    assert result.exposures == {}
    assert result.coverage_pct == 0
    assert result.warnings == ["Explicit holding weights exceed 100%."]


def test_get_existing_exposure_fails_closed_on_etf_weights_above_one_hundred() -> None:
    class InvalidETFProvider:
        def get_etf_composition(self, ticker: str, _as_of: datetime):
            return ETFComposition(
                ticker=ticker,
                holdings={"ACME": 50.005, "BETA": 50.005},
                as_of=NOW,
                source="fixture",
            )

    result = get_existing_exposure(
        HoldingsPortfolio(
            holdings=[
                Holding(ticker="GRID", weight_pct=100, instrument_type="etf")
            ]
        ),
        as_of=NOW,
        provider=InvalidETFProvider(),
    )

    assert result.exposures == {"GRID:UNKNOWN/OTHER": 100}
    assert result.coverage_pct == 0
    assert result.warnings[0].startswith("GRID: ETF composition unavailable")


def test_analyze_overlap_uses_the_proposed_position_addition() -> None:
    overlaps = analyze_overlap(
        ExposureAnalysis(exposures={"ACME": 20}, coverage_pct=100),
        ExposureAnalysis(
            exposures={"ACME": 10, "OTHER": 90},
            coverage_pct=100,
        ),
    )

    assert overlaps == [
        {
            "ticker": "ACME",
            "existing_pct": 20,
            "new_pct": 10,
            "combined_pct": 30,
        }
    ]


def test_holdings_command_reports_actual_direct_addition(monkeypatch) -> None:
    class FakeProvider:
        def get_etf_composition(self, ticker: str, _as_of: datetime):
            if ticker == "VT":
                return ETFComposition(
                    ticker="VT",
                    holdings={"BETA": 90},
                    as_of=NOW,
                    source="fixture",
                    reason="fixture composition is partial",
                )
            return None

    monkeypatch.setattr(holdings_module, "DEFAULT_PROVIDER", FakeProvider())
    runner = CliRunner()
    with runner.isolated_filesystem():
        allocation_dir = holdings_module.Path("data/allocations")
        allocation_dir.mkdir(parents=True)
        (allocation_dir / "20260909_allocation.json").write_text(
            json.dumps(
                {
                    "positions": [
                        {
                            "ticker": "ACME",
                            "instrument_type": "stock",
                            "sleeve": "thematic",
                            "weight_pct": 10,
                            "currency": "USD",
                            "entry_price": 10,
                            "price_timestamp": NOW.isoformat(),
                        },
                        {
                            "ticker": "VT",
                            "instrument_type": "etf",
                            "sleeve": "core",
                            "weight_pct": 90,
                            "currency": "USD",
                            "entry_price": 100,
                            "price_timestamp": NOW.isoformat(),
                        },
                    ],
                    "entries": [
                        {
                            "theme": "Grid",
                            "vehicle": "ACME, OTHER",
                            "pct_allocation": 30,
                            "entry_prices": {"ACME": 10, "OTHER": 20},
                        }
                    ],
                    "core_pct": 70,
                }
            )
        )
        existing_path = holdings_module.Path("existing.json")
        existing_path.write_text(
            json.dumps(
                [
                    {"ticker": "ACME", "weight_pct": 20},
                    {"ticker": "OTHER", "weight_pct": 80},
                ]
            )
        )

        result = runner.invoke(
            cli,
            ["holdings", "--file", str(existing_path), "--base-currency", "USD"],
        )

    assert result.exit_code == 0
    assert "Proposed portfolio adds 10.0%" in result.output
    assert "adds 30.0%" not in result.output
    assert "Proposed exposure coverage: 91.0%" in result.output
    assert "fixture composition is partial" in result.output
