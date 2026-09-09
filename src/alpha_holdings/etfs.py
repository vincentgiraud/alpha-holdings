"""ETF mapping: find thematic ETFs for each discovered theme."""

from __future__ import annotations

import json
import logging
import math
from datetime import UTC, datetime
from typing import Callable, Protocol

import yfinance as yf
from alpha_holdings import llm
from alpha_holdings.models import (
    ETFCandidateEvaluation,
    ETFMarketEvidence,
    ETFRecommendation,
    ETFRecommendationType,
    FundamentalsResult,
    MarketDataStatus,
    ThemeThesis,
)
from alpha_holdings.signals import _extract_json

log = logging.getLogger(__name__)


class ETFDataProvider(Protocol):
    def fetch(self, ticker: str) -> ETFMarketEvidence: ...


class YahooETFProvider:
    def fetch(self, ticker: str) -> ETFMarketEvidence:
        return _fetch_yahoo_evidence(ticker)


DEFAULT_PROVIDER: ETFDataProvider = YahooETFProvider()


def fetch_validated_etf(
    ticker: str,
    *,
    provider: ETFDataProvider | None = None,
    clock: Callable[[], datetime] | None = None,
) -> FundamentalsResult:
    """Fetch investability evidence and return adjusted-close allocation data."""
    normalized_ticker = ticker.strip().upper()
    observed_at = (clock or (lambda: datetime.now(UTC)))()
    try:
        evidence = (provider or DEFAULT_PROVIDER).fetch(normalized_ticker)
    except Exception as exc:
        return FundamentalsResult(
            ticker=normalized_ticker,
            status=MarketDataStatus.UNAVAILABLE,
            observed_at=observed_at,
            reason=f"ETF provider unavailable: {exc}",
        )
    reasons = _validation_reasons(normalized_ticker, evidence)
    if reasons:
        return FundamentalsResult(
            ticker=normalized_ticker,
            status=MarketDataStatus.INVALID,
            observed_at=observed_at,
            reason="; ".join(reasons),
        )
    evaluation = ETFCandidateEvaluation(
        ticker=normalized_ticker,
        is_valid=True,
        evidence=evidence,
    )
    return evaluation.to_market_data(observed_at=observed_at)

_ETF_DISCOVERY_PROMPT = """\
For the investment theme "{theme_name}" ({thesis_summary}), \
identify the 1-3 most relevant thematic ETFs that provide exposure to this theme.

For each ETF, provide:
- "etf_ticker": ticker symbol
- "etf_name": fund name
- "reasoning": why this ETF is relevant

If no good thematic ETF exists for this theme, return an empty array.

Return ONLY a JSON array. No commentary.
"""


