"""Backtesting: compare historical theme allocations vs benchmark."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import yfinance as yf

log = logging.getLogger(__name__)

ALLOC_DIR = Path("data/allocations")
THEMES_DIR = Path("data/themes")


def list_snapshots() -> list[str]:
    """List available allocation dates (YYYYMMDD)."""
    if not ALLOC_DIR.exists():
        return []
    return sorted(
        f.stem.replace("_allocation", "")
        for f in ALLOC_DIR.glob("*_allocation.json")
    )


def load_allocation(date_str: str) -> Optional[dict]:
    """Load a saved allocation by date string (YYYYMMDD)."""
    path = ALLOC_DIR / f"{date_str}_allocation.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def compute_returns(
    alloc: dict,
    from_date: str,
    to_date: str | None = None,
    benchmark: str = "SPY",
) -> dict:
    """Compute portfolio and benchmark returns between two dates.

    Args:
        alloc: Saved allocation dict with entries and entry_prices.
        from_date: Start date YYYYMMDD.
        to_date: End date YYYYMMDD (default: today).
        benchmark: Benchmark ticker (default: SPY).

    Returns:
        Dict with per-ticker returns, portfolio return, benchmark return, alpha.
    """
    start = datetime.strptime(from_date, "%Y%m%d")
    end = datetime.strptime(to_date, "%Y%m%d") if to_date else datetime.utcnow()

    # Per-ticker returns
    ticker_returns: list[dict] = []
    total_thematic_pct = 0.0

    for entry in alloc.get("entries", []):
        entry_prices = entry.get("entry_prices", {})
        theme = entry.get("theme", "?")
        pct = entry.get("pct_allocation", 0)
        tickers_in_vehicle = [t.strip() for t in entry.get("vehicle", "").split(",")]
        n_tickers = max(len(tickers_in_vehicle), 1)

        for ticker in tickers_in_vehicle:
            ticker = ticker.strip()
            if not ticker:
                continue

            ep = entry_prices.get(ticker)
            cp = _get_current_price(ticker)

            ret_pct = None
            if ep and ep > 0 and cp and cp > 0:
                ret_pct = round((cp - ep) / ep * 100, 2)

            ticker_weight = pct / n_tickers  # equal split within vehicle
            total_thematic_pct += ticker_weight

            ticker_returns.append({
                "ticker": ticker,
                "theme": theme,
                "entry_price": ep,
                "current_price": cp,
                "return_pct": ret_pct,
                "weight_pct": round(ticker_weight, 2),
            })

    # Portfolio weighted return (thematic portion only)
    weighted_return = 0.0
    valid_weight = 0.0
    for tr in ticker_returns:
        if tr["return_pct"] is not None:
            weighted_return += tr["return_pct"] * tr["weight_pct"]
            valid_weight += tr["weight_pct"]

    portfolio_return = round(weighted_return / max(valid_weight, 1), 2) if valid_weight > 0 else 0.0

    # Core allocation return (assume SPY/VT proxy)
    core_pct = alloc.get("core_pct", 60)
    core_return = _get_period_return(benchmark, start, end)

    # Blended return (thematic portion + core portion)
    thematic_share = total_thematic_pct / 100.0
    core_share = core_pct / 100.0
    blended_return = round(
        portfolio_return * thematic_share + (core_return or 0) * core_share,
        2,
    )

    # Benchmark return
    benchmark_return = _get_period_return(benchmark, start, end)

    # Alpha
    alpha = round(blended_return - (benchmark_return or 0), 2) if benchmark_return is not None else None

    # Max drawdown (simplified — per-ticker max loss)
    max_drawdown = 0.0
    for tr in ticker_returns:
        if tr["return_pct"] is not None and tr["return_pct"] < max_drawdown:
            max_drawdown = tr["return_pct"]

    return {
        "from_date": from_date,
        "to_date": to_date or datetime.utcnow().strftime("%Y%m%d"),
        "ticker_returns": ticker_returns,
        "thematic_return": portfolio_return,
        "core_return": core_return,
        "blended_return": blended_return,
        "benchmark_ticker": benchmark,
        "benchmark_return": benchmark_return,
        "alpha": alpha,
        "max_drawdown": round(max_drawdown, 2),
    }


def _get_current_price(ticker: str) -> Optional[float]:
    """Get current price for a ticker."""
    try:
        info = yf.Ticker(ticker).info or {}
        return info.get("regularMarketPrice") or info.get("currentPrice")
    except Exception:
        return None


def _get_period_return(ticker: str, start: datetime, end: datetime) -> Optional[float]:
    """Get return % for a ticker between two dates."""
    try:
        hist = yf.Ticker(ticker).history(
            start=start.strftime("%Y-%m-%d"),
            end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
        )
        if hist is None or hist.empty or len(hist) < 2:
            return None
        start_price = hist["Close"].iloc[0]
        end_price = hist["Close"].iloc[-1]
        if start_price and start_price > 0:
            return round((end_price - start_price) / start_price * 100, 2)
    except Exception:
        return None
    return None
