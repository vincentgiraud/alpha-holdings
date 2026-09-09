from __future__ import annotations

import pandas as pd

from alpha_holdings import backtest as backtest_module
from alpha_holdings.backtest import (
    compute_returns,
    compute_risk_metrics,
    score_validation,
    score_validation_report,
    theme_attribution,
)


def test_compute_returns_tracks_authoritative_etf_positions(monkeypatch) -> None:
    prices = {"GRID": 55.0, "VT": 137.5, "SPY": 120.0}

    class FakeTicker:
        def __init__(self, ticker: str) -> None:
            self.ticker = ticker
            self.info = {"regularMarketPrice": prices[ticker]}

        def history(self, **_kwargs):
            return pd.DataFrame(
                {
                    "Close": [100.0, prices[self.ticker]],
                    "Adj Close": [100.0, prices[self.ticker]],
                }
            )

    monkeypatch.setattr(backtest_module.yf, "Ticker", FakeTicker)
    allocation = {
        "positions": [
            {
                "ticker": "GRID",
                "instrument_type": "etf",
                "sleeve": "thematic",
                "weight_pct": 32,
                "entry_price": 50,
            },
            {
                "ticker": "VT",
                "instrument_type": "etf",
                "sleeve": "core",
                "weight_pct": 68,
                "entry_price": 125,
            },
        ],
        "entries": [
            {
                "theme": "Grid Modernization",
                "vehicle": "GRID",
                "pct_allocation": 32,
                "entry_prices": {"GRID": 50},
            }
        ],
        "core_pct": 68,
    }

    result = compute_returns(allocation, "20260901", "20260908", "SPY")

    assert [row["ticker"] for row in result["ticker_returns"]] == ["GRID", "VT"]
    assert result["blended_return"] == 10


def test_compute_risk_metrics_includes_authoritative_etf_positions(
    monkeypatch,
) -> None:
    index = pd.date_range("2026-09-01", periods=3, tz="UTC")

    class FakeTicker:
        def __init__(self, ticker: str) -> None:
            self.ticker = ticker

        def history(self, **_kwargs):
            values = {
                "GRID": [50.0],
                "VT": [100.0, 110.0, 121.0],
                "SPY": [100.0, 105.0, 110.0],
            }[self.ticker]
            return pd.DataFrame(
                {"Close": values, "Adj Close": values},
                index=index[: len(values)],
            )

    monkeypatch.setattr(backtest_module.yf, "Ticker", FakeTicker)
    allocation = {
        "positions": [
            {
                "ticker": "GRID",
                "instrument_type": "etf",
                "sleeve": "thematic",
                "weight_pct": 32,
                "entry_price": 50,
            },
            {
                "ticker": "VT",
                "instrument_type": "etf",
                "sleeve": "core",
                "weight_pct": 68,
                "entry_price": 100,
            },
        ],
        "entries": [
            {
                "theme": "Grid Modernization",
                "vehicle": "GRID",
                "pct_allocation": 32,
                "entry_prices": {"GRID": 50},
            }
        ],
    }

    result = compute_risk_metrics(allocation, "20260901", "20260908", "SPY")

    assert result["incomplete"] is True
    assert result["missing_tickers"] == ["GRID"]
    assert result["trading_days"] == 0
    assert result["total_return"] is None