def find_etf(
    theme: ThemeThesis,
    *,
    provider: ETFDataProvider | None = None,
) -> ETFRecommendation:
    """Find the best ETF for a theme and assess ETF vs individual stocks."""
    prompt = _ETF_DISCOVERY_PROMPT.format(
        theme_name=theme.name,
        thesis_summary=theme.thesis_summary,
    )

    try:
        raw = llm.respond_text(prompt, mini=True, web_search=True)
        candidates = json.loads(_extract_json(raw))
    except Exception:
        log.warning("ETF discovery failed for theme: %s", theme.name)
        return ETFRecommendation(
            theme_name=theme.name,
            recommendation=ETFRecommendationType.NO_GOOD_ETF,
            reasoning="Unable to identify a suitable ETF.",
        )

    if not isinstance(candidates, list) or not candidates:
        return ETFRecommendation(
            theme_name=theme.name,
            recommendation=ETFRecommendationType.NO_GOOD_ETF,
            reasoning="ETF discovery returned no valid candidate list.",
        )

    data_provider = provider or DEFAULT_PROVIDER
    theme_tickers = {company.full_ticker.upper() for company in theme.all_companies}
    evaluated: list[tuple[dict, ETFMarketEvidence, ETFCandidateEvaluation, float]] = []
    audit: list[ETFCandidateEvaluation] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            audit.append(
                ETFCandidateEvaluation(
                    ticker="UNKNOWN",
                    is_valid=False,
                    rejection_reasons=["candidate payload is not an object"],
                )
            )
            continue
        ticker = str(candidate.get("etf_ticker", "")).strip().upper()
        if not ticker:
            audit.append(
                ETFCandidateEvaluation(
                    ticker="UNKNOWN",
                    is_valid=False,
                    rejection_reasons=["candidate did not provide a ticker"],
                )
            )
            continue
        try:
            evidence = data_provider.fetch(ticker)
        except Exception as exc:
            audit.append(
                ETFCandidateEvaluation(
                    ticker=ticker,
                    is_valid=False,
                    rejection_reasons=[f"provider unavailable: {exc}"],
                )
            )
            continue
        reasons = _validation_reasons(ticker, evidence)
        holdings_coverage_pct = round(sum(evidence.holdings.values()), 4)
        unknown_weight_pct = round(max(100 - holdings_coverage_pct, 0), 4)
        overlap = len(theme_tickers & set(evidence.holdings))
        overlap_pct = overlap / max(len(theme_tickers), 1) * 100
        evaluation = ETFCandidateEvaluation(
            ticker=ticker,
            is_valid=not reasons,
            rejection_reasons=reasons,
            evidence=evidence,
            holdings_coverage_pct=holdings_coverage_pct,
            unknown_weight_pct=unknown_weight_pct,
            theme_coverage_pct=round(overlap_pct, 1),
        )
        audit.append(evaluation)
        if reasons:
            continue
        evaluated.append((candidate, evidence, evaluation, overlap_pct))

    if not evaluated:
        return ETFRecommendation(
            theme_name=theme.name,
            recommendation=ETFRecommendationType.NO_GOOD_ETF,
            reasoning="No ETF candidate passed investability validation.",
            candidate_evaluations=audit,
        )

    best, evidence, _evaluation, overlap_pct = max(
        evaluated,
        key=lambda item: (
            item[3],
            item[1].total_assets or 0,
            -(item[1].expense_ratio or 0),
        ),
    )
    etf_ticker = evidence.ticker.strip().upper()
    etf_name = evidence.name or best.get("etf_name", "")

    # Decision logic
    if overlap_pct >= 50:
        rec_type = ETFRecommendationType.ETF_SUFFICIENT
        reasoning = f"ETF covers {overlap_pct:.0f}% of theme companies. Good for broad exposure."
    elif overlap_pct >= 20:
        rec_type = ETFRecommendationType.STOCKS_BETTER
        reasoning = (
            f"ETF only covers {overlap_pct:.0f}% of theme companies. "
            "Individual Tier 2-3 picks offer better positioning."
        )
    else:
        rec_type = ETFRecommendationType.NO_GOOD_ETF
        reasoning = f"ETF has minimal overlap ({overlap_pct:.0f}%) with theme companies."

    holdings_coverage_pct = round(sum(evidence.holdings.values()), 4)
    unknown_weight_pct = round(max(100 - holdings_coverage_pct, 0), 4)
    reported_holdings = dict(evidence.holdings)
    if unknown_weight_pct:
        reported_holdings["UNKNOWN/OTHER"] = unknown_weight_pct

    return ETFRecommendation(
        theme_name=theme.name,
        etf_ticker=etf_ticker,
        etf_name=etf_name,
        expense_ratio=evidence.expense_ratio,
        aum=evidence.total_assets,
        overlap_pct=round(overlap_pct, 1),
        holdings=reported_holdings,
        holdings_coverage_pct=holdings_coverage_pct,
        unknown_weight_pct=unknown_weight_pct,
        selected_evidence=evidence,
        candidate_evaluations=audit,
        recommendation=rec_type,
        reasoning=reasoning,
    )


def _validation_reasons(
    requested_ticker: str,
    evidence: ETFMarketEvidence,
) -> list[str]:
    return evidence.investability_reasons(requested_ticker)


def _fetch_yahoo_evidence(ticker: str) -> ETFMarketEvidence:
    """Fetch one ETF observation from Yahoo Finance."""
    instrument = yf.Ticker(ticker)
    info = instrument.info or {}
    holdings: dict[str, float] = {}
    top_holdings = instrument.funds_data.top_holdings
    if top_holdings is not None and not top_holdings.empty:
        holdings = _parse_holdings(top_holdings)
    history = instrument.history(period="5d", auto_adjust=False)
    adjusted_close = None
    price_as_of = None
    if history is not None and not history.empty and "Adj Close" in history.columns:
        series = history["Adj Close"].dropna()
        if not series.empty:
            adjusted_close = float(series.iloc[-1])
            observed = series.index[-1]
            price_as_of = observed.to_pydatetime() if hasattr(observed, "to_pydatetime") else observed
    return ETFMarketEvidence(
        ticker=ticker,
        provider_symbol=info.get("symbol"),
        name=info.get("shortName") or info.get("longName"),
        quote_type=info.get("quoteType"),
        average_daily_volume=info.get("averageDailyVolume10Day")
        or info.get("averageVolume"),
        total_assets=info.get("totalAssets"),
        expense_ratio=info.get("annualReportExpenseRatio"),
        adjusted_close=adjusted_close,
        price_as_of=price_as_of,
        source="yfinance",
        holdings=holdings,
    )


def _parse_holdings(table) -> dict[str, float]:
    weight_formats = {
        "Holding Percent": "fraction",
        "% Of Net Assets": "percentage",
        "pctNetAssets": "percentage",
    }
    weight_column = next(
        (
            column
            for column in weight_formats
            if column in table.columns
        ),
        None,
    )
    if weight_column is None:
        return {}
    raw: list[tuple[str, float]] = []
    for index, row in table.iterrows():
        symbol = row.get("Symbol") or row.get("symbol") or index
        value = row.get(weight_column)
        if not isinstance(symbol, str) or not symbol.strip():
            continue
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            continue
        weight = float(value)
        if weight <= 0:
            continue
        raw.append((symbol.strip().upper(), weight))
    if not raw:
        return {}
    multiplier = 100 if weight_formats[weight_column] == "fraction" else 1
    normalized: dict[str, float] = {}
    for symbol, weight in raw:
        normalized[symbol] = round(
            normalized.get(symbol, 0) + weight * multiplier,
            4,
        )
    return normalized
