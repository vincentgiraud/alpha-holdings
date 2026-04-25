"""ETF mapping: find thematic ETFs for each discovered theme."""

from __future__ import annotations

import json
import logging
from typing import Optional

import yfinance as yf

from alpha_holdings import llm
from alpha_holdings.models import ETFRecommendation, ETFRecommendationType, ThemeThesis
from alpha_holdings.signals import _extract_json

log = logging.getLogger(__name__)

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


def find_etf(theme: ThemeThesis) -> ETFRecommendation:
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

    if not candidates:
        return ETFRecommendation(
            theme_name=theme.name,
            recommendation=ETFRecommendationType.NO_GOOD_ETF,
            reasoning="No thematic ETF adequately captures this theme.",
        )

    # Evaluate top candidate
    best = candidates[0]
    etf_ticker = best.get("etf_ticker", "")
    etf_name = best.get("etf_name", "")

    info = _fetch_etf_info(etf_ticker)
    if not info:
        return ETFRecommendation(
            theme_name=theme.name,
            etf_ticker=etf_ticker,
            etf_name=etf_name,
            recommendation=ETFRecommendationType.ETF_SUFFICIENT,
            reasoning=best.get("reasoning", "Suggested by analysis."),
        )

    # Check holdings overlap with theme companies
    theme_tickers = {c.ticker.upper() for c in theme.all_companies}
    etf_holdings = info.get("holdings", set())
    overlap = len(theme_tickers & etf_holdings)
    overlap_pct = overlap / max(len(theme_tickers), 1) * 100

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

    return ETFRecommendation(
        theme_name=theme.name,
        etf_ticker=etf_ticker,
        etf_name=etf_name,
        expense_ratio=info.get("expense_ratio"),
        aum=info.get("aum"),
        overlap_pct=round(overlap_pct, 1),
        recommendation=rec_type,
        reasoning=reasoning,
    )


def _fetch_etf_info(ticker: str) -> Optional[dict]:
    """Fetch basic ETF info and top holdings from yfinance."""
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        holdings: set[str] = set()

        # Use funds_data.top_holdings — returns a DataFrame with ticker symbols
        try:
            top_holdings = t.funds_data.top_holdings
            if top_holdings is not None and not top_holdings.empty:
                for sym in top_holdings.index:
                    if isinstance(sym, str) and sym.strip():
                        holdings.add(sym.strip().upper())
        except Exception:
            pass

        # Fallback: try the holdings property directly
        if not holdings:
            try:
                h = t.get_holdings()
                if h is not None and not h.empty:
                    for sym in h.index:
                        if isinstance(sym, str) and sym.strip():
                            holdings.add(sym.strip().upper())
            except Exception:
                pass

        return {
            "expense_ratio": info.get("annualReportExpenseRatio"),
            "aum": info.get("totalAssets"),
            "holdings": holdings,
        }
    except Exception as exc:
        log.debug("Failed to fetch ETF info for %s: %s", ticker, exc)
        return None
