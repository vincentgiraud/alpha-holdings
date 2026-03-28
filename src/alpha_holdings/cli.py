"""Command-line interface for alpha-holdings.

Entry points for workflows: refresh, score, construct, backtest, analyze, and report.
"""

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import typer

app = typer.Typer(help="Alpha Holdings: Free-Data Upgradeable Strategy Engine")


@app.command()
def version():
    """Show version information."""
    from alpha_holdings import __version__

    typer.echo(f"alpha-holdings {__version__}")


@app.command()
def check():
    """Check configuration and data availability."""
    from alpha_holdings import config

    typer.echo("Checking configuration...")
    typer.echo(f"  DATA_STORAGE_PATH: {config.DATA_STORAGE_PATH}")
    typer.echo(f"  DATABASE_URL: {config.DATABASE_URL}")
    typer.echo(f"  DATA_SOURCE: {config.DATA_SOURCE}")
    typer.echo(f"  BENCHMARK_SYMBOL: {config.BENCHMARK_SYMBOL}")
    typer.echo("✅ Configuration loaded successfully")


@app.command()
def refresh(
    universe: str = typer.Option(..., help="Path to universe CSV"),
    start_date: str | None = typer.Option(
        None, help="Start date (YYYY-MM-DD). Default: 90 days ago"
    ),
    end_date: str | None = typer.Option(None, help="End date (YYYY-MM-DD). Default: today"),
):
    """Refresh data from free sources."""
    from alpha_holdings import config
    from alpha_holdings.data.refresh import refresh_prices
    from alpha_holdings.data.storage import build_storage_backend

    universe_path = Path(universe)
    parsed_end = _parse_date_or_default(end_date, default=date.today())
    parsed_start = _parse_date_or_default(
        start_date,
        default=parsed_end - timedelta(days=90),
    )

    backend = build_storage_backend(
        backend=config.STORAGE_BACKEND,
        root_path=config.DATA_STORAGE_PATH,
        database_path=_database_path_from_url(config.DATABASE_URL),
        azure_account_url=config.AZURE_STORAGE_ACCOUNT_URL,
        azure_container=config.AZURE_STORAGE_CONTAINER,
        azure_prefix=config.AZURE_STORAGE_PREFIX,
    )

    summary = refresh_prices(
        universe_path=universe_path,
        start_date=parsed_start,
        end_date=parsed_end,
        storage=backend,
        preferred_source=config.DATA_SOURCE,
        fallback_source=config.FALLBACK_DATA_SOURCE,
    )

    typer.echo("Refresh complete")
    typer.echo(f"  Requested: {summary.tickers_requested}")
    typer.echo(f"  Succeeded: {summary.tickers_succeeded}")
    typer.echo(f"  Failed:    {summary.tickers_failed}")
    typer.echo(f"  Snapshots: {summary.snapshots_written}")
    if summary.failures:
        typer.echo(f"  Failed tickers: {', '.join(summary.failures)}")


@app.command(name="list-snapshots")
def list_snapshots(
    dataset: str | None = typer.Option(None, help="Filter by dataset name"),
):
    """List registered data snapshots."""
    from alpha_holdings import config
    from alpha_holdings.data.storage import build_storage_backend

    backend = build_storage_backend(
        backend=config.STORAGE_BACKEND,
        root_path=config.DATA_STORAGE_PATH,
        database_path=_database_path_from_url(config.DATABASE_URL),
        azure_account_url=config.AZURE_STORAGE_ACCOUNT_URL,
        azure_container=config.AZURE_STORAGE_CONTAINER,
        azure_prefix=config.AZURE_STORAGE_PREFIX,
    )

    snapshots = backend.list_snapshots(dataset_filter=dataset)
    if not snapshots:
        typer.echo("No snapshots found.")
        return

    header = f"{'DATASET':<20} {'AS_OF':<26} {'ROWS':>6}  SOURCE"
    typer.echo(header)
    typer.echo("-" * len(header))
    for s in snapshots:
        as_of = str(s["as_of"])[:19]
        source = s["metadata"].get("source", "-")
        typer.echo(f"{s['dataset']:<20} {as_of:<26} {s['row_count']:>6}  {source}")


