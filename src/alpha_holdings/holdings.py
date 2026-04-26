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

# Fallback: hardcoded approximate top holdings (used when yfinance + LLM can't fetch)
_FALLBACK_COMPOSITIONS: dict[str, dict[str, float]] = {
    "VT": {"AAPL": 4.2, "MSFT": 3.8, "NVDA": 3.2, "AMZN": 2.5, "GOOGL": 1.5, "META": 1.5, "AVGO": 1.0, "2330.TW": 0.8, "005930.KS": 0.5},
    "VOO": {"AAPL": 7.0, "MSFT": 6.5, "NVDA": 5.5, "AMZN": 4.0, "GOOGL": 2.5, "META": 2.5, "AVGO": 1.7, "JPM": 1.5},
    "SPY": {"AAPL": 7.0, "MSFT": 6.5, "NVDA": 5.5, "AMZN": 4.0, "GOOGL": 2.5, "META": 2.5, "AVGO": 1.7, "JPM": 1.5},
    "IWDA.AS": {"AAPL": 5.0, "MSFT": 4.5, "NVDA": 3.8, "AMZN": 3.0, "GOOGL": 1.8, "META": 1.8, "AVGO": 1.2},
    "VWCE.DE": {"AAPL": 4.2, "MSFT": 3.8, "NVDA": 3.2, "AMZN": 2.5, "GOOGL": 1.5, "META": 1.5, "AVGO": 1.0},
    "SMH": {"NVDA": 20.0, "TSM": 12.0, "AVGO": 8.0, "ASML": 5.0, "TXN": 5.0, "AMD": 4.5, "QCOM": 4.0, "AMAT": 4.0, "MU": 3.5, "INTC": 3.0},
    "SOXX": {"NVDA": 9.0, "AVGO": 8.5, "AMD": 7.5, "QCOM": 5.5, "TXN": 5.0, "MU": 4.5, "AMAT": 4.0, "INTC": 3.5, "MRVL": 3.5, "TSM": 3.0},
    "XLK": {"AAPL": 16.0, "MSFT": 14.0, "NVDA": 13.0, "AVGO": 5.0, "CRM": 2.5, "AMD": 2.0, "ADBE": 2.0, "ORCL": 2.0, "ACN": 2.0},
    "QQQ": {"AAPL": 9.0, "MSFT": 8.0, "NVDA": 7.5, "AMZN": 5.5, "META": 4.5, "AVGO": 4.0, "GOOGL": 3.0, "GOOG": 2.5, "TSLA": 2.5, "COST": 2.5},
    "URA": {"CCJ": 18.0, "NXE": 7.0, "UUUU": 5.5, "DNN": 4.5, "LEU": 4.0, "PDN.AX": 3.5, "DYL.AX": 3.0, "SRUUF": 3.0, "UEC": 3.0, "FCU.TO": 2.5},
    "HACK": {"CRWD": 6.5, "FTNT": 6.0, "PANW": 5.5, "ZS": 4.0, "OKTA": 3.5, "CYBR": 3.5, "CHKP": 3.0, "MNDT": 3.0, "RPD": 2.5, "NET": 2.5},
    "XME": {"NUE": 5.5, "STLD": 5.0, "FCX": 5.0, "AA": 4.5, "CLF": 4.0, "RS": 3.5, "CMC": 3.0, "ATI": 3.0, "MP": 2.5, "CRS": 2.5},
    "XLE": {"XOM": 23.0, "CVX": 17.0, "COP": 5.0, "SLB": 4.5, "EOG": 4.0, "MPC": 4.0, "PXD": 3.5, "PSX": 3.5, "VLO": 3.0, "OXY": 2.5},
    "ITA": {"RTX": 18.0, "LMT": 6.0, "GE": 5.0, "BA": 5.0, "NOC": 4.5, "GD": 4.0, "LHX": 4.0, "TDG": 3.5, "HII": 3.0, "TXT": 2.5},
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

    # Fallback 2: ask LLM for approximate composition (with web search for accuracy)
    try:
        from alpha_holdings import llm
        from alpha_holdings.signals import _extract_json

        prompt = (
            f"For educational research purposes only. "
            f"What are the approximate top 10 holdings and their weights (%) "
            f"in the ETF '{ticker}'? Search the web for the latest holdings. "
            f"Return ONLY a JSON object mapping "
            f"ticker symbols to weight percentages, e.g. {{\"NVDA\": 20.0, \"TSM\": 12.0}}. "
            f"If you cannot determine the holdings, return an empty object {{}}."
        )
        raw = llm.respond_text(prompt, mini=True, web_search=True)
        data = json.loads(_extract_json(raw))
        if isinstance(data, dict) and data:
            composition = {k.upper(): round(float(v), 2) for k, v in data.items() if float(v) > 0}
            if composition:
                log.info("Fetched %d holdings for %s via LLM", len(composition), ticker)
                _etf_composition_cache[ticker] = composition
                return composition
    except Exception as exc:
        log.debug("LLM composition fetch failed for %s: %s", ticker, exc)

    # Fallback 3: hardcoded
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
    """Load holdings from a JSON file.

    Accepts two formats:
    - Holdings list: [{"ticker": "...", "shares": N, "avg_cost": X}, ...]
    - Allocation file: {"entries": [...], ...} (auto-detected from data/allocations/)
    """
    p = Path(path)
    if not p.exists():
        log.warning("Holdings file not found: %s", p)
        return HoldingsPortfolio()

    data = json.loads(p.read_text())
    if isinstance(data, list):
        return HoldingsPortfolio(holdings=[Holding(**h) for h in data])
    # Auto-detect allocation format (has "entries" key with vehicles + entry_prices)
    if isinstance(data, dict) and "entries" in data:
        return _allocation_to_portfolio(data)
    return HoldingsPortfolio(**data)


def _allocation_to_portfolio(alloc_data: dict) -> HoldingsPortfolio:
    """Convert a PortfolioAllocation dict into a HoldingsPortfolio.

    Extracts tickers from allocation entries and uses entry_prices as avg_cost.
    """
    holdings: list[Holding] = []
    for entry in alloc_data.get("entries", []):
        entry_prices = entry.get("entry_prices", {})
        tickers = [t.strip() for t in entry.get("vehicle", "").split(",") if t.strip()]
        for ticker in tickers:
            holdings.append(Holding(
                ticker=ticker,
                shares=0,
                avg_cost=entry_prices.get(ticker),
            ))
    return HoldingsPortfolio(holdings=holdings)


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
