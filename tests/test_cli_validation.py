from __future__ import annotations

import click
import pytest
from click.testing import CliRunner

from alpha_holdings.cli import cli
from alpha_holdings.llm import CodexCLIError


@pytest.fixture
def guard_command_callback(monkeypatch: pytest.MonkeyPatch):
    """Fail fast if an invalid command reaches its provider-owning callback."""

    def guard(command_name: str) -> None:
        def unexpected_callback(**_: object) -> None:
            raise AssertionError(f"{command_name} callback must not run")

        command = cli.commands[command_name]
        monkeypatch.setattr(command, "callback", unexpected_callback)

    return guard


@pytest.fixture
def capture_command_callback(monkeypatch: pytest.MonkeyPatch):
    """Capture validated values at the public command callback boundary."""

    def capture(command_name: str) -> dict[str, object]:
        received: dict[str, object] = {}

        def recording_callback(**values: object) -> None:
            received.update(values)

        command = cli.commands[command_name]
        monkeypatch.setattr(command, "callback", recording_callback)
        return received

    return capture


@pytest.mark.parametrize("capital", ["-1", "0", "nan", "inf", "-inf"])
def test_discover_rejects_non_positive_or_non_finite_capital(
    capital: str, guard_command_callback
) -> None:
    guard_command_callback("discover")
    result = CliRunner().invoke(cli, ["discover", "--capital", capital])

    assert result.exit_code == 2
    assert "Invalid value for '--capital'" in result.output
    assert "finite and greater than zero" in result.output


@pytest.mark.parametrize("currency", ["", "US", "USDD", "XYZ", "12A"])
def test_discover_rejects_unsupported_base_currency(
    currency: str, guard_command_callback
) -> None:
    guard_command_callback("discover")
    result = CliRunner().invoke(cli, ["discover", "--base-currency", currency])

    assert result.exit_code == 2
    assert "Invalid value for '--base-currency'" in result.output
    assert "supported ISO currency code" in result.output


@pytest.mark.parametrize("score", ["-1", "101", "nan", "inf", "-inf"])
def test_watchlist_rejects_out_of_range_or_non_finite_score(
    score: str, guard_command_callback
) -> None:
    guard_command_callback("watchlist")
    result = CliRunner().invoke(cli, ["watchlist", "--min-score", score])

    assert result.exit_code == 2
    assert "Invalid value for '--min-score'" in result.output
    assert "finite and between 0 and 100" in result.output


@pytest.mark.parametrize("date_value", ["2026-01-01", "20260230", "2026011", "not-a-date"])
@pytest.mark.parametrize(
    ("command_name", "option_name"),
    [("monitor", "--since"), ("backtest", "--from"), ("backtest", "--to")],
)
def test_date_options_require_real_yyyymmdd_dates(
    command_name: str,
    option_name: str,
    date_value: str,
    guard_command_callback,
) -> None:
    guard_command_callback(command_name)
    result = CliRunner().invoke(cli, [command_name, option_name, date_value])

    assert result.exit_code == 2
    assert f"Invalid value for '{option_name}'" in result.output
    assert "valid date in YYYYMMDD format" in result.output


@pytest.mark.parametrize(
    ("command_name", "option_name"),
    [("monitor", "--since"), ("backtest", "--from"), ("backtest", "--to")],
)
def test_date_options_reject_future_dates(
    command_name: str, option_name: str, guard_command_callback
) -> None:
    guard_command_callback(command_name)
    result = CliRunner().invoke(cli, [command_name, option_name, "20991231"])

    assert result.exit_code == 2
    assert f"Invalid value for '{option_name}'" in result.output
    assert "must not be in the future" in result.output


def test_backtest_rejects_an_end_before_the_start(guard_command_callback) -> None:
    guard_command_callback("backtest")
    result = CliRunner().invoke(
        cli, ["backtest", "--from", "20260102", "--to", "20260101"]
    )

    assert result.exit_code == 2
    assert "--to must not be before --from" in result.output


@pytest.mark.parametrize("benchmark", ["", " ", ".", "SP Y", "../SPY", "SPY,QQQ"])
def test_backtest_rejects_empty_or_malformed_benchmark(
    benchmark: str, guard_command_callback
) -> None:
    guard_command_callback("backtest")
    result = CliRunner().invoke(cli, ["backtest", "--benchmark", benchmark])

    assert result.exit_code == 2
    assert "Invalid value for '--benchmark'" in result.output
    assert "valid ticker symbol" in result.output


def test_discover_accepts_positive_capital_and_supported_currency(
    capture_command_callback,
) -> None:
    received = capture_command_callback("discover")

    result = CliRunner().invoke(
        cli, ["discover", "--capital", "0.01", "--base-currency", "eur"]
    )

    assert result.exit_code == 0
    assert received["capital"] == 0.01
    assert received["base_currency"] == "EUR"


def test_codex_failure_is_rendered_as_a_concise_cli_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def usage_limited(**_: object) -> None:
        raise CodexCLIError("You've hit your usage limit", retryable=False)

    monkeypatch.setattr(cli.commands["discover"], "callback", usage_limited)

    result = CliRunner().invoke(cli, ["discover"])

    assert result.exit_code == 1
    assert result.output == (
        "Error: AI research unavailable: You've hit your usage limit\n"
    )


@pytest.mark.parametrize("score", ["0", "100"])
def test_watchlist_accepts_score_boundaries(
    score: str, capture_command_callback
) -> None:
    received = capture_command_callback("watchlist")

    result = CliRunner().invoke(cli, ["watchlist", "--min-score", score])

    assert result.exit_code == 0
    assert received["min_score"] == float(score)


def test_backtest_accepts_an_ordered_historical_range(
    capture_command_callback,
) -> None:
    received = capture_command_callback("backtest")

    result = CliRunner().invoke(
        cli, ["backtest", "--from", "20260101", "--to", "20260102"]
    )

    assert result.exit_code == 0
    assert received["from_date"] == "20260101"
    assert received["to_date"] == "20260102"


@pytest.mark.parametrize("benchmark", ["SPY", "BRK-B", "IWDA.AS", "^GSPC", "EURUSD=X"])
def test_backtest_accepts_supported_ticker_shapes(
    benchmark: str, capture_command_callback
) -> None:
    received = capture_command_callback("backtest")

    result = CliRunner().invoke(cli, ["backtest", "--benchmark", benchmark])

    assert result.exit_code == 0
    assert received["benchmark"] == benchmark
