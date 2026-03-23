"""Tests for provider payload normalization into canonical contracts."""

from datetime import date
from decimal import Decimal

from alpha_holdings.data.normalization import (
    normalize_edgar_fundamental_rows,
    normalize_stooq_price_rows,
    normalize_yahoo_price_rows,
)


class TestYahooNormalization:
    def test_normalize_yahoo_rows_maps_aliases_and_sorts_by_date(self):
        rows = [
            {
                "Date": date(2025, 1, 3),
                "Open": "101.0",
                "High": "102.5",
                "Low": "100.1",
                "Close": "101.8",
                "Adj Close": "101.7",
                "Volume": "12345",
                "Dividends": "0.1",
                "Stock Splits": "1",
            },
            {
                "Date": date(2025, 1, 2),
                "Open": "99.0",
                "High": "100.0",
                "Low": "98.5",
                "Close": "99.8",
                "adj_close": "99.7",
                "Volume": "10000",
            },
        ]

        bars = normalize_yahoo_price_rows("AAPL", rows)

        assert len(bars) == 2
        assert bars[0].date.date() == date(2025, 1, 2)
        assert bars[1].date.date() == date(2025, 1, 3)
        assert bars[0].adjusted_close == Decimal("99.7")
        assert bars[1].adjusted_close == Decimal("101.7")
        assert bars[1].dividend == Decimal("0.1")
        assert bars[1].quality.source == "yahoo"
        assert "normalized" in bars[1].quality.data_flags

    def test_normalize_yahoo_rows_flags_missing_adjusted_close(self):
        rows = [
            {
                "Date": date(2025, 2, 1),
                "Open": "100",
                "High": "101",
                "Low": "99",
                "Close": "100.5",
                "Volume": "500",
            }
        ]

        bars = normalize_yahoo_price_rows("MSFT", rows)

        assert bars[0].adjusted_close is None
        assert "adjusted_close_missing" in bars[0].quality.data_flags


class TestStooqNormalization:
    def test_normalize_stooq_rows_sets_unadjusted_and_defaults_volume(self):
        rows = [
            {
                "Date": "2025-03-01",
                "Open": "12.0",
                "High": "12.4",
                "Low": "11.8",
                "Close": "12.1",
            }
        ]

        bars = normalize_stooq_price_rows("AAPL.US", rows)

        assert len(bars) == 1
        assert bars[0].adjusted_close is None
        assert bars[0].volume == 0
        assert "unadjusted" in bars[0].quality.data_flags
        assert bars[0].quality.source == "stooq"


class TestEdgarNormalization:
    def test_normalize_edgar_rows_infers_period_type_from_form(self):
        rows = [
            {
                "end": "2024-12-31",
                "form": "10-K",
                "revenue": "1000000",
                "net_income": "100000",
            },
            {
                "end": "2024-09-30",
                "form": "10-Q",
                "revenue": "230000",
                "net_income": "18000",
            },
        ]

        snaps = normalize_edgar_fundamental_rows("AAPL", rows)

        assert len(snaps) == 2
        assert snaps[0].period_end_date.date() == date(2024, 12, 31)
        assert snaps[0].period_type == "FY"
        assert snaps[1].period_type == "Q3"
        assert snaps[0].revenue == Decimal("1000000")
        assert snaps[0].quality.source == "edgar"
        assert "no_point_in_time" in snaps[0].quality.data_flags

    def test_normalize_edgar_rows_keeps_missing_fields_as_none(self):
        rows = [{"period_end_date": "2024-06-30", "period_type": "Q2"}]

        snap = normalize_edgar_fundamental_rows("MSFT", rows)[0]

        assert snap.revenue is None
        assert snap.debt_to_equity is None
        assert snap.period_type == "Q2"