def test_compute_returns_fails_closed_when_a_funded_etf_has_no_price(
    monkeypatch,
) -> None:
    class FakeTicker:
        def __init__(self, ticker: str) -> None:
            self.ticker = ticker

        @property
        def info(self):
            prices = {"GRID": None, "VT": 110.0, "SPY": 110.0}
            return {"regularMarketPrice": prices[self.ticker]}

        def history(self, **_kwargs):
            if self.ticker == "GRID":
                return pd.DataFrame()
            return pd.DataFrame(
                {"Close": [100.0, 110.0], "Adj Close": [100.0, 110.0]}
            )

    monkeypatch.setattr(backtest_module.yf, "Ticker", FakeTicker)
    allocation = {
        "positions": [
            {
                "ticker": "GRID",
                "instrument_type": "etf",
                "sleeve": "thematic",
                "weight_pct": 40,
                "entry_price": 50,
            },
            {
                "ticker": "VT",
                "instrument_type": "etf",
                "sleeve": "core",
                "weight_pct": 60,
                "entry_price": 100,
            },
        ],
        "entries": [
            {
                "theme": "Grid Modernization",
                "vehicle": "GRID",
                "pct_allocation": 40,
                "entry_prices": {"GRID": 50},
            }
        ],
    }

    result = compute_returns(allocation, "20260901", "20260908", "SPY")

    assert result["incomplete"] is True
    assert result["missing_tickers"] == ["GRID"]
    assert result["thematic_return"] is None
    assert result["blended_return"] is None
    assert result["alpha"] is None


def test_theme_attribution_uses_authoritative_etf_weight(monkeypatch) -> None:
    prices = {"GRID": 55.0, "VT": 110.0}

    class FakeTicker:
        def __init__(self, ticker: str) -> None:
            self.info = {"regularMarketPrice": prices[ticker]}

    monkeypatch.setattr(backtest_module.yf, "Ticker", FakeTicker)
    allocation = {
        "positions": [
            {
                "ticker": "GRID",
                "instrument_type": "etf",
                "sleeve": "thematic",
                "weight_pct": 32,
                "entry_price": 50,
            },
            {
                "ticker": "VT",
                "instrument_type": "etf",
                "sleeve": "core",
                "weight_pct": 68,
                "entry_price": 100,
            },
        ],
        "entries": [
            {
                "theme": "Grid Modernization",
                "vehicle": "GRID",
                "pct_allocation": 5,
                "entry_prices": {"GRID": 50},
            }
        ],
    }

    result = theme_attribution(
        allocation,
        {"Grid Modernization": []},
        [{"name": "Grid Modernization", "confidence_score": 8}],
    )

    assert result[0]["weight_pct"] == 32
    assert result[0]["return_pct"] == 10
    assert result[0]["contribution"] == 3.2


def test_compute_returns_uses_bounded_adjusted_prices_and_cash_weight(
    monkeypatch,
) -> None:
    dates = pd.to_datetime(["2026-09-01", "2026-09-08", "2026-09-09"])
    histories = {
        "GRID": pd.DataFrame(
            {"Close": [50.0, 60.0, 500.0], "Adj Close": [100.0, 120.0, 999.0]},
            index=dates,
        ),
        "VT": pd.DataFrame(
            {"Close": [100.0, 110.0, 111.0], "Adj Close": [200.0, 220.0, 222.0]},
            index=dates,
        ),
        "SPY": pd.DataFrame(
            {"Close": [50.0, 55.0, 56.0], "Adj Close": [100.0, 110.0, 111.0]},
            index=dates,
        ),
    }

    class HistoricalTicker:
        def __init__(self, ticker: str) -> None:
            self.ticker = ticker

        @property
        def info(self):
            raise AssertionError("backtest must not read a current quote")

        def history(self, **_kwargs):
            return histories[self.ticker]

    monkeypatch.setattr(backtest_module.yf, "Ticker", HistoricalTicker)
    allocation = {
        "positions": [
            {
                "ticker": "GRID",
                "instrument_type": "etf",
                "sleeve": "thematic",
                "weight_pct": 40,
                "entry_price": 100,
            },
            {
                "ticker": "VT",
                "instrument_type": "etf",
                "sleeve": "core",
                "weight_pct": 40,
                "entry_price": 200,
            },
            {
                "ticker": "CASH",
                "instrument_type": "cash",
                "sleeve": "cash",
                "weight_pct": 20,
                "entry_price": 1,
            },
        ],
        "entries": [
            {
                "theme": "Grid Modernization",
                "vehicle": "GRID",
                "pct_allocation": 40,
            }
        ],
        "core_pct": 40,
        "cash_pct": 20,
    }

    result = compute_returns(allocation, "20260901", "20260908", "SPY")

    assert result["to_date"] == "20260908"
    assert {row["ticker"]: row["return_pct"] for row in result["ticker_returns"]} == {
        "GRID": 20.0,
        "VT": 10.0,
        "CASH": 0.0,
    }
    assert result["thematic_return"] == 20.0
    assert result["core_return"] == 10.0
    assert result["cash_return"] == 0.0
    assert result["blended_return"] == 12.0
    assert result["benchmark_return"] == 10.0
    assert result["incomplete"] is False
    assert result["coverage"]["weight_with_data_pct"] == 100.0
    assert result["assumptions"]["price_basis"] == "adjusted_close"


