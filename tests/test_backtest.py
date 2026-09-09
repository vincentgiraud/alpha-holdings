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
            return pd.DataFrame({"Close": [100.0, prices[self.ticker]]})

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
                {"Close": values},
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
            return pd.DataFrame({"Close": [100.0, 110.0]})

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
