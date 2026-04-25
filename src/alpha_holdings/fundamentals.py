"""Fundamentals fetcher: yfinance (global) + file cache."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import yfinance as yf

from alpha_holdings.models import Fundamentals

log = logging.getLogger(__name__)

CACHE_DIR = Path("data/cache")
CACHE_TTL = timedelta(hours=24)


def fetch(ticker: str) -> Fundamentals:
    """Fetch fundamentals for a single ticker, using cache if fresh."""
    cached = _load_cache(ticker)
    if cached:
        return cached

    log.info("Fetching fundamentals for %s...", ticker)
    fundamentals = _fetch_yfinance(ticker)
    fundamentals.fetched_at = datetime.utcnow()
    _save_cache(ticker, fundamentals)
    return fundamentals


def fetch_batch(tickers: list[str]) -> dict[str, Fundamentals]:
    """Fetch fundamentals for a list of tickers."""
    results = {}
    for t in tickers:
        try:
            results[t] = fetch(t)
        except Exception as exc:
            log.warning("Failed to fetch %s: %s", t, exc)
            results[t] = Fundamentals(ticker=t)
    return results


def _fetch_yfinance(ticker: str) -> Fundamentals:
    """Pull fundamentals from yfinance."""
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
    except Exception as exc:
        log.warning("yfinance error for %s: %s", ticker, exc)
        return Fundamentals(ticker=ticker)

    current = info.get("regularMarketPrice") or info.get("currentPrice")
    high_52 = info.get("fiftyTwoWeekHigh")
    drawdown = None
    if current and high_52 and high_52 > 0:
        drawdown = round((current - high_52) / high_52 * 100, 2)

    # Revenue growth 3yr CAGR
    revenue_growth = None
    try:
        financials = t.financials
        if financials is not None and len(financials.columns) >= 3:
            revenues = financials.loc["Total Revenue"] if "Total Revenue" in financials.index else None
            if revenues is not None and len(revenues) >= 3:
                recent = revenues.iloc[0]
                old = revenues.iloc[2]
                if old and old > 0 and recent and recent > 0:
                    revenue_growth = round(((recent / old) ** (1 / 3) - 1) * 100, 2)
    except Exception:
        pass

    # Technical indicators
    return_2yr = _compute_2yr_return(t)
    pct_200dma = _compute_200dma_position(t, current)
    trailing_pe = info.get("trailingPE")
    fwd_pe = info.get("forwardPE")
    pe_revision = round(fwd_pe / trailing_pe, 2) if fwd_pe and trailing_pe and trailing_pe > 0 else None
    pe_vs_history = _compute_pe_vs_history(t, fwd_pe)

    return Fundamentals(
        ticker=ticker,
        name=info.get("shortName") or info.get("longName"),
        sector=info.get("sector"),
        market_cap=info.get("marketCap"),
        revenue_growth_3yr_cagr=revenue_growth,
        gross_margin=_pct(info.get("grossMargins")),
        operating_margin=_pct(info.get("operatingMargins")),
        free_cash_flow=info.get("freeCashflow"),
        fcf_yield=_compute_fcf_yield(info),
        pe_ratio=trailing_pe,
        forward_pe=fwd_pe,
        peg_ratio=info.get("pegRatio"),
        debt_to_equity=info.get("debtToEquity"),
        roe=_pct(info.get("returnOnEquity")),
        rd_pct_revenue=None,  # yfinance doesn't provide directly
        high_52w=high_52,
        low_52w=info.get("fiftyTwoWeekLow"),
        current_price=current,
        drawdown_from_peak=drawdown,
        ev_to_ebitda=info.get("enterpriseToEbitda"),
        avg_daily_volume=info.get("averageDailyVolume10Day"),
        return_2yr=return_2yr,
        pct_from_200dma=pct_200dma,
        pe_revision_ratio=pe_revision,
        pe_vs_own_history=pe_vs_history,
    )


def _compute_2yr_return(ticker_obj) -> Optional[float]:
    """Compute 2-year price return %."""
    try:
        hist = ticker_obj.history(period="2y")
        if hist is not None and len(hist) >= 20:
            start = hist["Close"].iloc[0]
            end = hist["Close"].iloc[-1]
            if start and start > 0:
                return round((end / start - 1) * 100, 2)
    except Exception:
        pass
    return None


def _compute_200dma_position(ticker_obj, current_price: Optional[float]) -> Optional[float]:
    """Compute % distance from 200-day moving average."""
    if not current_price:
        return None
    try:
        hist = ticker_obj.history(period="1y")
        if hist is not None and len(hist) >= 200:
            ma_200 = hist["Close"].rolling(200).mean().iloc[-1]
            if ma_200 and ma_200 > 0:
                return round((current_price / ma_200 - 1) * 100, 2)
    except Exception:
        pass
    return None


def _compute_pe_vs_history(ticker_obj, current_forward_pe: Optional[float]) -> Optional[float]:
    """Compare current forward P/E to the stock's historical P/E range.

    Returns current forward P/E as a percentage of the 5yr average trailing P/E.
    <80 = cheap vs own history, >120 = expensive vs own history.
    """
    if not current_forward_pe or current_forward_pe <= 0:
        return None
    try:
        # Use price/earnings history as proxy
        hist = ticker_obj.history(period="5y")
        if hist is None or len(hist) < 200:
            return None
        # Approximate historical P/E from price history + current EPS
        info = ticker_obj.info or {}
        trailing_eps = info.get("trailingEps")
        if not trailing_eps or trailing_eps <= 0:
            return None
        # Compute average price over 5 years / current EPS as rough historical P/E proxy
        avg_price_5yr = hist["Close"].mean()
        if avg_price_5yr and avg_price_5yr > 0:
            historical_pe_proxy = avg_price_5yr / trailing_eps
            if historical_pe_proxy > 0:
                return round((current_forward_pe / historical_pe_proxy) * 100, 0)
    except Exception:
        pass
    return None


def _pct(val) -> Optional[float]:
    """Convert ratio (0.25) to percentage (25.0) if not None."""
    if val is None:
        return None
    if isinstance(val, (int, float)) and abs(val) < 1:
        return round(val * 100, 2)
    return round(float(val), 2)


def _compute_fcf_yield(info: dict) -> Optional[float]:
    fcf = info.get("freeCashflow")
    mcap = info.get("marketCap")
    if fcf and mcap and mcap > 0:
        return round(fcf / mcap * 100, 2)
    return None


# ---------------------------------------------------------------------------
# Quality filter
# ---------------------------------------------------------------------------

def passes_quality_filter(f: Fundamentals) -> tuple[bool, str]:
    """Check if a company meets minimum quality thresholds.
    
    Returns (passes, reason) where reason explains rejection.
    """
    from alpha_holdings.config import MIN_MARKET_CAP, MIN_AVG_DAILY_VOLUME, QUALITY_FLOOR

    # Market cap floor
    if f.market_cap is not None and f.market_cap < MIN_MARKET_CAP:
        return False, f"Market cap ${f.market_cap / 1e6:.0f}M below ${MIN_MARKET_CAP / 1e6:.0f}M minimum"

    # Volume floor
    if f.avg_daily_volume is not None and f.current_price is not None:
        dollar_volume = f.avg_daily_volume * f.current_price
        if dollar_volume < MIN_AVG_DAILY_VOLUME:
            return False, f"Avg daily $ volume ${dollar_volume / 1e6:.1f}M below ${MIN_AVG_DAILY_VOLUME / 1e6:.0f}M minimum"

    # Debt ceiling
    if f.debt_to_equity is not None and f.debt_to_equity > QUALITY_FLOOR["max_debt_to_equity"]:
        return False, f"Debt/equity {f.debt_to_equity:.0f} exceeds {QUALITY_FLOOR['max_debt_to_equity']} maximum"

    # Operating margin floor
    if f.operating_margin is not None and f.operating_margin < QUALITY_FLOOR["min_operating_margin"]:
        return False, f"Operating margin {f.operating_margin:.1f}% below {QUALITY_FLOOR['min_operating_margin']}% floor"

    # Must have revenue (market cap as proxy — pre-revenue SPACs/explorers often have tiny market cap)
    if QUALITY_FLOOR["require_revenue"] and f.market_cap is not None and f.market_cap < 100_000_000:
        if f.revenue_growth_3yr_cagr is None and f.gross_margin is None:
            return False, "Appears pre-revenue with no financial history"

    # 2-year price momentum: reject persistent decliners
    if f.return_2yr is not None and f.return_2yr < -30:
        return False, f"2-year return {f.return_2yr:.0f}% — persistent decline suggests structural issues"

    return True, "OK"


def get_technical_flags(f: Fundamentals) -> list[str]:
    """Return warning/info flags for technical indicators."""
    flags = []

    # 200-DMA position: stock in a downtrend
    if f.pct_from_200dma is not None and f.pct_from_200dma < -20:
        flags.append(f"📉 {f.pct_from_200dma:.0f}% below 200-DMA — downtrend")

    # Earnings revision: estimates being cut
    if f.pe_revision_ratio is not None and f.pe_revision_ratio > 1.3:
        flags.append(f"⚠ Forward P/E > trailing P/E (ratio {f.pe_revision_ratio:.1f}x) — estimates may be getting cut")

    # Historical P/E comparison
    if f.pe_vs_own_history is not None:
        if f.pe_vs_own_history < 70:
            flags.append(f"🏷️ P/E at {f.pe_vs_own_history:.0f}% of 5yr avg — cheap vs own history")
        elif f.pe_vs_own_history > 130:
            flags.append(f"💰 P/E at {f.pe_vs_own_history:.0f}% of 5yr avg — expensive vs own history")

    return flags


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _cache_path(ticker: str) -> Path:
    safe = ticker.replace("/", "_").replace(".", "_")
    return CACHE_DIR / f"{safe}.json"


def _load_cache(ticker: str) -> Optional[Fundamentals]:
    path = _cache_path(ticker)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        fetched = data.get("fetched_at")
        if fetched:
            fetched_dt = datetime.fromisoformat(fetched)
            if datetime.utcnow() - fetched_dt > CACHE_TTL:
                return None
        return Fundamentals(**data)
    except Exception:
        return None


def _save_cache(ticker: str, f: Fundamentals) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(ticker)
    path.write_text(f.model_dump_json(indent=2))