def test_compute_risk_metrics_preserves_cash_weight_in_portfolio_returns(
    monkeypatch,
) -> None:
    dates = pd.to_datetime(["2026-09-01", "2026-09-08"])
    histories = {
        "GRID": pd.DataFrame(
            {"Close": [50.0, 55.0], "Adj Close": [100.0, 110.0]},
            index=dates,
        ),
        "VT": pd.DataFrame(
            {"Close": [100.0, 110.0], "Adj Close": [100.0, 120.0]},
            index=dates,
        ),
        "SPY": pd.DataFrame(
            {"Close": [100.0, 105.0], "Adj Close": [100.0, 105.0]},
            index=dates,
        ),
    }

    class HistoricalTicker:
        def __init__(self, ticker: str) -> None:
            self.ticker = ticker

        def history(self, **_kwargs):
            return histories[self.ticker]

    monkeypatch.setattr(backtest_module.yf, "Ticker", HistoricalTicker)
    allocation = {
        "positions": [
            {
                "ticker": "GRID",
                "instrument_type": "etf",
                "sleeve": "thematic",
                "weight_pct": 40,
                "entry_price": 100,
            },
            {
                "ticker": "VT",
                "instrument_type": "etf",
                "sleeve": "core",
                "weight_pct": 40,
                "entry_price": 100,
            },
            {
                "ticker": "CASH",
                "instrument_type": "cash",
                "sleeve": "cash",
                "weight_pct": 20,
                "entry_price": 1,
            },
        ],
        "entries": [{"theme": "Grid", "vehicle": "GRID", "pct_allocation": 40}],
        "core_pct": 40,
        "cash_pct": 20,
    }

    result = compute_risk_metrics(allocation, "20260901", "20260908", "SPY")

    assert result["total_return"] == 12.0
    assert result["incomplete"] is False
    assert result["coverage"]["weight_with_data_pct"] == 100.0
    assert result["cash_return_pct"] == 0.0


def test_compute_returns_discloses_fx_dividend_and_cost_assumptions() -> None:
    dates = pd.to_datetime(["2026-09-01", "2026-09-08"])
    allocation = {
        "positions": [
            {
                "ticker": "US",
                "instrument_type": "stock",
                "sleeve": "thematic",
                "weight_pct": 50,
                "currency": "USD",
                "entry_price": 100,
            },
            {
                "ticker": "EU",
                "instrument_type": "etf",
                "sleeve": "defensive",
                "weight_pct": 30,
                "currency": "EUR",
                "entry_price": 100,
            },
            {
                "ticker": "CASH",
                "instrument_type": "cash",
                "sleeve": "cash",
                "weight_pct": 20,
                "currency": "USD",
                "entry_price": 1,
            },
        ],
        "entries": [{"theme": "US", "vehicle": "US", "pct_allocation": 50}],
        "core_pct": 0,
        "defensive_pct": 30,
        "cash_pct": 20,
    }
    prices = {
        "US": pd.Series([100.0, 110.0], index=dates),
        "EU": pd.Series([100.0, 105.0], index=dates),
        "SPY": pd.Series([100.0, 103.0], index=dates),
    }

    result = compute_returns(
        allocation,
        "20260901",
        "20260908",
        prices=prices,
    )

    assumptions = result["assumptions"]
    assert assumptions["price_basis"] == "adjusted_close"
    assert assumptions["dividend_treatment"] == "included in adjusted close"
    assert assumptions["fee_treatment"].startswith("no additional fees")
    assert assumptions["fx_treatment"].startswith("no FX conversion")
    assert assumptions["transaction_cost_pct"] == 0.0
    assert assumptions["cash_return_pct"] == 0.0
    assert assumptions["risk_free_rate_pct"] == 0.0


