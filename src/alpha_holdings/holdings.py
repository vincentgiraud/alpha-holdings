"""Existing holdings awareness — overlap detection with discovered themes."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Holdings model
# ---------------------------------------------------------------------------

class Holding(BaseModel):
    """A single holding in the user's portfolio."""
    ticker: str
    shares: float = 0
    avg_cost: Optional[float] = None


class HoldingsPortfolio(BaseModel):
    """User's existing portfolio loaded from data/holdings.json."""
    holdings: list[Holding] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Dynamic ETF decomposition + hardcoded fallback
# ---------------------------------------------------------------------------

# Fallback: hardcoded approximate top holdings (used when yfinance can't fetch)
_FALLBACK_COMPOSITIONS: dict[str, dict[str, float]] = {
    "VT": {"AAPL": 4.2, "MSFT": 3.8, "NVDA": 3.2, "AMZN": 2.5, "GOOGL": 1.5, "META": 1.5, "AVGO": 1.0, "2330.TW": 0.8, "005930.KS": 0.5},
    "VOO": {"AAPL": 7.0, "MSFT": 6.5, "NVDA": 5.5, "AMZN": 4.0, "GOOGL": 2.5, "META": 2.5, "AVGO": 1.7, "JPM": 1.5},
    "SPY": {"AAPL": 7.0, "MSFT": 6.5, "NVDA": 5.5, "AMZN": 4.0, "GOOGL": 2.5, "META": 2.5, "AVGO": 1.7, "JPM": 1.5},
    "IWDA.AS": {"AAPL": 5.0, "MSFT": 4.5, "NVDA": 3.8, "AMZN": 3.0, "GOOGL": 1.8, "META": 1.8, "AVGO": 1.2},
    "VWCE.DE": {"AAPL": 4.2, "MSFT": 3.8, "NVDA": 3.2, "AMZN": 2.5, "GOOGL": 1.5, "META": 1.5, "AVGO": 1.0},
}

# Cache for dynamically fetched ETF compositions
_etf_composition_cache: dict[str, dict[str, float]] = {}


def _fetch_etf_composition(ticker: str) -> dict[str, float]:
    """Dynamically fetch ETF top holdings via yfinance.

    Returns a dict of constituent ticker → weight (%).
    Falls back to hardcoded data for known ETFs, or empty dict for unknown.
    """
    if ticker in _etf_composition_cache:
        return _etf_composition_cache[ticker]

    import yfinance as yf

    try:
        t = yf.Ticker(ticker)
        # Try funds_data.top_holdings (returns DataFrame with Symbol index + Holding Name, % Of Net Assets)
        try:
            top_holdings = t.funds_data.top_holdings
            if top_holdings is not None and not top_holdings.empty:
                composition: dict[str, float] = {}
                for sym in top_holdings.index:
                    if isinstance(sym, str) and sym.strip():
                        # % Of Net Assets is typically 0.0-1.0 or 0-100
                        weight = 0.0
                        try:
                            val = top_holdings.loc[sym]
                            if hasattr(val, "iloc"):
                                weight = float(val.iloc[0]) if len(val) > 0 else 0.0
                            else:
                                weight = float(val)
                        except (ValueError, TypeError, IndexError):
                            pass
                        # Normalize: if weights look like fractions (< 1), convert to %
                        if 0 < weight < 1:
                            weight *= 100
                        if weight > 0:
                            composition[sym.strip().upper()] = round(weight, 2)
                if composition:
                    log.info("Fetched %d holdings for %s via yfinance", len(composition), ticker)
                    _etf_composition_cache[ticker] = composition
                    return composition
        except Exception as exc:
            log.debug("funds_data.top_holdings failed for %s: %s", ticker, exc)

        # Try alternative: get_holdings()
        try:
            holdings_df = t.get_holdings()
            if holdings_df is not None and not holdings_df.empty:
                composition = {}
                for _, row in holdings_df.iterrows():
                    sym = row.get("Symbol") or row.get("symbol") or ""
                    weight = row.get("% Of Net Assets") or row.get("pctNetAssets") or 0
                    if sym and weight:
                        w = float(weight)
                        if 0 < w < 1:
                            w *= 100
                        if w > 0:
                            composition[str(sym).strip().upper()] = round(w, 2)
                if composition:
                    log.info("Fetched %d holdings for %s via get_holdings()", len(composition), ticker)
                    _etf_composition_cache[ticker] = composition
                    return composition
        except Exception as exc:
            log.debug("get_holdings() failed for %s: %s", ticker, exc)

    except Exception as exc:
        log.debug("yfinance ticker init failed for %s: %s", ticker, exc)

    # Fallback to hardcoded
    if ticker in _FALLBACK_COMPOSITIONS:
        log.debug("Using hardcoded fallback composition for %s", ticker)
        _etf_composition_cache[ticker] = _FALLBACK_COMPOSITIONS[ticker]
        return _FALLBACK_COMPOSITIONS[ticker]

    log.debug("No composition data available for %s — treating as individual holding", ticker)
    _etf_composition_cache[ticker] = {}
    return {}