@app.command(name="show-snapshot")
def show_snapshot(
    dataset: str = typer.Option(..., help="Dataset name (e.g. 'prices_aapl')"),
    as_of: str = typer.Option(
        ..., help="As-of datetime prefix (e.g. '2026-03-23' or full UTC stamp)"
    ),
    limit: int = typer.Option(20, help="Maximum rows to display"),
):
    """Display contents of a snapshot."""
    from alpha_holdings import config
    from alpha_holdings.data.storage import build_storage_backend

    backend = build_storage_backend(
        backend=config.STORAGE_BACKEND,
        root_path=config.DATA_STORAGE_PATH,
        database_path=_database_path_from_url(config.DATABASE_URL),
        azure_account_url=config.AZURE_STORAGE_ACCOUNT_URL,
        azure_container=config.AZURE_STORAGE_CONTAINER,
        azure_prefix=config.AZURE_STORAGE_PREFIX,
    )

    try:
        df = backend.read_snapshot(dataset=dataset, as_of=as_of)
    except FileNotFoundError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    total = len(df)
    typer.echo(f"Snapshot: dataset={dataset!r}  rows={total}")
    typer.echo(df.head(limit).to_string(index=False))
    if total > limit:
        typer.echo(f"... {total - limit} more rows (use --limit to show more)")


@app.command()
def score(date: str = typer.Option(..., help="Score as-of date prefix (YYYY-MM-DD)")):
    """Compute equity scores from persisted snapshots."""
    from alpha_holdings import config
    from alpha_holdings.data.storage import build_storage_backend
    from alpha_holdings.scoring import score_equities_from_snapshots

    backend = build_storage_backend(
        backend=config.STORAGE_BACKEND,
        root_path=config.DATA_STORAGE_PATH,
        database_path=_database_path_from_url(config.DATABASE_URL),
        azure_account_url=config.AZURE_STORAGE_ACCOUNT_URL,
        azure_container=config.AZURE_STORAGE_CONTAINER,
        azure_prefix=config.AZURE_STORAGE_PREFIX,
    )

    try:
        summary = score_equities_from_snapshots(
            storage=backend,
            as_of=date,
            lookback_days=config.SCORE_LOOKBACK_DAYS,
            min_avg_dollar_volume=config.UNIVERSE_MIN_AVG_DOLLAR_VOLUME,
            seed_universe_path=config.UNIVERSE_SEED_PATH,
            base_currency=config.UNIVERSE_BASE_CURRENCY,
        )
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    typer.echo(
        f"Scored {summary.securities_scored}/{summary.universe_size} symbols as of {summary.as_of}."
    )
    if summary.skipped:
        typer.echo(f"Skipped: {', '.join(summary.skipped)}")
    typer.echo(f"Snapshot written: {summary.snapshot_path}")
    typer.echo(
        summary.scores[
            [
                "rank",
                "symbol",
                "composite_score",
                "factor_momentum",
                "factor_low_volatility",
                "factor_liquidity",
            ]
        ].to_string(index=False)
    )


@app.command()
def construct(date: str = typer.Option(..., help="Construction date (YYYY-MM-DD)")):
    """Construct target portfolio from equity scores."""
    from alpha_holdings import config
    from alpha_holdings.data.storage import build_storage_backend
    from alpha_holdings.portfolio import construct_portfolio

    backend = build_storage_backend(
        backend=config.STORAGE_BACKEND,
        root_path=config.DATA_STORAGE_PATH,
        database_path=_database_path_from_url(config.DATABASE_URL),
        azure_account_url=config.AZURE_STORAGE_ACCOUNT_URL,
        azure_container=config.AZURE_STORAGE_CONTAINER,
        azure_prefix=config.AZURE_STORAGE_PREFIX,
    )

    try:
        result = construct_portfolio(
            storage=backend,
            as_of=date,
            seed_universe_path=config.UNIVERSE_SEED_PATH,
        )
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Constructed portfolio '{result.portfolio_id}' as of {result.as_of}")
    typer.echo(f"  Holdings:     {result.holdings_count}")
    typer.echo(f"  Total weight: {result.total_weight}")
    typer.echo(f"  Max weight:   {result.max_weight}")
    if result.turnover_vs_prior is not None:
        typer.echo(f"  Turnover:     {result.turnover_vs_prior:.2%}")
    typer.echo(f"  Snapshot:     {result.snapshot_path}")
    if result.warnings:
        typer.echo("")
        for w in result.warnings:
            typer.secho(f"  ⚠ {w}", fg=typer.colors.YELLOW)
    typer.echo("")
    typer.echo(
        result.weights[["symbol", "target_weight", "composite_score", "rank", "country"]].to_string(
            index=False
        )
    )