def test_legacy_defensive_weight_is_explicitly_incomplete() -> None:
    dates = pd.to_datetime(["2026-09-01", "2026-09-08"])
    allocation = {
        "entries": [{"theme": "Grid", "vehicle": "GRID", "pct_allocation": 40}],
        "core_pct": 40,
        "defensive_pct": 20,
    }
    prices = {
        "GRID": pd.Series([100.0, 110.0], index=dates),
        "SPY": pd.Series([100.0, 105.0], index=dates),
    }

    result = compute_returns(
        allocation,
        "20260901",
        "20260908",
        prices=prices,
    )

    assert result["defensive_return"] is None
    assert result["blended_return"] is None
    assert result["incomplete"] is True
    assert result["coverage"]["unpriced_sleeves"] == ["defensive"]
    assert result["coverage"]["weight_total_pct"] == 100.0
    assert result["coverage"]["weight_with_data_pct"] == 80.0


def test_full_backtest_reuses_one_historical_panel_for_all_analyses(monkeypatch) -> None:
    dates = pd.to_datetime(["2026-09-01", "2026-09-08"])
    panel = {
        ticker: pd.Series([100.0, 110.0], index=dates)
        for ticker in ("GRID", "VT", "SPY", "A", "B", "C")
    }
    allocation = {
        "positions": [
            {
                "ticker": "GRID",
                "instrument_type": "etf",
                "sleeve": "thematic",
                "weight_pct": 40,
                "entry_price": 100,
            },
            {
                "ticker": "VT",
                "instrument_type": "etf",
                "sleeve": "core",
                "weight_pct": 40,
                "entry_price": 100,
            },
            {
                "ticker": "CASH",
                "instrument_type": "cash",
                "sleeve": "cash",
                "weight_pct": 20,
                "entry_price": 1,
            },
        ],
        "entries": [{"theme": "Grid", "vehicle": "GRID", "pct_allocation": 40}],
        "core_pct": 40,
        "cash_pct": 20,
    }
    scores = {
        "Grid": [
            {
                "ticker": ticker,
                "composite_score": score,
                "fundamental_score": score,
                "thesis_alignment_score": score,
                "pricing_gap_score": score,
            }
            for ticker, score in (("GRID", 90), ("A", 80), ("B", 70), ("C", 60))
        ]
    }
    themes = [
        {
            "name": "Grid",
            "confidence_score": 8,
            "sub_themes": [
                {
                    "companies": [
                        {"ticker": "GRID", "supply_chain_tier": "tier_3_picks_and_shovels"}
                    ]
                }
            ],
        }
    ]
    calls: list[tuple[list[str], str, str]] = []

    def fake_fetch(tickers, start, end):
        calls.append((list(tickers), start.strftime("%Y%m%d"), end.strftime("%Y%m%d")))
        return panel

    monkeypatch.setattr(
        backtest_module,
        "load_snapshot",
        lambda _date: {"allocation": allocation, "scores": scores, "themes": themes},
    )
    monkeypatch.setattr(backtest_module, "fetch_price_history", fake_fetch)

    result = backtest_module.full_backtest("20260901", "20260908", "SPY")

    assert result is not None
    assert calls == [
        (["A", "B", "C", "GRID", "SPY", "VT"], "20260901", "20260908")
    ]
    assert result["returns"]["benchmark_return"] == 10.0
    assert result["risk_metrics"]["total_return"] == 8.0
    assert result["to_date"] == "20260908"


