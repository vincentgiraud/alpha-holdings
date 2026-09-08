"""Fundamentals fetcher: yfinance (global) + file cache."""

from __future__ import annotations

import json
import logging
import math
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional, Protocol

import yfinance as yf

from alpha_holdings.models import (
    Company,
    Fundamentals,
    FundamentalsResult,
    InstrumentType,
    MarketDataStatus,
)

log = logging.getLogger(__name__)

CACHE_DIR = Path("data/cache")
CACHE_TTL = timedelta(hours=24)

# Candidates must be tradable and have at least half of the eight inputs used
# by the fixed-weight fundamental score. Missing optional metrics receive a
# neutral score; they never cause the remaining metrics to be reweighted.
REQUIRED_MARKET_FIELDS = ("market_cap", "current_price", "avg_daily_volume")
SCORING_METRIC_FIELDS = (
    "revenue_growth_cagr",
    "roe",
    "gross_margin",
    "operating_margin",
    "fcf_yield",
    "forward_pe",
    "peg_ratio",
    "debt_to_equity",
)
MIN_SCORING_METRICS = 4


class FundamentalsProvider(Protocol):
    """External provider boundary used by the fundamentals fetcher."""

    def fetch(self, ticker: str) -> Fundamentals: ...


class YahooFinanceProvider:
    def fetch(self, ticker: str) -> Fundamentals:
        return _fetch_yfinance(ticker)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


DEFAULT_PROVIDER: FundamentalsProvider = YahooFinanceProvider()
DEFAULT_CLOCK: Callable[[], datetime] = _utc_now


def fetch(
    ticker: str,
    *,
    skip_cache: bool = False,
    provider: FundamentalsProvider | None = None,
    clock: Callable[[], datetime] | None = None,
    cache_dir: Path = CACHE_DIR,
) -> FundamentalsResult:
    """Fetch fundamentals for a single ticker, using cache if fresh."""
    now = _as_utc((clock or DEFAULT_CLOCK)())
    cached = None if skip_cache else _load_cache(ticker, cache_dir=cache_dir)
    if cached is not None and not _has_market_data(cached):
        cached = None
    if cached and cached.fetched_at:
        as_of = _as_utc(cached.fetched_at)
        age = max((now - as_of).total_seconds(), 0.0)
        if age <= CACHE_TTL.total_seconds():
            return FundamentalsResult(
                ticker=ticker,
                status=MarketDataStatus.AVAILABLE,
                data=cached,
                observed_at=now,
                as_of=as_of,
                from_cache=True,
                age=age,
            )

    log.info("Fetching fundamentals for %s...", ticker)
    data_provider = provider or DEFAULT_PROVIDER
    try:
        fundamentals = data_provider.fetch(ticker)
    except Exception as exc:
        return _refresh_failure(
            ticker,
            now=now,
            cached=cached,
            status=MarketDataStatus.UNAVAILABLE,
            reason=str(exc),
        )
    if not isinstance(fundamentals, Fundamentals):
        return _refresh_failure(
            ticker,
            now=now,
            cached=cached,
            status=MarketDataStatus.INVALID,
            reason="provider returned an invalid payload",
        )
    if not _has_market_data(fundamentals):
        return _refresh_failure(
            ticker,
            now=now,
            cached=cached,
            status=MarketDataStatus.INVALID,
            reason="provider returned no market data",
        )
    if not fundamentals.source:
        fundamentals.source = type(data_provider).__name__
    fundamentals.fetched_at = now
    _save_cache(ticker, fundamentals, cache_dir=cache_dir)
    return FundamentalsResult(
        ticker=ticker,
        status=MarketDataStatus.AVAILABLE,
        data=fundamentals,
        observed_at=now,
        as_of=now,
        from_cache=False,
        age=0.0,
    )


def fetch_batch(
    tickers: list[str],
    *,
    skip_cache: bool = False,
    provider: FundamentalsProvider | None = None,
    clock: Callable[[], datetime] | None = None,
    cache_dir: Path = CACHE_DIR,
) -> dict[str, FundamentalsResult]:
    """Fetch fundamentals for a list of tickers."""
    results: dict[str, FundamentalsResult] = {}
    for t in tickers:
        results[t] = fetch(
            t,
            skip_cache=skip_cache,
            provider=provider,
            clock=clock,
            cache_dir=cache_dir,
        )
    return results