@app.command()
def rebalance(
    date: str = typer.Option(..., help="Rebalance as-of date (YYYY-MM-DD)"),
    portfolio_value: float = typer.Option(
        1_000_000.0, help="Total portfolio value for share calculation"
    ),
):
    """Generate trade proposals vs. current holdings."""
    from alpha_holdings import config
    from alpha_holdings.data.storage import build_storage_backend
    from alpha_holdings.portfolio import rebalance_portfolio

    backend = build_storage_backend(
        backend=config.STORAGE_BACKEND,
        root_path=config.DATA_STORAGE_PATH,
        database_path=_database_path_from_url(config.DATABASE_URL),
        azure_account_url=config.AZURE_STORAGE_ACCOUNT_URL,
        azure_container=config.AZURE_STORAGE_CONTAINER,
        azure_prefix=config.AZURE_STORAGE_PREFIX,
    )

    try:
        result = rebalance_portfolio(
            storage=backend,
            as_of=date,
            portfolio_value=portfolio_value,
            seed_universe_path=config.UNIVERSE_SEED_PATH,
        )
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Rebalance for '{result.portfolio_id}' as of {result.as_of}")
    typer.echo(f"  Portfolio value: ${result.portfolio_value:,.2f}")
    typer.echo(f"  Trades:     {result.trades_count} ({result.buys} buys, {result.sells} sells)")
    typer.echo(f"  Turnover:   {result.estimated_turnover:.2%}")
    typer.echo(f"  Snapshot:   {result.snapshot_path}")
    if not result.proposals.empty:
        typer.echo("")
        display_cols = [
            "symbol",
            "side",
            "shares",
            "price_estimate",
            "estimated_value",
            "weight_change",
        ]
        typer.echo(result.proposals[display_cols].to_string(index=False))


@app.command()
def backtest(
    start_date: str = typer.Option(..., help="Backtest start (YYYY-MM-DD)"),
    end_date: str = typer.Option(..., help="Backtest end (YYYY-MM-DD)"),
    rebalance_freq: str = typer.Option(
        "monthly", help="Rebalance frequency (weekly, monthly, quarterly)"
    ),
    benchmark: str = typer.Option("SPY", help="Benchmark symbol"),
):
    """Run historical walk-forward backtest."""
    from alpha_holdings import config
    from alpha_holdings.backtest import run_backtest
    from alpha_holdings.data.storage import build_storage_backend

    backend = build_storage_backend(
        backend=config.STORAGE_BACKEND,
        root_path=config.DATA_STORAGE_PATH,
        database_path=_database_path_from_url(config.DATABASE_URL),
        azure_account_url=config.AZURE_STORAGE_ACCOUNT_URL,
        azure_container=config.AZURE_STORAGE_CONTAINER,
        azure_prefix=config.AZURE_STORAGE_PREFIX,
    )

    try:
        result = run_backtest(
            storage=backend,
            start_date=start_date,
            end_date=end_date,
            rebalance_freq=rebalance_freq,
            seed_universe_path=config.UNIVERSE_SEED_PATH,
            benchmark_symbol=benchmark,
            lookback_days=config.SCORE_LOOKBACK_DAYS,
        )
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Backtest: {result.start_date} → {result.end_date}")
    typer.echo(f"  Rebalances:       {result.rebalance_count}")
    typer.echo(f"  Total Return:     {result.total_return:.4%}")
    typer.echo(f"  Annualized:       {result.annualized_return:.4%}")
    typer.echo(f"  Volatility:       {result.volatility:.4%}")
    typer.echo(f"  Sharpe Ratio:     {result.sharpe_ratio:.4f}")
    typer.echo(f"  Max Drawdown:     {result.max_drawdown:.4%}")
    if result.benchmark_total_return is not None:
        typer.echo(f"  Benchmark Return: {result.benchmark_total_return:.4%}")
    typer.echo(f"  Snapshot:         {result.snapshot_path}")
    if result.warnings:
        typer.echo("")
        for w in result.warnings:
            typer.secho(f"  ⚠ {w}", fg=typer.colors.YELLOW)