def _validation_scores(values: dict[str, dict[str, float]]) -> dict[str, list[dict]]:
    return {
        "Grid": [
            {
                "ticker": ticker,
                "composite_score": dimensions["composite"],
                "fundamental_score": dimensions["fundamental"],
                "thesis_alignment_score": dimensions["thesis"],
                "pricing_gap_score": dimensions["pricing"],
            }
            for ticker, dimensions in values.items()
        ]
    }


def _validation_prices(tickers: list[str]) -> dict[str, pd.Series]:
    dates = pd.to_datetime(["2026-01-01", "2026-02-01"])
    return {
        ticker: pd.Series(
            [100.0, 100.0 + (5 - index) * 10.0],
            index=dates,
        )
        for index, ticker in enumerate(tickers, 1)
    }


def _entry_prices(scores: dict[str, list[dict]]) -> dict[str, float]:
    return {score["ticker"]: 100.0 for score in scores["Grid"]}


def test_score_validation_uses_unallocated_candidates_from_the_cohort() -> None:
    scores = _validation_scores(
        {
            "A": {"composite": 90, "fundamental": 90, "thesis": 90, "pricing": 90},
            "B": {"composite": 80, "fundamental": 80, "thesis": 80, "pricing": 80},
            "C": {"composite": 70, "fundamental": 70, "thesis": 70, "pricing": 70},
            "D": {"composite": 60, "fundamental": 60, "thesis": 60, "pricing": 60},
        }
    )
    cohort = {
        "cohort_id": "20260101-run-a",
        "captured_at": "2026-01-01T00:00:00+00:00",
        "scores": scores,
        "entry_prices": _entry_prices(scores),
    }

    result = score_validation(
        {
            "positions": [
                {
                    "ticker": "A",
                    "sleeve": "thematic",
                    "weight_pct": 100,
                    "entry_price": 100,
                }
            ]
        },
        scores,
        prices=_validation_prices(["A", "B", "C", "D"]),
        from_date="20260101",
        to_date="20260201",
        cohorts=[cohort],
    )

    composite = next(row for row in result if row["dimension"] == "composite_score")
    assert composite["n_companies"] == 4
    assert composite["n_observations"] == 4
    assert composite["cohort_count"] == 1
    assert composite["rank_correlation"] == 1.0
    assert composite["horizon_days_min"] == 31
    assert composite["horizon_days_max"] == 31
    assert composite["coverage"]["forward_return_coverage_pct"] == 100.0
    assert composite["limitations"] == []


def test_full_backtest_passes_all_persisted_cohorts_to_score_validation(monkeypatch) -> None:
    dates = pd.to_datetime(["2026-01-01", "2026-02-01"])
    panel = {
        ticker: pd.Series([100.0, 110.0], index=dates)
        for ticker in ("A", "B", "C", "D", "SPY")
    }
    scores = _validation_scores(
        {
            "A": {"composite": 90, "fundamental": 90, "thesis": 90, "pricing": 90},
            "B": {"composite": 80, "fundamental": 80, "thesis": 80, "pricing": 80},
            "C": {"composite": 70, "fundamental": 70, "thesis": 70, "pricing": 70},
            "D": {"composite": 60, "fundamental": 60, "thesis": 60, "pricing": 60},
        }
    )
    cohorts = [
        {
            "cohort_id": "20260101-run-a",
            "captured_at": "2026-01-01T00:00:00+00:00",
            "scores": scores,
            "entry_prices": _entry_prices(scores),
        },
        {
            "cohort_id": "20260115-run-b",
            "captured_at": "2026-01-15T00:00:00+00:00",
            "scores": scores,
            "entry_prices": _entry_prices(scores),
        },
    ]
    allocation = {
        "positions": [
            {
                "ticker": "A",
                "instrument_type": "stock",
                "sleeve": "thematic",
                "weight_pct": 100,
                "entry_price": 100,
            }
        ],
        "entries": [],
        "core_pct": 0,
        "defensive_pct": 0,
        "cash_pct": 0,
    }
    calls: list[tuple[list[str], str, str]] = []

    def fake_fetch(tickers, start, end):
        calls.append((list(tickers), start.strftime("%Y%m%d"), end.strftime("%Y%m%d")))
        return panel

    monkeypatch.setattr(
        backtest_module,
        "load_snapshot",
        lambda _date: {
            "source": "versioned",
            "allocation": allocation,
            "scores": scores,
            "themes": [],
        },
    )
    monkeypatch.setattr(backtest_module, "load_score_cohorts", lambda: cohorts)
    monkeypatch.setattr(backtest_module, "fetch_price_history", fake_fetch)

    result = backtest_module.full_backtest("20260101", "20260201", "SPY")

    assert result is not None
    assert calls == [
        (["A", "B", "C", "D", "SPY"], "20260101", "20260201")
    ]
    assert result["score_validation_summary"]["cohort_count"] == 2
    assert result["score_validation_summary"]["observation_count"] == 8
    assert result["score_validation"][0]["n_observations"] == 8