def _fetch_yfinance(ticker: str) -> Fundamentals:
    """Pull fundamentals from yfinance."""
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
    except Exception as exc:
        log.warning("yfinance error for %s: %s", ticker, exc)
        raise

    current = info.get("regularMarketPrice") or info.get("currentPrice")
    high_52 = info.get("fiftyTwoWeekHigh")
    drawdown = None
    if current and high_52 and high_52 > 0:
        drawdown = round((current - high_52) / high_52 * 100, 2)

    # Revenue CAGR over the actual dates returned by the provider.
    revenue_growth = None
    revenue_growth_period = None
    try:
        financials = t.financials
        if financials is not None and len(financials.columns) >= 2:
            revenues = financials.loc["Total Revenue"] if "Total Revenue" in financials.index else None
            if revenues is not None and len(revenues) >= 2:
                observations = []
                for observed_at, value in revenues.items():
                    try:
                        numeric_value = float(value)
                    except (TypeError, ValueError):
                        continue
                    if _is_positive_finite(numeric_value):
                        observations.append((_as_date(observed_at), numeric_value))
                observations.sort(key=lambda item: item[0])
                if len(observations) >= 2:
                    oldest_date, oldest_revenue = observations[0]
                    latest_date, latest_revenue = observations[-1]
                    revenue_growth, revenue_growth_period = calculate_cagr(
                        oldest_revenue,
                        oldest_date,
                        latest_revenue,
                        latest_date,
                    )
                    revenue_growth = round(revenue_growth, 2)
                    revenue_growth_period = round(revenue_growth_period, 2)
    except Exception:
        pass

    # Technical indicators
    return_2yr = _compute_2yr_return(t)
    pct_200dma = _compute_200dma_position(t, current)
    trailing_pe = info.get("trailingPE")
    fwd_pe = info.get("forwardPE")
    forward_to_trailing_pe = (
        round(fwd_pe / trailing_pe, 2)
        if fwd_pe and fwd_pe > 0 and trailing_pe and trailing_pe > 0
        else None
    )
    price_history_proxy = _compute_forward_pe_price_proxy(t, fwd_pe)

    return Fundamentals(
        ticker=ticker,
        provider_symbol=info.get("symbol"),
        name=info.get("shortName") or info.get("longName"),
        quote_type=info.get("quoteType"),
        source="yfinance",
        sector=info.get("sector"),
        market_cap=info.get("marketCap"),
        revenue_growth_cagr=revenue_growth,
        revenue_growth_period_years=revenue_growth_period,
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
        forward_to_trailing_pe_ratio=forward_to_trailing_pe,
        forward_pe_vs_price_history_proxy=price_history_proxy,
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


def calculate_cagr(
    start_value: float,
    start_date: date,
    end_value: float,
    end_date: date,
) -> tuple[float, float]:
    """Return annualized percentage growth and actual elapsed years."""
    elapsed_days = (end_date - start_date).days
    if (
        not _is_positive_finite(start_value)
        or not _is_positive_finite(end_value)
        or elapsed_days <= 0
    ):
        raise ValueError("CAGR requires positive values and an increasing date range")
    elapsed_years = elapsed_days / 365.2425
    growth = ((end_value / start_value) ** (1 / elapsed_years) - 1) * 100
    return growth, elapsed_years


def _as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date()


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


def _compute_forward_pe_price_proxy(
    ticker_obj,
    current_forward_pe: Optional[float],
) -> Optional[float]:
    """Compare forward P/E with historical price divided by current EPS.

    This is explicitly a price/current-EPS proxy, not a historical P/E series.
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

_COMPANY_NAME_STOPWORDS = {
    "class",
    "company",
    "corp",
    "corporation",
    "group",
    "holding",
    "holdings",
    "inc",
    "limited",
    "ltd",
    "plc",
    "sa",
    "the",
}


def validate_company_identity(
    company: Company,
    fundamentals: Fundamentals,
) -> tuple[bool, str]:
    """Verify provider symbol/type/name metadata against the proposed company."""
    expected_symbol = company.full_ticker.strip().upper()
    identity_error = _provider_identity_error(
        expected_symbol,
        fundamentals,
        expected_quote_types={"EQUITY", "STOCK"},
        identity_label="company",
        quote_type_label="an equity",
    )
    if identity_error:
        return False, identity_error
    if not _company_names_match(company.name, fundamentals.name):
        return False, (
            f"provider name '{fundamentals.name}' does not match '{company.name}'"
        )
    return True, "OK"


def validate_priced_instrument(
    ticker: str,
    instrument_type: InstrumentType,
    fundamentals: Fundamentals,
) -> tuple[bool, str]:
    """Validate identity, type, and dated positive price for allocation."""
    expected_ticker = ticker.strip().upper()
    expected_quote_types = (
        {"ETF"}
        if instrument_type is InstrumentType.ETF
        else {"EQUITY", "STOCK"}
    )
    identity_error = _provider_identity_error(
        expected_ticker,
        fundamentals,
        expected_quote_types=expected_quote_types,
        identity_label="instrument",
        quote_type_label=f"valid for {instrument_type.value}",
    )
    if identity_error:
        return False, identity_error
    if not _is_positive_finite(fundamentals.current_price):
        return False, "provider did not report a positive finite price"
    if fundamentals.fetched_at is None:
        return False, "provider price is missing its observation timestamp"
    return True, "OK"


def _provider_identity_error(
    expected_ticker: str,
    fundamentals: Fundamentals,
    *,
    expected_quote_types: set[str],
    identity_label: str,
    quote_type_label: str,
) -> str | None:
    """Return why provider identity/type evidence is invalid, if it is."""
    fundamentals_ticker = fundamentals.ticker.strip().upper()
    if fundamentals_ticker != expected_ticker:
        return (
            f"fundamentals ticker '{fundamentals_ticker}' does not match "
            f"'{expected_ticker}'"
        )
    provider_symbol = (fundamentals.provider_symbol or "").strip().upper()
    if not provider_symbol:
        return "provider did not report symbol identity"
    if provider_symbol != expected_ticker:
        return (
            f"provider symbol '{provider_symbol}' does not match '{expected_ticker}'"
        )
    quote_type = (fundamentals.quote_type or "").strip().upper()
    if quote_type not in expected_quote_types:
        return (
            f"provider quote type '{quote_type or 'missing'}' is not "
            f"{quote_type_label}"
        )
    if not fundamentals.name:
        return f"provider did not report {identity_label} identity"
    return None


def _company_names_match(expected: str, actual: str) -> bool:
    expected_tokens = _company_name_tokens(expected)
    actual_tokens = _company_name_tokens(actual)
    if not expected_tokens or not actual_tokens:
        return False
    if expected_tokens[0] == actual_tokens[0]:
        return True
    if len(set(expected_tokens) & set(actual_tokens)) >= 2:
        return True
    expected_compact = "".join(expected_tokens)
    actual_compact = "".join(actual_tokens)
    if expected_compact in actual_compact or actual_compact in expected_compact:
        return True
    expected_acronym = "".join(token[0] for token in expected_tokens)
    actual_acronym = "".join(token[0] for token in actual_tokens)
    return expected_compact == actual_acronym or actual_compact == expected_acronym


def _company_name_tokens(value: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if token not in _COMPANY_NAME_STOPWORDS
    ]


def passes_quality_filter(
    observation: Fundamentals | FundamentalsResult,
) -> tuple[bool, str]:
    """Check if a company meets minimum quality thresholds.
    
    Returns (passes, reason) where reason explains rejection.
    """
    from alpha_holdings.config import MIN_MARKET_CAP, MIN_AVG_DAILY_VOLUME, QUALITY_FLOOR

    if isinstance(observation, FundamentalsResult):
        if observation.data is None:
            detail = f": {observation.reason}" if observation.reason else ""
            return False, f"Market data {observation.status.value}{detail}"
        f = observation.data
    else:
        f = observation

    missing_required = [
        field
        for field in REQUIRED_MARKET_FIELDS
        if not _is_positive_finite(getattr(f, field))
    ]
    available_metrics = sum(
        _is_usable_scoring_metric(field, getattr(f, field))
        for field in SCORING_METRIC_FIELDS
    )
    if missing_required or available_metrics < MIN_SCORING_METRICS:
        missing_summary = ", ".join(missing_required) if missing_required else "none"
        return False, (
            f"Incomplete fundamentals: missing required {missing_summary}; "
            f"{available_metrics}/{len(SCORING_METRIC_FIELDS)} scoring metrics "
            f"available (minimum {MIN_SCORING_METRICS})"
        )

    # Market cap floor
    if f.market_cap is not None and f.market_cap < MIN_MARKET_CAP:
        return False, f"Market cap ${f.market_cap / 1e6:.0f}M below ${MIN_MARKET_CAP / 1e6:.0f}M minimum"

    # Volume floor
    if f.avg_daily_volume is not None and f.current_price is not None:
        dollar_volume = f.avg_daily_volume * f.current_price
        if dollar_volume < MIN_AVG_DAILY_VOLUME:
            return False, f"Avg daily $ volume ${dollar_volume / 1e6:.1f}M below ${MIN_AVG_DAILY_VOLUME / 1e6:.0f}M minimum"

    # Debt ceiling — exempt profitable companies (high D/E from buybacks, not distress)
    if f.debt_to_equity is not None and f.debt_to_equity > QUALITY_FLOOR["max_debt_to_equity"]:
        margin_ok = f.operating_margin is not None and f.operating_margin > 15
        if not margin_ok:
            return False, f"Debt/equity {f.debt_to_equity:.0f} exceeds {QUALITY_FLOOR['max_debt_to_equity']} maximum"

    # Operating margin floor
    if f.operating_margin is not None and f.operating_margin < QUALITY_FLOOR["min_operating_margin"]:
        return False, f"Operating margin {f.operating_margin:.1f}% below {QUALITY_FLOOR['min_operating_margin']}% floor"

    # Must have revenue (market cap as proxy — pre-revenue SPACs/explorers often have tiny market cap)
    if QUALITY_FLOOR["require_revenue"] and f.market_cap is not None and f.market_cap < 100_000_000:
        if f.revenue_growth_cagr is None and f.gross_margin is None:
            return False, "Appears pre-revenue with no financial history"

    # 2-year price momentum: reject persistent decliners
    if f.return_2yr is not None and f.return_2yr < -30:
        return False, f"2-year return {f.return_2yr:.0f}% — persistent decline suggests structural issues"

    return True, "OK"


def _is_positive_finite(value) -> bool:
    return (
        isinstance(value, (int, float))
        and math.isfinite(value)
        and value > 0
    )


def _is_usable_scoring_metric(field: str, value) -> bool:
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return False
    if field in {"forward_pe", "peg_ratio"}:
        return value > 0
    return True


def get_technical_flags(f: Fundamentals) -> list[str]:
    """Return warning/info flags for technical indicators."""
    flags = []

    # 200-DMA position: stock in a downtrend
    if f.pct_from_200dma is not None and f.pct_from_200dma < -20:
        flags.append(f"📉 {f.pct_from_200dma:.0f}% below 200-DMA — downtrend")

    # Forward/trailing P/E comparison (not a revision series)
    if (
        f.forward_to_trailing_pe_ratio is not None
        and f.forward_to_trailing_pe_ratio > 1.3
    ):
        flags.append(
            "⚠ Forward/trailing P/E ratio "
            f"{f.forward_to_trailing_pe_ratio:.1f}x — not a direct "
            "earnings-revision measure"
        )

    # Historical-price/current-EPS proxy (not a historical multiple series)
    proxy = f.forward_pe_vs_price_history_proxy
    if proxy is not None:
        if proxy < 70:
            flags.append(
                f"🏷️ Forward P/E is {proxy:.0f}% of the price/current-EPS "
                "proxy — not historical P/E"
            )
        elif proxy > 130:
            flags.append(
                f"💰 Forward P/E is {proxy:.0f}% of the price/current-EPS "
                "proxy — not historical P/E"
            )

    return flags


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _cache_path(ticker: str, *, cache_dir: Path = CACHE_DIR) -> Path:
    safe = ticker.replace("/", "_").replace(".", "_")
    return cache_dir / f"{safe}.json"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _has_market_data(fundamentals: Fundamentals) -> bool:
    values = fundamentals.model_dump(
        exclude={
            "ticker",
            "provider_symbol",
            "name",
            "quote_type",
            "source",
            "sector",
            "fetched_at",
        },
        exclude_none=True,
    )
    return any(value not in ([], {}) for value in values.values())


def _refresh_failure(
    ticker: str,
    *,
    now: datetime,
    cached: Fundamentals | None,
    status: MarketDataStatus,
    reason: str,
) -> FundamentalsResult:
    if cached and cached.fetched_at:
        as_of = _as_utc(cached.fetched_at)
        return FundamentalsResult(
            ticker=ticker,
            status=MarketDataStatus.STALE,
            data=cached,
            observed_at=now,
            as_of=as_of,
            from_cache=True,
            age=max((now - as_of).total_seconds(), 0.0),
            reason=reason,
        )
    return FundamentalsResult(
        ticker=ticker,
        status=status,
        observed_at=now,
        reason=reason,
    )


def _load_cache(ticker: str, *, cache_dir: Path = CACHE_DIR) -> Optional[Fundamentals]:
    path = _cache_path(ticker, cache_dir=cache_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return Fundamentals(**data)
    except Exception:
        return None


def _save_cache(ticker: str, f: Fundamentals, *, cache_dir: Path = CACHE_DIR) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(ticker, cache_dir=cache_dir)
    path.write_text(f.model_dump_json(indent=2))
