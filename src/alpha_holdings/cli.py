"""Command-line interface for alpha-holdings.

Entry points for workflows: refresh, score, construct, backtest, analyze, and report.
"""

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
def refresh(universe: str = typer.Option(..., help="Path to universe CSV")):
    """Refresh data from free sources."""
    typer.echo(f"Refreshing data from {universe}...")
    typer.secho("❌ Not yet implemented", fg=typer.colors.YELLOW)


@app.command()
def score(date: str = typer.Option(..., help="Score as-of date (YYYY-MM-DD)")):
    """Compute fundamental scores."""
    typer.echo(f"Scoring as of {date}...")
    typer.secho("❌ Not yet implemented", fg=typer.colors.YELLOW)


@app.command()
def construct(date: str = typer.Option(..., help="Construction date (YYYY-MM-DD)")):
    """Construct target portfolio."""
    typer.echo(f"Constructing portfolio as of {date}...")
    typer.secho("❌ Not yet implemented", fg=typer.colors.YELLOW)


@app.command()
def backtest(start_date: str = typer.Option(..., help="Backtest start (YYYY-MM-DD)"),
             end_date: str = typer.Option(..., help="Backtest end (YYYY-MM-DD)")):
    """Run historical backtest."""
    typer.echo(f"Backtesting from {start_date} to {end_date}...")
    typer.secho("❌ Not yet implemented", fg=typer.colors.YELLOW)


if __name__ == "__main__":
    app()