def test_score_validation_combines_cohorts_and_deduplicates_within_each_run() -> None:
    base_scores = _validation_scores(
        {
            "A": {"composite": 90, "fundamental": 90, "thesis": 90, "pricing": 90},
            "B": {"composite": 80, "fundamental": 80, "thesis": 80, "pricing": 80},
            "C": {"composite": 70, "fundamental": 70, "thesis": 70, "pricing": 70},
            "D": {"composite": 60, "fundamental": 60, "thesis": 60, "pricing": 60},
        }
    )
    second_scores = _validation_scores(
        {
            "A": {"composite": 85, "fundamental": 85, "thesis": 85, "pricing": 85},
            "B": {"composite": 75, "fundamental": 75, "thesis": 75, "pricing": 75},
            "C": {"composite": 65, "fundamental": 65, "thesis": 65, "pricing": 65},
            "D": {"composite": 55, "fundamental": 55, "thesis": 55, "pricing": 55},
        }
    )
    first_cohort = {
        "cohort_id": "20260101-run-a",
        "captured_at": "2026-01-01T00:00:00+00:00",
        "scores": {
            "Grid": base_scores["Grid"],
            "Duplicate theme": [
                {
                    "ticker": "A",
                    "composite_score": 70,
                    "fundamental_score": 70,
                    "thesis_alignment_score": 70,
                    "pricing_gap_score": 70,
                }
            ],
        },
        "entry_prices": _entry_prices(base_scores),
    }
    second_cohort = {
        "cohort_id": "20260115-run-b",
        "captured_at": "2026-01-15T00:00:00+00:00",
        "scores": second_scores,
        "entry_prices": _entry_prices(second_scores),
    }

    result = score_validation(
        {"positions": []},
        base_scores,
        prices=_validation_prices(["A", "B", "C", "D"]),
        from_date="20260101",
        to_date="20260201",
        cohorts=[first_cohort, second_cohort],
    )

    composite = next(row for row in result if row["dimension"] == "composite_score")
    assert composite["n_companies"] == 4
    assert composite["n_observations"] == 8
    assert composite["cohort_count"] == 2
    assert "one observation per ticker per cohort" in composite["duplicate_ticker_policy"]