@app.command()
def report(
    benchmark: str = typer.Option("SPY", help="Benchmark symbol"),
    risk_free_rate: float = typer.Option(0.04, help="Annual risk-free rate"),
    html: str | None = typer.Option(None, help="Output HTML report to this file path"),
):
    """Generate performance report from latest backtest results."""
    from alpha_holdings import config
    from alpha_holdings.analytics import generate_report
    from alpha_holdings.data.storage import build_storage_backend

    backend = build_storage_backend(
        backend=config.STORAGE_BACKEND,
        root_path=config.DATA_STORAGE_PATH,
        database_path=_database_path_from_url(config.DATABASE_URL),
        azure_account_url=config.AZURE_STORAGE_ACCOUNT_URL,
        azure_container=config.AZURE_STORAGE_CONTAINER,
        azure_prefix=config.AZURE_STORAGE_PREFIX,
    )

    try:
        rpt = generate_report(
            storage=backend,
            risk_free_rate=risk_free_rate,
            benchmark_symbol=benchmark,
        )
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Performance Report: {rpt.start_date} → {rpt.end_date}")
    typer.echo(f"  Portfolio: {rpt.portfolio_id}")
    typer.echo(f"  Snapshot:  {rpt.snapshot_path}")
    typer.echo("")
    typer.echo(rpt.summary.to_string(index=False))
    if rpt.degraded_assumptions:
        typer.echo("")
        for warning in rpt.degraded_assumptions:
            typer.secho(f"  ⚠ {warning}", fg=typer.colors.YELLOW)

    # Factor attribution
    attribution = None
    try:
        from alpha_holdings.analytics import compute_factor_attribution

        attribution = compute_factor_attribution(
            storage=backend,
            start_date=rpt.start_date,
            end_date=rpt.end_date,
            seed_universe_path=config.UNIVERSE_SEED_PATH,
            risk_free_rate=risk_free_rate,
        )
        typer.echo("")
        typer.echo("Factor Attribution:")
        typer.echo(f"  Alpha (ann.):        {attribution.alpha_ann:.2%}")
        typer.echo(f"  R²:                  {attribution.r_squared:.4f}")
        typer.echo(f"  Residual Vol (ann.): {attribution.residual_vol_ann:.2%}")
        for f in attribution.factors:
            typer.echo(
                f"  {f.name:<20s} β={f.beta:+.4f}  contrib={f.contribution_ann:+.2%}  t={f.t_stat:.2f}"
            )
    except (ValueError, Exception) as exc:
        typer.secho(f"  Attribution skipped: {exc}", fg=typer.colors.YELLOW, err=True)

    # HTML report
    if html:
        from alpha_holdings.analytics import render_html_report
        from alpha_holdings.analytics.performance import _read_latest_backtest

        nav_df = _read_latest_backtest(storage=backend)
        html_path = render_html_report(
            report=rpt,
            nav_series=nav_df if nav_df is not None else pd.DataFrame(),
            attribution=attribution,
            output_path=Path(html),
        )
        typer.echo(f"\nHTML report written: {html_path}")


def _parse_date_or_default(value: str | None, *, default: date) -> date:
    if not value:
        return default
    return date.fromisoformat(value)


def _database_path_from_url(database_url: str) -> Path:
    prefix = "duckdb:///"
    if database_url.startswith(prefix):
        return Path(database_url[len(prefix) :])
    return Path(database_url)


if __name__ == "__main__":
    app()
