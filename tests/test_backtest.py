from __future__ import annotations

import pandas as pd

from alpha_holdings import backtest as backtest_module
from alpha_holdings.backtest import compute_returns, compute_risk_metrics, theme_attribution


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
