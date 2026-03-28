"""Portfolio performance analytics and reporting.

Computes returns, volatility, Sharpe ratio, max drawdown, and benchmark-relative
metrics from a NAV series or stored backtest results. Produces a PerformanceReport
that can be displayed or persisted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from alpha_holdings.data.storage import StorageBackend


@dataclass(slots=True)
class PerformanceReport:
    """Summary performance report for a portfolio."""

    portfolio_id: str
    start_date: str
    end_date: str
    total_return: float
    annualized_return: float
    volatility: float
    sharpe_ratio: float
    max_drawdown: float
    calmar_ratio: float | None
    benchmark_return: float | None
    excess_return: float | None
    tracking_error: float | None
    information_ratio: float | None
    best_day: float
    worst_day: float
    positive_days_pct: float
    snapshot_path: Path
    summary: pd.DataFrame
    degraded_assumptions: list[str] = field(default_factory=list)
    holdings_count: int | None = None
    realized_gain_total: float | None = None
    unrealized_gain_total: float | None = None
    realized_gain_since_prior: float | None = None


def generate_report(
    *,
    storage: StorageBackend,
    portfolio_id: str = "backtest",
    risk_free_rate: float = 0.04,
    benchmark_symbol: str = "SPY",
) -> PerformanceReport:
    """Generate a performance report from the latest backtest or portfolio NAV data.

    Reads the latest backtest_results snapshot and computes detailed analytics.

    Args:
        storage: Backend for reading backtest results and benchmark data.
        portfolio_id: Portfolio to report on.
        risk_free_rate: Annual risk-free rate for Sharpe/information ratio.
        benchmark_symbol: Benchmark for relative metrics.

    Returns:
        PerformanceReport with all computed metrics.
    """
    # Read the latest backtest_results snapshot
    nav_df, backtest_metadata = _read_latest_backtest_with_metadata(storage=storage)
    if nav_df is None:
        raise ValueError("No backtest_results snapshot found. Run 'alpha backtest' first.")

    raw_warnings = backtest_metadata.get("warnings", [])
    degraded_assumptions = [str(w) for w in raw_warnings] if isinstance(raw_warnings, list) else []
    holdings_summary = _read_holdings_rollforward_summary(
        storage=storage,
        portfolio_id=portfolio_id,
    )

    return compute_report_from_nav(
        nav_series=nav_df,
        storage=storage,
        portfolio_id=portfolio_id,
        risk_free_rate=risk_free_rate,
        benchmark_symbol=benchmark_symbol,
        degraded_assumptions=degraded_assumptions,
        holdings_summary=holdings_summary,
    )


def compute_report_from_nav(
    *,
    nav_series: pd.DataFrame,
    storage: StorageBackend,
    portfolio_id: str = "default",
    risk_free_rate: float = 0.04,
    benchmark_symbol: str = "SPY",
    degraded_assumptions: list[str] | None = None,
    holdings_summary: dict[str, float | int | str | None] | None = None,
) -> PerformanceReport:
    """Compute performance report from a NAV DataFrame.

    Expected columns: date, nav. Optional: daily_return, benchmark_return.

    Args:
        nav_series: DataFrame with at least 'date' and 'nav' columns.
        storage: Backend for persisting the report snapshot.
        portfolio_id: Portfolio identifier.
        risk_free_rate: Annual risk-free rate.
        benchmark_symbol: Benchmark identifier for labeling.

    Returns:
        PerformanceReport with computed metrics.
    """
    df = nav_series.copy()
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)

    if len(df) < 2:
        raise ValueError("NAV series must have at least 2 data points.")

    # Compute daily returns from NAV if not present
    if "daily_return" not in df.columns:
        df["daily_return"] = df["nav"].pct_change().fillna(0.0)

    daily_returns = df["daily_return"].iloc[1:]  # skip first (0 return)
    nav_values = df["nav"].values

    # Core metrics
    total_return = float(nav_values[-1] / nav_values[0] - 1.0)
    n_days = len(daily_returns)
    ann_factor = 252.0 / n_days if n_days > 0 else 1.0
    annualized_return = float((1.0 + total_return) ** ann_factor - 1.0)
    volatility = float(daily_returns.std() * np.sqrt(252)) if n_days > 0 else 0.0
    max_dd = _compute_max_drawdown(nav_values)

    # Sharpe ratio
    daily_rf = risk_free_rate / 252.0
    excess_daily = daily_returns - daily_rf
    sharpe = (
        float(excess_daily.mean() / excess_daily.std() * np.sqrt(252))
        if excess_daily.std() > 0
        else 0.0
    )

    # Calmar ratio
    calmar = abs(annualized_return / max_dd) if max_dd > 0 else None

    # Best/worst days
    best_day = float(daily_returns.max()) if n_days > 0 else 0.0
    worst_day = float(daily_returns.min()) if n_days > 0 else 0.0
    positive_pct = float((daily_returns > 0).sum() / n_days * 100) if n_days > 0 else 0.0

    # Benchmark-relative metrics
    benchmark_return = None
    excess_return = None
    tracking_error = None
    information_ratio = None

    if "benchmark_return" in df.columns and df["benchmark_return"].abs().sum() > 0:
        bm_returns = df["benchmark_return"].iloc[1:]
        bm_cum = float((1.0 + bm_returns).prod() - 1.0)
        benchmark_return = bm_cum
        excess_return = total_return - bm_cum

        active_returns = daily_returns.values - bm_returns.values
        tracking_error = float(np.std(active_returns, ddof=1) * np.sqrt(252))
        information_ratio = (
            float(np.mean(active_returns) / np.std(active_returns, ddof=1) * np.sqrt(252))
            if np.std(active_returns, ddof=1) > 0
            else 0.0
        )

    # Build summary DataFrame
    start_date = str(df["date"].iloc[0].date()) if "date" in df.columns else "unknown"
    end_date = str(df["date"].iloc[-1].date()) if "date" in df.columns else "unknown"

    summary_rows = [
        {"metric": "Total Return", "value": f"{total_return:.4%}"},
        {"metric": "Annualized Return", "value": f"{annualized_return:.4%}"},
        {"metric": "Volatility (ann.)", "value": f"{volatility:.4%}"},
        {"metric": "Sharpe Ratio", "value": f"{sharpe:.4f}"},
        {"metric": "Max Drawdown", "value": f"{max_dd:.4%}"},
        {"metric": "Calmar Ratio", "value": f"{calmar:.4f}" if calmar is not None else "N/A"},
        {"metric": "Best Day", "value": f"{best_day:.4%}"},
        {"metric": "Worst Day", "value": f"{worst_day:.4%}"},
        {"metric": "Positive Days", "value": f"{positive_pct:.1f}%"},
        {
            "metric": f"Benchmark Return ({benchmark_symbol})",
            "value": f"{benchmark_return:.4%}" if benchmark_return is not None else "N/A",
        },
        {
            "metric": "Excess Return",
            "value": f"{excess_return:.4%}" if excess_return is not None else "N/A",
        },
        {
            "metric": "Tracking Error",
            "value": f"{tracking_error:.4%}" if tracking_error is not None else "N/A",
        },
        {
            "metric": "Information Ratio",
            "value": f"{information_ratio:.4f}" if information_ratio is not None else "N/A",
        },
        {
            "metric": "Data Quality Assumptions",
            "value": " | ".join(degraded_assumptions) if degraded_assumptions else "None",
        },
        {
            "metric": "Holdings Count",
            "value": str(holdings_summary.get("holdings_count"))
            if holdings_summary and holdings_summary.get("holdings_count") is not None
            else "N/A",
        },
        {
            "metric": "Realized Gain Total",
            "value": f"{float(holdings_summary['realized_gain_total']):,.2f}"
            if holdings_summary and holdings_summary.get("realized_gain_total") is not None
            else "N/A",
        },
        {
            "metric": "Unrealized Gain Total",
            "value": f"{float(holdings_summary['unrealized_gain_total']):,.2f}"
            if holdings_summary and holdings_summary.get("unrealized_gain_total") is not None
            else "N/A",
        },
        {
            "metric": "Realized Gain Since Prior Snapshot",
            "value": f"{float(holdings_summary['realized_gain_since_prior']):,.2f}"
            if holdings_summary and holdings_summary.get("realized_gain_since_prior") is not None
            else "N/A",
        },
    ]
    summary_df = pd.DataFrame(summary_rows)

    # Persist report
    run_as_of = datetime.now(tz=UTC)
    report_rows = summary_df.to_dict(orient="records")
    snapshot_path = storage.write_normalized_snapshot(
        dataset="performance_report",
        as_of=run_as_of,
        rows=report_rows,
    )
    storage.register_snapshot(
        dataset="performance_report",
        as_of=run_as_of,
        snapshot_path=snapshot_path,
        row_count=len(report_rows),
        metadata={
            "portfolio_id": portfolio_id,
            "start_date": start_date,
            "end_date": end_date,
            "total_return": round(total_return, 6),
            "sharpe_ratio": round(sharpe, 4),
            "max_drawdown": round(max_dd, 6),
            "degraded_assumptions": degraded_assumptions or [],
            "holdings_summary": holdings_summary or {},
        },
    )

    return PerformanceReport(
        portfolio_id=portfolio_id,
        start_date=start_date,
        end_date=end_date,
        total_return=round(total_return, 6),
        annualized_return=round(annualized_return, 6),
        volatility=round(volatility, 6),
        sharpe_ratio=round(sharpe, 4),
        max_drawdown=round(max_dd, 6),
        calmar_ratio=round(calmar, 4) if calmar is not None else None,
        benchmark_return=round(benchmark_return, 6) if benchmark_return is not None else None,
        excess_return=round(excess_return, 6) if excess_return is not None else None,
        tracking_error=round(tracking_error, 6) if tracking_error is not None else None,
        information_ratio=round(information_ratio, 4) if information_ratio is not None else None,
        best_day=round(best_day, 6),
        worst_day=round(worst_day, 6),
        positive_days_pct=round(positive_pct, 1),
        snapshot_path=snapshot_path,
        summary=summary_df,
        degraded_assumptions=list(degraded_assumptions or []),
        holdings_count=(
            int(holdings_summary["holdings_count"])
            if holdings_summary and holdings_summary.get("holdings_count") is not None
            else None
        ),
        realized_gain_total=(
            float(holdings_summary["realized_gain_total"])
            if holdings_summary and holdings_summary.get("realized_gain_total") is not None
            else None
        ),
        unrealized_gain_total=(
            float(holdings_summary["unrealized_gain_total"])
            if holdings_summary and holdings_summary.get("unrealized_gain_total") is not None
            else None
        ),
        realized_gain_since_prior=(
            float(holdings_summary["realized_gain_since_prior"])
            if holdings_summary and holdings_summary.get("realized_gain_since_prior") is not None
            else None
        ),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_latest_backtest(*, storage: StorageBackend) -> pd.DataFrame | None:
    """Read the latest backtest_results snapshot."""
    nav_df, _ = _read_latest_backtest_with_metadata(storage=storage)
    return nav_df


def _read_latest_backtest_with_metadata(
    *,
    storage: StorageBackend,
) -> tuple[pd.DataFrame | None, dict[str, object]]:
    """Read latest backtest NAV rows plus snapshot metadata."""
    snapshots = storage.list_snapshots(dataset_filter="backtest_results")
    if not snapshots:
        return None, {}
    latest = max(snapshots, key=lambda s: str(s["as_of"]))
    as_of_prefix = str(latest["as_of"])[:10]
    try:
        return storage.read_snapshot(dataset="backtest_results", as_of=as_of_prefix), dict(
            latest.get("metadata", {})
        )
    except FileNotFoundError:
        return None, dict(latest.get("metadata", {}))


def _read_holdings_rollforward_summary(
    *,
    storage: StorageBackend,
    portfolio_id: str,
) -> dict[str, float | int | str | None] | None:
    """Read latest holdings snapshot and derive continuity rollforward metrics."""
    dataset = f"holdings_snapshot_{portfolio_id}"
    snapshots = storage.list_snapshots(dataset_filter=dataset)
    if not snapshots:
        return None

    ordered = sorted(snapshots, key=lambda s: str(s["as_of"]), reverse=True)
    latest = ordered[0]
    try:
        latest_df = pd.read_parquet(latest["snapshot_path"])
    except (FileNotFoundError, KeyError):
        return None

    if latest_df.empty:
        return {
            "holdings_count": 0,
            "realized_gain_total": 0.0,
            "unrealized_gain_total": 0.0,
            "realized_gain_since_prior": None,
        }

    realized_latest = float(
        pd.to_numeric(latest_df.get("realized_gain_total"), errors="coerce").fillna(0).sum()
    )
    unrealized_latest = float(
        pd.to_numeric(latest_df.get("unrealized_gain"), errors="coerce").fillna(0).sum()
    )

    realized_since_prior: float | None = None
    if len(ordered) > 1:
        prior = ordered[1]
        try:
            prior_df = pd.read_parquet(prior["snapshot_path"])
        except (FileNotFoundError, KeyError):
            prior_df = None
        if prior_df is not None and not prior_df.empty:
            realized_prior = float(
                pd.to_numeric(prior_df.get("realized_gain_total"), errors="coerce").fillna(0).sum()
            )
            realized_since_prior = realized_latest - realized_prior

    return {
        "holdings_count": len(latest_df),
        "realized_gain_total": realized_latest,
        "unrealized_gain_total": unrealized_latest,
        "realized_gain_since_prior": realized_since_prior,
    }


def _compute_max_drawdown(nav_array: np.ndarray) -> float:
    """Maximum peak-to-trough drawdown."""
    if len(nav_array) < 2:
        return 0.0
    peak = nav_array[0]
    max_dd = 0.0
    for val in nav_array[1:]:
        if val > peak:
            peak = val
        dd = (peak - val) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 6)
