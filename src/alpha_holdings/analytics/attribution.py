"""Factor attribution via returns-based style analysis.

Decomposes portfolio returns into contributions from momentum, low-volatility,
and liquidity factors using long-short factor portfolios constructed from the
same universe. Uses OLS regression of portfolio excess returns on daily factor
returns to estimate exposures and contributions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from alpha_holdings.data.storage import StorageBackend


@dataclass(slots=True)
class FactorExposure:
    """Exposure and contribution for a single factor."""

    name: str
    beta: float
    mean_factor_return_ann: float
    contribution_ann: float
    t_stat: float


@dataclass(slots=True)
class AttributionResult:
    """Full factor attribution report."""

    start_date: str
    end_date: str
    alpha_ann: float
    r_squared: float
    residual_vol_ann: float
    factors: list[FactorExposure] = field(default_factory=list)
    factor_returns: pd.DataFrame | None = None  # daily factor return series


def compute_factor_attribution(
    *,
    storage: StorageBackend,
    start_date: str,
    end_date: str,
    seed_universe_path: Path | None = None,
    rebalance_freq: str = "monthly",
    lookback_days: int = 20,
    risk_free_rate: float = 0.04,
) -> AttributionResult:
    """Compute factor attribution for the latest backtest.

    Steps:
      1. Load portfolio daily returns from backtest_results.
      2. Load universe price/volume data and build factor return series.
      3. Regress portfolio excess returns on factor returns via OLS.
      4. Compute factor contributions and alpha.

    Args:
        storage: Backend for reading backtest results and price data.
        start_date: Attribution period start (YYYY-MM-DD).
        end_date: Attribution period end (YYYY-MM-DD).
        seed_universe_path: Seed universe CSV for symbol list.
        rebalance_freq: Frequency for factor portfolio rebalancing.
        lookback_days: Trailing window for computing factor scores.
        risk_free_rate: Annual risk-free rate for excess returns.

    Returns:
        AttributionResult with per-factor exposures and contributions.
    """
    # 1. Load portfolio daily returns from the latest backtest
    nav_df = _read_latest_backtest(storage=storage)
    if nav_df is None:
        raise ValueError("No backtest_results snapshot found. Run 'alpha backtest' first.")

    nav_df["date"] = pd.to_datetime(nav_df["date"])
    nav_df = nav_df.sort_values("date").reset_index(drop=True)

    dt_start = pd.Timestamp(start_date)
    dt_end = pd.Timestamp(end_date)
    nav_df = nav_df[(nav_df["date"] >= dt_start) & (nav_df["date"] <= dt_end)]

    if len(nav_df) < 10:
        raise ValueError("Too few data points for factor attribution (need >= 10 days).")

    if "daily_return" not in nav_df.columns:
        nav_df["daily_return"] = nav_df["nav"].pct_change().fillna(0.0)
    port_returns = nav_df.set_index("date")["daily_return"].iloc[1:]

    # 2. Build factor return series from universe price data
    symbols = _load_universe_symbols(seed_universe_path)
    price_matrix, volume_matrix = _build_price_matrices(storage=storage, symbols=symbols)

    if price_matrix.empty:
        raise ValueError("No price data available for factor attribution.")

    price_matrix = price_matrix.loc[
        (price_matrix.index >= dt_start) & (price_matrix.index <= dt_end)
    ]
    volume_matrix = volume_matrix.loc[price_matrix.index]

    factor_returns = _build_factor_return_series(
        price_matrix=price_matrix,
        volume_matrix=volume_matrix,
        rebalance_freq=rebalance_freq,
        lookback_days=lookback_days,
    )

    if factor_returns.empty:
        raise ValueError("Could not construct factor return series.")

    # 3. Align portfolio and factor returns
    common_dates = port_returns.index.intersection(factor_returns.index)
    if len(common_dates) < 10:
        raise ValueError(
            f"Only {len(common_dates)} overlapping dates between portfolio and factor data."
        )

    y = port_returns.loc[common_dates].values
    daily_rf = risk_free_rate / 252.0
    y_excess = y - daily_rf

    factor_names = factor_returns.columns.tolist()
    x_factors = factor_returns.loc[common_dates].values

    # 4. OLS regression: y_excess = alpha + beta_1*F1 + beta_2*F2 + ... + epsilon
    x_design = np.column_stack([np.ones(len(x_factors)), x_factors])
    # Use least-squares: (X'X)^-1 X'y
    try:
        betas, _residuals, _, _ = np.linalg.lstsq(x_design, y_excess, rcond=None)
    except np.linalg.LinAlgError:
        # Fallback if data is degenerate
        return AttributionResult(
            start_date=start_date,
            end_date=end_date,
            alpha_ann=0.0,
            r_squared=0.0,
            residual_vol_ann=0.0,
        )

    alpha_daily = betas[0]
    factor_betas = betas[1:]

    # Predicted values and R²
    y_pred = x_design @ betas
    ss_res = float(np.sum((y_excess - y_pred) ** 2))
    ss_tot = float(np.sum((y_excess - np.mean(y_excess)) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    # Residual volatility
    n_obs = len(y_excess)
    n_params = len(betas)
    residual_std = np.sqrt(ss_res / max(n_obs - n_params, 1))
    residual_vol_ann = float(residual_std * np.sqrt(252))

    # Standard errors for t-stats
    try:
        cov_matrix = float(ss_res / max(n_obs - n_params, 1)) * np.linalg.inv(x_design.T @ x_design)
        se = np.sqrt(np.diag(cov_matrix))
    except np.linalg.LinAlgError:
        se = np.ones(n_params) * 1e-6

    # 5. Build factor exposures
    factors = []
    for i, name in enumerate(factor_names):
        beta = float(factor_betas[i])
        mean_fr = float(factor_returns[name].loc[common_dates].mean())
        mean_fr_ann = mean_fr * 252.0
        contribution_ann = beta * mean_fr_ann
        t_stat = float(factor_betas[i] / se[i + 1]) if se[i + 1] > 0 else 0.0
        factors.append(
            FactorExposure(
                name=name,
                beta=round(beta, 4),
                mean_factor_return_ann=round(mean_fr_ann, 6),
                contribution_ann=round(contribution_ann, 6),
                t_stat=round(t_stat, 2),
            )
        )

    alpha_ann = float(alpha_daily * 252.0)

    return AttributionResult(
        start_date=start_date,
        end_date=end_date,
        alpha_ann=round(alpha_ann, 6),
        r_squared=round(max(0.0, r_squared), 4),
        residual_vol_ann=round(residual_vol_ann, 6),
        factors=factors,
        factor_returns=factor_returns.loc[common_dates],
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_factor_return_series(
    *,
    price_matrix: pd.DataFrame,
    volume_matrix: pd.DataFrame,
    rebalance_freq: str,
    lookback_days: int,
) -> pd.DataFrame:
    """Build daily long-short factor return series.

    At each rebalance date, sort the universe by each factor, go long the top
    half and short the bottom half (equal-weight within each half).
    Between rebalances, track the daily return of each long-short portfolio.
    """
    return_matrix = price_matrix.pct_change().iloc[1:]
    if return_matrix.empty:
        return pd.DataFrame()

    rebalance_dates = _generate_rebalance_dates(
        trading_dates=return_matrix.index, freq=rebalance_freq
    )
    if not rebalance_dates:
        rebalance_dates = [return_matrix.index[0]]

    factor_names = ["momentum", "low_volatility", "liquidity"]
    # Pre-compute: at each rebalance, compute factor scores
    rebalance_assignments: list[tuple[pd.Timestamp, dict[str, dict[str, float]]]] = []

    for reb_date in rebalance_dates:
        idx = return_matrix.index.get_loc(reb_date)
        start_idx = max(0, idx - lookback_days)
        window_prices = price_matrix.iloc[start_idx : idx + 1]

        if len(window_prices) < 2:
            continue

        scores_by_factor: dict[str, dict[str, float]] = {f: {} for f in factor_names}
        for sym in price_matrix.columns:
            col = window_prices[sym].dropna()
            if len(col) < 2:
                continue
            close = col.values
            returns = pd.Series(close).pct_change().dropna()
            if returns.empty:
                continue

            scores_by_factor["momentum"][sym] = float(close[-1] / close[0] - 1.0)
            scores_by_factor["low_volatility"][sym] = -float(returns.std(ddof=0))

            vol_col = volume_matrix[sym] if sym in volume_matrix.columns else None
            if vol_col is not None:
                vol_window = vol_col.iloc[start_idx : idx + 1]
                avg_dv = float((col * vol_window.reindex(col.index).fillna(0)).mean())
            else:
                avg_dv = 0.0
            scores_by_factor["liquidity"][sym] = avg_dv

        rebalance_assignments.append((reb_date, scores_by_factor))

    if not rebalance_assignments:
        return pd.DataFrame()

    # Build daily factor returns
    factor_return_rows: list[dict] = []
    for period_idx, (reb_date, scores_by_factor) in enumerate(rebalance_assignments):
        # Determine period end
        if period_idx + 1 < len(rebalance_assignments):
            next_reb = rebalance_assignments[period_idx + 1][0]
        else:
            next_reb = return_matrix.index[-1] + pd.Timedelta(days=1)

        period_mask = (return_matrix.index > reb_date) & (return_matrix.index <= next_reb)
        # Include rebalance date for the first period
        if period_idx == 0:
            period_mask = (return_matrix.index >= return_matrix.index[0]) & (
                return_matrix.index <= next_reb
            )
            if reb_date in return_matrix.index:
                period_mask = period_mask | (return_matrix.index == reb_date)

        period_returns = return_matrix.loc[period_mask]

        for trade_date in period_returns.index:
            row: dict[str, object] = {"date": trade_date}
            daily_rets = return_matrix.loc[trade_date]

            for factor_name in factor_names:
                scores = scores_by_factor.get(factor_name, {})
                if len(scores) < 2:
                    row[factor_name] = 0.0
                    continue

                sorted_syms = sorted(scores, key=lambda s: scores[s], reverse=True)
                mid = len(sorted_syms) // 2
                long_syms = sorted_syms[:mid] if mid > 0 else sorted_syms[:1]
                short_syms = sorted_syms[mid:] if mid > 0 else sorted_syms[1:]

                long_ret = np.mean([daily_rets.get(s, 0.0) for s in long_syms])
                short_ret = np.mean([daily_rets.get(s, 0.0) for s in short_syms])
                row[factor_name] = float(long_ret - short_ret)

            factor_return_rows.append(row)

    if not factor_return_rows:
        return pd.DataFrame()

    result = pd.DataFrame(factor_return_rows)
    result["date"] = pd.to_datetime(result["date"])
    result = result.drop_duplicates(subset="date", keep="last")
    result = result.set_index("date").sort_index()
    return result[factor_names]


def _read_latest_backtest(*, storage: StorageBackend) -> pd.DataFrame | None:
    """Read the latest backtest_results snapshot."""
    snapshots = storage.list_snapshots(dataset_filter="backtest_results")
    if not snapshots:
        return None
    latest = max(snapshots, key=lambda s: str(s["as_of"]))
    as_of_prefix = str(latest["as_of"])[:10]
    try:
        return storage.read_snapshot(dataset="backtest_results", as_of=as_of_prefix)
    except FileNotFoundError:
        return None


def _load_universe_symbols(seed_path: Path | None) -> list[str]:
    """Load symbol list from seed universe CSV."""
    if seed_path is None:
        from alpha_holdings.universe.builder import DEFAULT_SEED_UNIVERSE_PATH

        seed_path = DEFAULT_SEED_UNIVERSE_PATH
    try:
        df = pd.read_csv(seed_path)
        return df["symbol"].tolist()
    except (FileNotFoundError, KeyError):
        return []


def _build_price_matrices(
    *,
    storage: StorageBackend,
    symbols: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build aligned price and volume matrices from stored snapshots."""
    prices_dict: dict[str, pd.Series] = {}
    volumes_dict: dict[str, pd.Series] = {}

    for symbol in symbols:
        dataset = f"{symbol.lower()}_prices"
        df = _read_latest_dataset(storage=storage, dataset=dataset)
        if df is None or df.empty or "date" not in df.columns:
            continue

        df = df.copy()
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").set_index("date")
        df = df[~df.index.duplicated(keep="last")]

        close_col = "adjusted_close" if "adjusted_close" in df.columns else "close"
        close = pd.to_numeric(df[close_col], errors="coerce")
        close = close.fillna(pd.to_numeric(df["close"], errors="coerce"))
        prices_dict[symbol] = close

        if "volume" in df.columns:
            volumes_dict[symbol] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)

    if not prices_dict:
        return pd.DataFrame(), pd.DataFrame()

    price_matrix = pd.DataFrame(prices_dict).sort_index().ffill()
    volume_matrix = pd.DataFrame(volumes_dict).reindex(price_matrix.index).fillna(0)
    price_matrix = price_matrix.dropna(how="all")
    volume_matrix = volume_matrix.loc[price_matrix.index]
    return price_matrix, volume_matrix


def _read_latest_dataset(*, storage: StorageBackend, dataset: str) -> pd.DataFrame | None:
    """Read the latest snapshot for a dataset."""
    snapshots = storage.list_snapshots(dataset_filter=dataset)
    if not snapshots:
        return None
    latest = max(snapshots, key=lambda s: str(s["as_of"]))
    try:
        df = pd.read_parquet(latest["snapshot_path"])
        return df if not df.empty else None
    except (FileNotFoundError, KeyError):
        return None


def _generate_rebalance_dates(
    *,
    trading_dates: pd.DatetimeIndex,
    freq: str,
) -> list[pd.Timestamp]:
    """Generate rebalance dates at period ends within the trading calendar."""
    if freq == "weekly":
        grouped = pd.Series(trading_dates, index=trading_dates).groupby(
            trading_dates.to_period("W")
        )
    elif freq == "quarterly":
        grouped = pd.Series(trading_dates, index=trading_dates).groupby(
            trading_dates.to_period("Q")
        )
    else:
        grouped = pd.Series(trading_dates, index=trading_dates).groupby(
            trading_dates.to_period("M")
        )
    return [group.index[-1] for _, group in grouped if len(group) > 0]