def test_score_validation_returns_unavailable_for_constant_scores_and_handles_ties() -> None:
    scores = _validation_scores(
        {
            "A": {"composite": 50, "fundamental": 10, "thesis": 50, "pricing": 50},
            "B": {"composite": 50, "fundamental": 10, "thesis": 50, "pricing": 50},
            "C": {"composite": 50, "fundamental": 20, "thesis": 50, "pricing": 50},
            "D": {"composite": 50, "fundamental": 30, "thesis": 50, "pricing": 50},
        }
    )
    cohort = {
        "cohort_id": "20260101-run-a",
        "captured_at": "2026-01-01T00:00:00+00:00",
        "scores": scores,
        "entry_prices": _entry_prices(scores),
    }

    result = score_validation(
        {"positions": []},
        scores,
        prices=_validation_prices(["A", "B", "C", "D"]),
        from_date="20260101",
        to_date="20260201",
        cohorts=[cohort],
    )

    composite = next(row for row in result if row["dimension"] == "composite_score")
    fundamental = next(row for row in result if row["dimension"] == "fundamental_score")
    assert composite["rank_correlation"] is None
    assert composite["spread"] is None
    assert composite["top_quartile_return"] is None
    assert fundamental["rank_correlation"] < 0


def test_score_validation_returns_unavailable_for_constant_returns() -> None:
    scores = _validation_scores(
        {
            "A": {"composite": 90, "fundamental": 90, "thesis": 90, "pricing": 90},
            "B": {"composite": 80, "fundamental": 80, "thesis": 80, "pricing": 80},
            "C": {"composite": 70, "fundamental": 70, "thesis": 70, "pricing": 70},
            "D": {"composite": 60, "fundamental": 60, "thesis": 60, "pricing": 60},
        }
    )
    cohort = {
        "cohort_id": "20260101-run-a",
        "captured_at": "2026-01-01T00:00:00+00:00",
        "scores": scores,
        "entry_prices": _entry_prices(scores),
    }
    dates = pd.to_datetime(["2026-01-01", "2026-02-01"])
    prices = {
        ticker: pd.Series([100.0, 110.0], index=dates)
        for ticker in ("A", "B", "C", "D")
    }

    result = score_validation(
        {"positions": []},
        scores,
        prices=prices,
        from_date="20260101",
        to_date="20260201",
        cohorts=[cohort],
    )

    assert all(row["rank_correlation"] is None for row in result)
    assert all(row["spread"] is None for row in result)


def test_score_validation_reports_sparse_cohort_coverage_and_small_sample_limit() -> None:
    scores = _validation_scores(
        {
            "A": {"composite": 90, "fundamental": 90, "thesis": 90, "pricing": 90},
            "B": {"composite": 80, "fundamental": 80, "thesis": 80, "pricing": 80},
            "C": {"composite": 70, "fundamental": 70, "thesis": 70, "pricing": 70},
            "D": {"composite": 60, "fundamental": 60, "thesis": 60, "pricing": 60},
        }
    )
    cohort = {
        "cohort_id": "20260101-run-a",
        "captured_at": "2026-01-01T00:00:00+00:00",
        "scores": scores,
        "entry_prices": {"A": 100.0, "B": 100.0, "C": 100.0},
    }
    prices = _validation_prices(["A", "B", "C"])

    report = score_validation_report(
        {"positions": []},
        scores,
        prices=prices,
        from_date="20260101",
        to_date="20260201",
        cohorts=[cohort],
    )

    assert report["summary"]["candidate_count"] == 4
    assert report["summary"]["observation_count"] == 3
    assert report["summary"]["forward_return_coverage_pct"] == 75.0
    composite = next(row for row in report["dimensions"] if row["dimension"] == "composite_score")
    assert composite["sufficient_data"] is False
    assert any("same-run entry price" in limitation for limitation in composite["limitations"])


def test_score_validation_rejects_legacy_prices_without_dates() -> None:
    scores = _validation_scores(
        {
            "A": {"composite": 90, "fundamental": 90, "thesis": 90, "pricing": 90},
            "B": {"composite": 80, "fundamental": 80, "thesis": 80, "pricing": 80},
            "C": {"composite": 70, "fundamental": 70, "thesis": 70, "pricing": 70},
            "D": {"composite": 60, "fundamental": 60, "thesis": 60, "pricing": 60},
        }
    )
    report = score_validation_report(
        {"positions": []},
        scores,
        prices=_validation_prices(["A", "B", "C", "D"]),
        from_date="20260101",
        to_date="20260201",
        cohorts=[
            {
                "cohort_id": "20260101-legacy",
                "captured_at": "2026-01-01",
                "scores": scores,
                "entry_prices": _entry_prices(scores),
                "requires_dated_entry_prices": True,
            }
        ],
    )

    assert report["summary"]["observation_count"] == 0
    assert any(
        "dated same-run entry price" in limitation
        for limitation in report["summary"]["limitations"]
    )