def _is_likely_etf(ticker: str) -> bool:
    """Heuristic: check if a ticker is likely an ETF/index fund."""
    # Known ETFs
    if ticker.upper() in _FALLBACK_COMPOSITIONS:
        return True
    # Try yfinance quoteType
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
        qt = info.get("quoteType", "").upper()
        return qt in ("ETF", "MUTUALFUND")
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Load & analyze
# ---------------------------------------------------------------------------

def load_holdings(path: str | Path) -> HoldingsPortfolio:
    """Load holdings from a JSON file."""
    p = Path(path)
    if not p.exists():
        log.warning("Holdings file not found: %s", p)
        return HoldingsPortfolio()

    data = json.loads(p.read_text())
    if isinstance(data, list):
        return HoldingsPortfolio(holdings=[Holding(**h) for h in data])
    return HoldingsPortfolio(**data)


def get_existing_exposure(holdings: HoldingsPortfolio) -> dict[str, float]:
    """Compute effective ticker exposure from holdings, including index decomposition.

    Fetches current prices via yfinance to compute dollar-weighted exposure.
    Falls back to equal-weight if prices unavailable.

    Returns a dict of ticker → approximate weight (%) in the existing portfolio.
    For index funds, decomposes into constituent weights.
    """
    exposure: dict[str, float] = {}
    n = len(holdings.holdings)
    if n == 0:
        return exposure

    # Compute dollar value per holding using current prices
    import yfinance as yf

    holding_values: dict[str, float] = {}
    total_value = 0.0

    for h in holdings.holdings:
        ticker = h.ticker.upper()
        dollar_value = None

        # Try to get current price
        try:
            info = yf.Ticker(h.ticker).info or {}
            price = info.get("regularMarketPrice") or info.get("currentPrice")
            if price and h.shares > 0:
                dollar_value = price * h.shares
        except Exception:
            pass

        # Fallback: use avg_cost if available
        if dollar_value is None and h.avg_cost and h.shares > 0:
            dollar_value = h.avg_cost * h.shares
            log.debug("Using avg_cost for %s (no live price)", ticker)

        if dollar_value and dollar_value > 0:
            holding_values[ticker] = dollar_value
            total_value += dollar_value

    # If we couldn't price anything, fall back to equal weight
    if total_value == 0:
        log.warning("Could not determine portfolio value — using equal-weight approximation.")
        equal_weight = 100.0 / n
        for h in holdings.holdings:
            ticker = h.ticker.upper()
            composition = _fetch_etf_composition(ticker)
            if composition:
                for constituent, weight_in_index in composition.items():
                    exposure[constituent] = exposure.get(constituent, 0.0) + (weight_in_index * equal_weight / 100.0)
            else:
                exposure[ticker] = exposure.get(ticker, 0.0) + equal_weight
        return exposure

    # Dollar-weighted exposure
    for h in holdings.holdings:
        ticker = h.ticker.upper()
        dv = holding_values.get(ticker, 0.0)
        if dv <= 0:
            continue
        weight_pct = (dv / total_value) * 100.0

        # Decompose ETFs/index funds into constituents
        composition = _fetch_etf_composition(ticker)
        if composition:
            for constituent, weight_in_index in composition.items():
                exposure[constituent] = exposure.get(constituent, 0.0) + (weight_in_index * weight_pct / 100.0)
        else:
            exposure[ticker] = exposure.get(ticker, 0.0) + weight_pct

    log.info(
        "Portfolio value: $%.0f across %d holdings (%d with live prices)",
        total_value, n, len(holding_values),
    )
    return exposure


def analyze_overlap(
    existing_exposure: dict[str, float],
    theme_companies: list[str],
    theme_allocation_pct: float,
) -> list[dict]:
    """Find companies that appear in both existing holdings and a theme.

    Returns list of overlaps with existing weight and new weight.
    """
    overlaps = []
    for ticker in theme_companies:
        ticker_upper = ticker.upper()
        if ticker_upper in existing_exposure:
            overlaps.append({
                "ticker": ticker,
                "existing_pct": round(existing_exposure[ticker_upper], 2),
                "new_pct": round(theme_allocation_pct / max(len(theme_companies), 1), 2),
                "combined_pct": round(
                    existing_exposure[ticker_upper]
                    + theme_allocation_pct / max(len(theme_companies), 1),
                    2,
                ),
            })
    return overlaps
