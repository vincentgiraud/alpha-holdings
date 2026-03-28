"""Walk-forward historical backtest runner.

Loads stored price histories, applies scoring and construction logic at each
rebalance date, and tracks daily portfolio NAV and returns. Produces a
BacktestResult with the full NAV series, performance metrics, and weight history.

Warning: When using free-source data, backtest results carry weaker assumptions
than institutional-grade point-in-time databases. Results should be treated as
indicative, not as auditable performance claims.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from alpha_holdings.data.storage import StorageBackend
from alpha_holdings.domain.investor_profile import PortfolioConstraints


@dataclass(slots=True)
class BacktestResult:
    """Outcome of one backtest run."""

    start_date: str
    end_date: str
    portfolio_id: str
    rebalance_count: int
    total_return: float
    annualized_return: float
    volatility: float
    sharpe_ratio: float
    max_drawdown: float
    benchmark_total_return: float | None
    snapshot_path: Path
    nav_series: pd.DataFrame
    warnings: list[str]
    weight_history: pd.DataFrame | None = None


def run_backtest(
    *,
    storage: StorageBackend,
    start_date: str,
    end_date: str,
    rebalance_freq: str = "monthly",
    portfolio_id: str = "backtest",
    constraints: PortfolioConstraints | None = None,
    seed_universe_path: Path | None = None,
    initial_value: float = 1_000_000.0,
    benchmark_symbol: str = "SPY",
    risk_free_rate: float = 0.04,
    lookback_days: int = 20,
) -> BacktestResult:
    """Run a walk-forward backtest over stored price data.

    At each rebalance date: score from trailing price data, construct weights,
    then hold until next rebalance while tracking daily returns.

    Args:
        storage: Backend for reading price snapshots.
        start_date: Backtest start (YYYY-MM-DD).
        end_date: Backtest end (YYYY-MM-DD).
        rebalance_freq: Rebalance cadence (monthly, quarterly, weekly).
        portfolio_id: Identifier for this backtest run.
        constraints: Portfolio constraints; defaults to moderate profile.
        seed_universe_path: Path to seed universe CSV for symbol list.
        initial_value: Starting portfolio value.
        benchmark_symbol: Symbol for benchmark comparison.
        risk_free_rate: Annual risk-free rate for Sharpe calculation.
        lookback_days: Trailing window for factor scoring.

    Returns:
        BacktestResult with NAV series and performance summary.
    """
    warnings: list[str] = []

    # 1. Load seed universe symbols
    symbols = _load_universe_symbols(seed_universe_path)
    if not symbols:
        raise ValueError("No symbols found in seed universe.")

    # 2. Load all price histories and build price matrix
    price_matrix, volume_matrix = _build_price_matrices(storage=storage, symbols=symbols)
    if price_matrix.empty:
        raise ValueError("No price data available for any symbol in the universe.")

    # Filter to backtest date range
    dt_start = pd.Timestamp(start_date)
    dt_end = pd.Timestamp(end_date)
    price_matrix = price_matrix.loc[
        (price_matrix.index >= dt_start) & (price_matrix.index <= dt_end)
    ]
    volume_matrix = volume_matrix.loc[price_matrix.index]

    if len(price_matrix) < 2:
        raise ValueError(
            f"Insufficient price data in range {start_date} to {end_date}. "
            f"Need at least 2 trading days."
        )

    # Calculate daily returns
    return_matrix = price_matrix.pct_change()

    # Load benchmark
    benchmark_returns = _load_benchmark_returns(
        storage=storage, symbol=benchmark_symbol, as_of=end_date
    )
    if benchmark_returns is not None:
        benchmark_returns = benchmark_returns.loc[
            (benchmark_returns.index >= dt_start) & (benchmark_returns.index <= dt_end)
        ]
    else:
        warnings.append(f"Benchmark '{benchmark_symbol}' price data not available.")

    # 3. Generate rebalance dates
    rebalance_dates = _generate_rebalance_dates(
        trading_dates=price_matrix.index, freq=rebalance_freq
    )

    # Ensure lookback buffer: first rebalance needs lookback_days of trailing data
    min_rebalance_date = (
        price_matrix.index[lookback_days]
        if len(price_matrix) > lookback_days
        else price_matrix.index[0]
    )
    rebalance_dates = [d for d in rebalance_dates if d >= min_rebalance_date]
    if not rebalance_dates:
        # Fall back to a single rebalance at the earliest feasible date
        rebalance_dates = [min_rebalance_date]
        warnings.append(
            "Insufficient data for requested rebalance frequency; using single rebalance."
        )

    if constraints is None:
        constraints = _default_constraints()

    warnings.append(
        "Free-source data: backtest uses unadjusted or partially adjusted prices. "
        "Results are indicative, not auditable performance."
    )

    # Load fundamentals snapshot history for all symbols.
    fundamentals_history = _load_fundamentals_snapshot_history(
        storage=storage,
        symbols=symbols,
    )
    missing_fundamentals = len(set(symbols) - set(fundamentals_history))
    if missing_fundamentals > 0:
        warnings.append(
            "Degraded execution: fundamentals snapshots unavailable for "
            f"{missing_fundamentals} symbol(s); price-only factors were used for those names."
        )
    symbols_missing_point_in_time: set[str] = set()

    # 4. Walk-forward simulation
    nav = initial_value
    nav_rows: list[dict] = []
    weights: dict[str, float] = {}
    rebalance_count = 0
    weight_snapshots: list[dict] = []

    trading_dates = price_matrix.index.tolist()

    for i, trade_date in enumerate(trading_dates):
        # Check if we should rebalance
        if trade_date in rebalance_dates or (i == 0 and not weights):
            # Score from trailing price data
            trailing_start = max(0, i - lookback_days)
            trailing_prices = price_matrix.iloc[trailing_start : i + 1]
            trailing_volumes = volume_matrix.iloc[trailing_start : i + 1]
            fundamentals_for_trade_date = _select_fundamentals_as_of(
                history=fundamentals_history,
                trade_date=trade_date,
            )
            symbols_missing_point_in_time.update(set(symbols) - set(fundamentals_for_trade_date))

            if len(trailing_prices) >= 2:
                new_weights = _score_and_construct(
                    prices=trailing_prices,
                    volumes=trailing_volumes,
                    constraints=constraints,
                    max_weight=float(constraints.max_single_name_weight),
                    min_holdings=constraints.min_holdings_count,
                    fundamentals=fundamentals_for_trade_date,
                )
                if new_weights:
                    weights = new_weights
                    rebalance_count += 1
                    # Record weight snapshot for visualization
                    snap = {"date": trade_date, **weights}
                    weight_snapshots.append(snap)

        # Compute daily return
        if i > 0 and weights:
            daily_returns = return_matrix.iloc[i]
            portfolio_return = sum(
                weights.get(sym, 0.0) * daily_returns.get(sym, 0.0) for sym in weights
            )
            nav *= 1.0 + portfolio_return

            # Drift weights (weights change as prices move)
            weights = _drift_weights(weights, daily_returns)

        # Benchmark NAV
        bm_return = 0.0
        if benchmark_returns is not None and i > 0 and trade_date in benchmark_returns.index:
            bm_return = float(benchmark_returns.loc[trade_date])

        nav_rows.append(
            {
                "date": trade_date,
                "nav": round(nav, 2),
                "daily_return": round(float(return_matrix.iloc[i].mean()) if i > 0 else 0.0, 6)
                if not weights
                else round(
                    float(
                        sum(
                            weights.get(s, 0.0) * return_matrix.iloc[i].get(s, 0.0) for s in weights
                        )
                    )
                    if i > 0
                    else 0.0,
                    6,
                ),
                "benchmark_return": round(bm_return, 6),
            }
        )

    nav_df = pd.DataFrame(nav_rows)

    # 5. Compute summary metrics
    if len(nav_df) > 1:
        daily_rets = nav_df["daily_return"].iloc[1:]
        total_return = float(nav_df["nav"].iloc[-1] / nav_df["nav"].iloc[0] - 1.0)
        n_days = len(nav_df) - 1
        ann_factor = 252.0 / n_days
        annualized_return = float((1.0 + total_return) ** ann_factor - 1.0)
        volatility = float(daily_rets.std() * np.sqrt(252))
        daily_rf = risk_free_rate / 252.0
        excess_daily = daily_rets - daily_rf
        sharpe = (
            float(excess_daily.mean() / excess_daily.std()) * np.sqrt(252)
            if excess_daily.std() > 0
            else 0.0
        )
        max_dd = _compute_max_drawdown(nav_df["nav"].values)
    else:
        total_return = 0.0
        annualized_return = 0.0
        volatility = 0.0
        sharpe = 0.0
        max_dd = 0.0

    # Benchmark total return
    bm_total_return = None
    if benchmark_returns is not None and len(benchmark_returns) > 1:
        bm_cum = (1.0 + benchmark_returns).cumprod()
        bm_total_return = float(bm_cum.iloc[-1] - 1.0) if len(bm_cum) > 0 else None

    # 6. Persist backtest results
    run_as_of = datetime.now(tz=UTC)
    snapshot_path = storage.write_normalized_snapshot(
        dataset="backtest_results",
        as_of=run_as_of,
        rows=nav_df.to_dict(orient="records"),
    )
    storage.register_snapshot(
        dataset="backtest_results",
        as_of=run_as_of,
        snapshot_path=snapshot_path,
        row_count=len(nav_df),
        metadata={
            "portfolio_id": portfolio_id,
            "start_date": start_date,
            "end_date": end_date,
            "rebalance_freq": rebalance_freq,
            "rebalance_count": rebalance_count,
            "total_return": round(total_return, 6),
            "annualized_return": round(annualized_return, 6),
            "volatility": round(volatility, 6),
            "sharpe_ratio": round(sharpe, 4),
            "max_drawdown": round(max_dd, 6),
            "warnings": warnings,
        },
    )

    # Build weight history DataFrame
    weight_history_df = pd.DataFrame(weight_snapshots) if weight_snapshots else None

    if symbols_missing_point_in_time:
        warnings.append(
            "Degraded execution: fundamentals snapshots were not available on-or-before "
            f"at least one rebalance date for {len(symbols_missing_point_in_time)} symbol(s)."
        )

    return BacktestResult(
        start_date=start_date,
        end_date=end_date,
        portfolio_id=portfolio_id,
        rebalance_count=rebalance_count,
        total_return=round(total_return, 6),
        annualized_return=round(annualized_return, 6),
        volatility=round(volatility, 6),
        sharpe_ratio=round(sharpe, 4),
        max_drawdown=round(max_dd, 6),
        benchmark_total_return=round(bm_total_return, 6) if bm_total_return is not None else None,
        snapshot_path=snapshot_path,
        nav_series=nav_df,
        warnings=warnings,
        weight_history=weight_history_df,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


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
    """Build aligned price and volume matrices from stored snapshots.

    Returns:
        (price_matrix, volume_matrix) — both indexed by date, columns = symbols.
    """
    prices_dict: dict[str, pd.Series] = {}
    volumes_dict: dict[str, pd.Series] = {}

    for symbol in symbols:
        dataset = f"{symbol.lower()}_prices"
        df = _read_latest_dataset(storage=storage, dataset=dataset)
        if df is None:
            continue
        if df.empty or "date" not in df.columns:
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
    # Drop rows where all prices are NaN
    price_matrix = price_matrix.dropna(how="all")
    volume_matrix = volume_matrix.loc[price_matrix.index]

    return price_matrix, volume_matrix


def _load_benchmark_returns(
    *,
    storage: StorageBackend,
    symbol: str,
    as_of: str,  # noqa: ARG001
) -> pd.Series | None:
    """Load benchmark daily returns from stored price snapshot."""
    dataset = f"{symbol.lower()}_prices"
    df = _read_latest_dataset(storage=storage, dataset=dataset)
    if df is None:
        return None
    if df.empty or "date" not in df.columns:
        return None

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").set_index("date")
    df = df[~df.index.duplicated(keep="last")]

    close_col = "adjusted_close" if "adjusted_close" in df.columns else "close"
    close = pd.to_numeric(df[close_col], errors="coerce").ffill()
    return close.pct_change()


def _generate_rebalance_dates(
    *,
    trading_dates: pd.DatetimeIndex,
    freq: str,
) -> list[pd.Timestamp]:
    """Generate rebalance dates at month/quarter/week ends within the trading calendar."""
    if freq == "weekly":
        # Last trading day of each week
        grouped = pd.Series(trading_dates, index=trading_dates).groupby(
            trading_dates.to_period("W")
        )
    elif freq == "quarterly":
        grouped = pd.Series(trading_dates, index=trading_dates).groupby(
            trading_dates.to_period("Q")
        )
    else:  # monthly (default)
        grouped = pd.Series(trading_dates, index=trading_dates).groupby(
            trading_dates.to_period("M")
        )

    return [group.index[-1] for _, group in grouped if len(group) > 0]


def _score_and_construct(
    *,
    prices: pd.DataFrame,
    volumes: pd.DataFrame,
    constraints: PortfolioConstraints,  # noqa: ARG001
    max_weight: float,
    min_holdings: int,
    fundamentals: dict[str, pd.DataFrame] | None = None,
) -> dict[str, float]:
    """In-memory scoring and weight construction for the backtest.

    Uses the same factor model as the main scoring engine but operates on
    in-memory DataFrames rather than storage snapshots. When fundamentals
    data is provided, includes fundamentals factors in the composite score.
    """
    if len(prices) < 2:
        return {}

    scores: list[dict] = []
    for symbol in prices.columns:
        col = prices[symbol].dropna()
        if len(col) < 2:
            continue

        close = col.values
        returns = pd.Series(close).pct_change().dropna()
        if returns.empty:
            continue

        momentum = float(close[-1] / close[0] - 1.0)
        volatility = float(returns.std(ddof=0))

        vol_col = volumes[symbol] if symbol in volumes.columns else pd.Series(dtype=float)
        avg_dollar_vol = float((col * vol_col.reindex(col.index).fillna(0)).mean())

        # Compute fundamentals metrics if available
        fund_data = fundamentals.get(symbol) if fundamentals else None
        fund_metrics = _compute_fundamentals_metrics_backtest(fund_data)

        scores.append(
            {
                "symbol": symbol,
                "momentum": momentum,
                "volatility": volatility,
                "avg_dollar_volume": avg_dollar_vol,
                **fund_metrics,
            }
        )

    if not scores:
        return {}

    df = pd.DataFrame(scores)

    # Z-score factors
    df["f_momentum"] = _zscore(df["momentum"])
    df["f_low_vol"] = _zscore(-df["volatility"])
    df["f_liquidity"] = _zscore(df["avg_dollar_volume"])

    # Include fundamentals factors if any symbol has them
    factor_cols = ["f_momentum", "f_low_vol", "f_liquidity"]
    if "has_fundamentals" in df.columns and df["has_fundamentals"].any():
        df["f_profitability"] = 0.0
        df["f_balance_sheet"] = 0.0
        df["f_cash_flow"] = 0.0

        fundamentals_mask = df["has_fundamentals"].astype(bool)
        if fundamentals_mask.any():
            df.loc[fundamentals_mask, "f_profitability"] = _zscore(
                df.loc[fundamentals_mask, "profitability"]
            )
            df.loc[fundamentals_mask, "f_balance_sheet"] = _zscore(
                df.loc[fundamentals_mask, "balance_sheet_quality"]
            )
            df.loc[fundamentals_mask, "f_cash_flow"] = _zscore(
                df.loc[fundamentals_mask, "cash_flow_quality"]
            )
        factor_cols.extend(["f_profitability", "f_balance_sheet", "f_cash_flow"])

    df["composite"] = df[factor_cols].mean(axis=1)

    # Score-proportional weights
    min_score = df["composite"].min()
    df["shifted"] = df["composite"] - min_score + 1e-6
    total = df["shifted"].sum()
    if total <= 0:
        n = len(df)
        return {row["symbol"]: 1.0 / n for _, row in df.iterrows()}

    weights = {row["symbol"]: float(row["shifted"]) / float(total) for _, row in df.iterrows()}

    # Position cap
    weights = _apply_position_cap(weights, max_weight)

    # Min holdings enforcement
    if sum(1 for w in weights.values() if w > 0) < min_holdings:
        # Already includes all scoreable symbols; no candidates to add
        pass

    # Renormalize
    total_w = sum(weights.values())
    if total_w > 0:
        weights = {s: w / total_w for s, w in weights.items()}

    return weights


def _apply_position_cap(weights: dict[str, float], max_weight: float) -> dict[str, float]:
    """Iteratively cap and redistribute."""
    n = len(weights)
    if n == 0:
        return weights
    if n * max_weight < 1.0 - 1e-10:
        return {s: 1.0 / n for s in weights}

    result = dict(weights)
    for _ in range(50):
        over = {s: w for s, w in result.items() if w > max_weight + 1e-10}
        if not over:
            break
        under = {s: w for s, w in result.items() if s not in over}
        if not under:
            return {s: 1.0 / n for s in result}

        excess = sum(w - max_weight for w in over.values())
        under_total = sum(under.values())
        for s in over:
            result[s] = max_weight
        if under_total > 0:
            for s in under:
                result[s] += excess * (result[s] / under_total)
    return result


def _drift_weights(weights: dict[str, float], daily_returns: pd.Series) -> dict[str, float]:
    """Update weights for one day of price movement (drift)."""
    new_values = {}
    for sym, w in weights.items():
        ret = daily_returns.get(sym, 0.0)
        if pd.isna(ret):
            ret = 0.0
        new_values[sym] = w * (1.0 + float(ret))

    total = sum(new_values.values())
    if total <= 0:
        return weights
    return {s: v / total for s, v in new_values.items()}


def _compute_max_drawdown(nav_array: np.ndarray) -> float:
    """Compute maximum peak-to-trough drawdown from a NAV series."""
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


def _zscore(series: pd.Series) -> pd.Series:
    """Compute z-scores for a series."""
    std = series.std(ddof=0)
    if std == 0 or pd.isna(std):
        return pd.Series(0.0, index=series.index)
    return (series - series.mean()) / std


def _read_latest_dataset(*, storage: StorageBackend, dataset: str) -> pd.DataFrame | None:
    """Read the latest snapshot for a dataset regardless of as_of date."""
    snapshots = storage.list_snapshots(dataset_filter=dataset)
    if not snapshots:
        return None
    latest = max(snapshots, key=lambda s: str(s["as_of"]))
    try:
        df = pd.read_parquet(latest["snapshot_path"])
        return df if not df.empty else None
    except (FileNotFoundError, KeyError):
        return None


def _load_fundamentals_snapshot_history(
    *,
    storage: StorageBackend,
    symbols: list[str],
) -> dict[str, list[tuple[pd.Timestamp, pd.DataFrame]]]:
    """Load all fundamentals snapshots for each symbol.

    Returns a dict mapping symbol to a sorted history list of
    (snapshot_as_of_timestamp, fundamentals_dataframe).
    """
    fundamentals: dict[str, list[tuple[pd.Timestamp, pd.DataFrame]]] = {}
    for symbol in symbols:
        dataset = f"{symbol.lower()}_fundamentals"
        snapshots = storage.list_snapshots(dataset_filter=dataset)
        history: list[tuple[pd.Timestamp, pd.DataFrame]] = []
        for snapshot in snapshots:
            try:
                df = pd.read_parquet(snapshot["snapshot_path"])
            except (FileNotFoundError, KeyError):
                continue
            if df.empty:
                continue
            as_of = pd.Timestamp(snapshot["as_of"])
            history.append((as_of, df))

        if history:
            history.sort(key=lambda item: item[0])
            fundamentals[symbol] = history

    return fundamentals


def _select_fundamentals_as_of(
    *,
    history: dict[str, list[tuple[pd.Timestamp, pd.DataFrame]]],
    trade_date: pd.Timestamp,
) -> dict[str, pd.DataFrame]:
    """Select latest fundamentals snapshot at or before a trade date."""
    selected: dict[str, pd.DataFrame] = {}
    trade_day = pd.Timestamp(trade_date).date()

    for symbol, snapshots in history.items():
        eligible = [
            (as_of, frame) for as_of, frame in snapshots if pd.Timestamp(as_of).date() <= trade_day
        ]
        if not eligible:
            continue
        selected[symbol] = eligible[-1][1]

    return selected


def _compute_fundamentals_metrics_backtest(
    fundamentals: pd.DataFrame | None,
) -> dict[str, object]:
    """Compute fundamentals factor metrics from a DataFrame.

    Returns dict with profitability, balance_sheet_quality, cash_flow_quality,
    and has_fundamentals flag.
    """
    if fundamentals is None or fundamentals.empty:
        return {
            "profitability": 0.0,
            "balance_sheet_quality": 0.0,
            "cash_flow_quality": 0.0,
            "has_fundamentals": False,
        }

    # Get latest period if multiple periods available
    ordered = (
        fundamentals.sort_values("period_end_date", ascending=False)
        if "period_end_date" in fundamentals.columns
        else fundamentals
    )
    latest = ordered.iloc[0] if not ordered.empty else None
    if latest is None:
        return {
            "profitability": 0.0,
            "balance_sheet_quality": 0.0,
            "cash_flow_quality": 0.0,
            "has_fundamentals": False,
        }

    # Extract metrics
    net_income = _coerce_float_backtest(latest.get("net_income"))
    revenue = _coerce_float_backtest(latest.get("revenue"))
    debt_to_equity = _coerce_float_backtest(latest.get("debt_to_equity"))
    current_ratio = _coerce_float_backtest(latest.get("current_ratio"))
    free_cash_flow = _coerce_float_backtest(latest.get("free_cash_flow"))

    # Compute composite metrics
    profitability = 0.0
    if revenue is not None and revenue > 0 and net_income is not None:
        profitability = net_income / revenue
    elif net_income is not None:
        profitability = net_income

    balance_sheet_quality = 0.0
    if debt_to_equity is not None:
        balance_sheet_quality += -debt_to_equity
    if current_ratio is not None:
        balance_sheet_quality += current_ratio

    cash_flow_quality = free_cash_flow if free_cash_flow is not None else 0.0

    return {
        "profitability": float(profitability or 0.0),
        "balance_sheet_quality": float(balance_sheet_quality),
        "cash_flow_quality": float(cash_flow_quality),
        "has_fundamentals": True,
    }


def _coerce_float_backtest(value: object) -> float | None:
    """Safely coerce a value to float, returning None if not possible."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _default_constraints() -> PortfolioConstraints:
    """Default constraints for backtest."""
    from alpha_holdings.domain.investor_profile import (
        FireVariant,
        InvestorProfile,
        ProfileToConstraints,
        WithdrawalPattern,
    )

    profile = InvestorProfile(
        profile_id="backtest_default",
        fire_variant=FireVariant.FAT_FIRE,
        risk_appetite=4,
        horizon_years=20,
        withdrawal_pattern=WithdrawalPattern.COMPOUND_ONLY,
    )
    return ProfileToConstraints.resolve(profile)