def test_score_validation_uses_average_ranks_for_partial_ties() -> None:
    scores = _validation_scores(
        {
            "A": {"composite": 1, "fundamental": 1, "thesis": 1, "pricing": 1},
            "B": {"composite": 1, "fundamental": 1, "thesis": 1, "pricing": 1},
            "C": {"composite": 2, "fundamental": 2, "thesis": 2, "pricing": 2},
            "D": {"composite": 3, "fundamental": 3, "thesis": 3, "pricing": 3},
        }
    )
    cohort = {
        "cohort_id": "20260101-run-a",
        "captured_at": "2026-01-01",
        "scores": scores,
        "entry_prices": _entry_prices(scores),
    }
    dates = pd.to_datetime(["2026-01-01", "2026-02-01"])
    prices = {
        ticker: pd.Series([100.0, 100.0 + index * 10], index=dates)
        for index, ticker in enumerate(("A", "B", "C", "D"), 1)
    }

    result = score_validation(
        {"positions": []},
        scores,
        prices=prices,
        from_date="20260101",
        to_date="20260201",
        cohorts=[cohort],
    )

    composite = next(row for row in result if row["dimension"] == "composite_score")
    assert composite["rank_correlation"] == 0.949


def test_score_validation_reports_perfectly_reversed_ranks() -> None:
    scores = _validation_scores(
        {
            "A": {"composite": 90, "fundamental": 90, "thesis": 90, "pricing": 90},
            "B": {"composite": 80, "fundamental": 80, "thesis": 80, "pricing": 80},
            "C": {"composite": 70, "fundamental": 70, "thesis": 70, "pricing": 70},
            "D": {"composite": 60, "fundamental": 60, "thesis": 60, "pricing": 60},
        }
    )
    cohort = {
        "cohort_id": "20260101-run-a",
        "captured_at": "2026-01-01",
        "scores": scores,
        "entry_prices": _entry_prices(scores),
    }
    dates = pd.to_datetime(["2026-01-01", "2026-02-01"])
    prices = {
        ticker: pd.Series([100.0, 100.0 + index * 10], index=dates)
        for index, ticker in enumerate(("A", "B", "C", "D"), 1)
    }

    result = score_validation(
        {"positions": []},
        scores,
        prices=prices,
        from_date="20260101",
        to_date="20260201",
        cohorts=[cohort],
    )

    composite = next(row for row in result if row["dimension"] == "composite_score")
    assert composite["rank_correlation"] == -1.0


def test_score_validation_fails_closed_without_a_persisted_cohort(monkeypatch) -> None:
    scores = _validation_scores(
        {
            "A": {"composite": 90, "fundamental": 90, "thesis": 90, "pricing": 90},
            "B": {"composite": 80, "fundamental": 80, "thesis": 80, "pricing": 80},
            "C": {"composite": 70, "fundamental": 70, "thesis": 70, "pricing": 70},
            "D": {"composite": 60, "fundamental": 60, "thesis": 60, "pricing": 60},
        }
    )
    monkeypatch.setattr(backtest_module, "load_score_cohorts", lambda: [])

    report = score_validation_report(
        {
            "positions": [
                {"ticker": "A", "entry_price": 100, "weight_pct": 100}
            ]
        },
        scores,
        prices=_validation_prices(["A", "B", "C", "D"]),
        from_date="20260101",
        to_date="20260201",
    )

    assert report["summary"]["candidate_count"] == 4
    assert report["summary"]["observation_count"] == 0
    assert report["dimensions"] == []
