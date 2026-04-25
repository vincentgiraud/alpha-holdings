"""Scoring engine: fundamental + thesis alignment + pricing gap."""

from __future__ import annotations

import json
import logging
from typing import Optional

from alpha_holdings import llm
from alpha_holdings.config import SCORING_WEIGHTS
from alpha_holdings.models import (
    Company,
    EntryMethod,
    Fundamentals,
    OpportunitySignal,
    OpportunityType,
    ThemeScore,
    ThemeThesis,
    ValuationContext,
    ValuationLevel,
)
from alpha_holdings.prompts.thesis_validation import PRICING_GAP_PROMPT, THESIS_ALIGNMENT_PROMPT
from alpha_holdings.signals import _extract_json

log = logging.getLogger(__name__)

_COMBINED_SCORING_PROMPT = """\
You are an investment analyst evaluating a company for a specific investment theme.

THEME: {theme_name}
THESIS: {thesis_summary}
WHY NOW: {why_now}

COMPANY: {company_name} ({ticker})
ROLE IN THEME: {role_in_theme}
SUPPLY CHAIN TIER: {tier}
SECTOR: {sector}

FUNDAMENTALS:
{fundamentals_summary}

VALUATION:
- Forward P/E: {forward_pe}
- EV/EBITDA: {ev_to_ebitda}
- Sector median forward P/E: {sector_median_pe}

Evaluate THREE dimensions:

1. THESIS ALIGNMENT (0-100): How well positioned is this company to benefit from the theme over 5 years? \
Consider: catalyst proximity, competitive moat, management execution, regulatory environment.

2. PRICING GAP (0-100): Has the market priced in this company's theme exposure? \
100 = massive unrecognized exposure (still valued as a boring {sector} company). \
0 = fully repriced as a theme play (premium already baked in). \
Compare its forward P/E ({forward_pe}) to the sector median ({sector_median_pe}). \
Tier 3 "picks-and-shovels" companies often have large pricing gaps.

3. REVENUE EXPOSURE (0-100): Estimate what percentage of this company's revenue is \
directly or indirectly tied to the theme "{theme_name}". \
0 = negligible exposure, company is a conglomerate with tiny theme-related revenue. \
100 = pure-play, nearly all revenue is theme-driven. \
Search the web if needed to validate.

Return a JSON object with:
- "alignment_score": 0-100
- "pricing_gap_score": 0-100
- "revenue_exposure": 0-100
- "alignment_reasoning": one sentence
- "pricing_gap_reasoning": one sentence
- "revenue_exposure_reasoning": one sentence

Return ONLY valid JSON.
"""


def compute_sector_median_pe(
    all_fundamentals: dict[str, Fundamentals],
    sector: str,
) -> str:
    """Compute the median forward P/E for a sector from available data."""
    pes = [
        f.forward_pe
        for f in all_fundamentals.values()
        if f.sector and f.sector.lower() == sector.lower()
        and f.forward_pe is not None
        and 0 < f.forward_pe < 200  # filter outliers
    ]
    if len(pes) >= 3:
        pes.sort()
        median = pes[len(pes) // 2]
        return f"{median:.1f}"
    return "~18 (insufficient peer data)"


def score_company(
    company: Company,
    theme: ThemeThesis,
    fundamentals: Fundamentals,
    *,
    all_fundamentals: dict[str, Fundamentals] | None = None,
) -> ThemeScore:
    """Score a company on all three dimensions (1 LLM call for thesis+pricing+revenue)."""
    f_score = _fundamental_score(fundamentals)
    sector_median = "~18"
    if all_fundamentals:
        sector_median = compute_sector_median_pe(all_fundamentals, company.sector)
    t_score, p_score, rev_exposure = _combined_llm_scores(
        company, theme, fundamentals, sector_median
    )

    # Penalize low revenue exposure — a conglomerate with 5% theme revenue
    # shouldn't score as high as a pure-play
    if rev_exposure < 20:
        t_score *= 0.5  # halve thesis alignment for marginal exposure
        log.info(
            "%s: low revenue exposure (%d%%) — thesis alignment penalized",
            company.ticker, rev_exposure,
        )

    w = SCORING_WEIGHTS
    composite = (
        f_score * w["fundamental"]
        + t_score * w["thesis_alignment"]
        + p_score * w["pricing_gap"]
    )

    valuation = _assess_valuation(fundamentals)
    entry = _determine_entry(valuation, theme.confidence_score, fundamentals)

    return ThemeScore(
        ticker=fundamentals.ticker,
        fundamental_score=round(f_score, 1),
        thesis_alignment_score=round(t_score, 1),
        pricing_gap_score=round(p_score, 1),
        composite_score=round(composite, 1),
        valuation=valuation,
        entry_method=entry,
    )


def detect_opportunity(
    ticker: str,
    theme_confidence: int,
    fundamentals: Fundamentals,
) -> Optional[OpportunitySignal]:
    """Check if a price drop represents a buy-the-dip opportunity."""
    if fundamentals.drawdown_from_peak is None or fundamentals.drawdown_from_peak > -10:
        return None  # not a significant dip

    # Assess fundamental health
    health_issues = []
    if fundamentals.revenue_growth_3yr_cagr is not None and fundamentals.revenue_growth_3yr_cagr < 0:
        health_issues.append("negative revenue growth")
    if fundamentals.operating_margin is not None and fundamentals.operating_margin < 0:
        health_issues.append("negative operating margin")
    if fundamentals.debt_to_equity is not None and fundamentals.debt_to_equity > 200:
        health_issues.append("high debt")

    fundamentals_intact = len(health_issues) == 0
    health_summary = "Fundamentals intact" if fundamentals_intact else f"Issues: {', '.join(health_issues)}"

    if theme_confidence >= 7 and fundamentals_intact:
        signal_type = OpportunityType.BUY_THE_DIP
        action = "Strong lump sum candidate — price dropped but thesis and fundamentals intact."
    elif theme_confidence >= 7 and not fundamentals_intact:
        signal_type = OpportunityType.CAUTION
        action = "Price dropped and some fundamental concerns. Possible early warning, not a clear bargain."
    else:
        signal_type = OpportunityType.AVOID
        action = "Thesis weakening and/or fundamentals deteriorating. Price may be right for a reason."

    return OpportunitySignal(
        ticker=ticker,
        signal_type=signal_type,
        thesis_confidence=theme_confidence,
        fundamental_health=health_summary,
        current_price=fundamentals.current_price,
        drawdown_pct=fundamentals.drawdown_from_peak,
        recommended_action=action,
    )


# ---------------------------------------------------------------------------
# Internal scoring helpers
# ---------------------------------------------------------------------------

def _fundamental_score(f: Fundamentals) -> float:
    """Score 0-100 based on available fundamentals."""
    scores: list[float] = []

    # Growth
    if f.revenue_growth_3yr_cagr is not None:
        scores.append(min(100, max(0, f.revenue_growth_3yr_cagr * 3)))
    if f.roe is not None:
        scores.append(min(100, max(0, f.roe * 3)))

    # Quality
    if f.gross_margin is not None:
        scores.append(min(100, max(0, f.gross_margin * 1.5)))
    if f.operating_margin is not None:
        scores.append(min(100, max(0, (f.operating_margin + 10) * 2.5)))
    if f.fcf_yield is not None:
        scores.append(min(100, max(0, f.fcf_yield * 10)))

    # Value (inverted — lower is better)
    if f.forward_pe is not None and f.forward_pe > 0:
        scores.append(max(0, min(100, 100 - f.forward_pe * 1.5)))
    if f.peg_ratio is not None and f.peg_ratio > 0:
        scores.append(max(0, min(100, 100 - f.peg_ratio * 30)))

    # Balance sheet
    if f.debt_to_equity is not None:
        scores.append(max(0, min(100, 100 - f.debt_to_equity * 0.3)))

    if not scores:
        return 50.0  # default when no data
    return sum(scores) / len(scores)


def _combined_llm_scores(
    company: Company,
    theme: ThemeThesis,
    fundamentals: Fundamentals,
    sector_median_pe: str = "~18",
) -> tuple[float, float, int]:
    """Get thesis alignment + pricing gap + revenue exposure in a single LLM call.
    
    Returns (alignment_score, pricing_gap_score, revenue_exposure_pct).
    """
    f_summary = _fundamentals_summary(fundamentals)
    prompt = _COMBINED_SCORING_PROMPT.format(
        theme_name=theme.name,
        thesis_summary=theme.thesis_summary,
        why_now=theme.why_now,
        company_name=company.name,
        ticker=fundamentals.ticker,
        role_in_theme=company.role_in_theme,
        tier=company.supply_chain_tier.value,
        sector=company.sector,
        fundamentals_summary=f_summary,
        forward_pe=fundamentals.forward_pe or "N/A",
        ev_to_ebitda=fundamentals.ev_to_ebitda or "N/A",
        sector_median_pe=sector_median_pe,
    )

    try:
        raw = llm.respond_text(prompt, mini=True)
        data = json.loads(_extract_json(raw))
        alignment = float(data.get("alignment_score", 50))
        pricing_gap = float(data.get("pricing_gap_score", 50))
        rev_exposure = int(data.get("revenue_exposure", 50))
        return alignment, pricing_gap, rev_exposure
    except Exception as exc:
        log.warning("Combined scoring failed for %s: %s", company.ticker, exc)
        return 50.0, 50.0, 50


def _pricing_gap_score(
    company: Company,
    theme: ThemeThesis,
    fundamentals: Fundamentals,
) -> float:
    """Standalone pricing gap score (delegates to combined call)."""
    _, gap, _ = _combined_llm_scores(company, theme, fundamentals)
    return gap


def _assess_valuation(f: Fundamentals) -> ValuationContext:
    """Heuristic valuation assessment."""
    if f.forward_pe is None:
        return ValuationContext(level=ValuationLevel.FAIR, summary="Insufficient data for valuation.")

    if f.peg_ratio is not None and f.peg_ratio < 1:
        level = ValuationLevel.CHEAP
    elif f.forward_pe < 15:
        level = ValuationLevel.CHEAP
    elif f.forward_pe > 35:
        level = ValuationLevel.EXPENSIVE
    else:
        level = ValuationLevel.FAIR

    pe_str = f"Forward P/E: {f.forward_pe:.1f}" if f.forward_pe else ""
    peg_str = f", PEG: {f.peg_ratio:.2f}" if f.peg_ratio else ""
    return ValuationContext(
        level=level,
        forward_pe_vs_sp500=pe_str,
        summary=f"{level.value.capitalize()} — {pe_str}{peg_str}",
    )


def _determine_entry(
    valuation: ValuationContext,
    confidence: int,
    f: Fundamentals,
) -> EntryMethod:
    if valuation.level == ValuationLevel.CHEAP and confidence >= 7:
        return EntryMethod.LUMP_SUM
    if valuation.level == ValuationLevel.EXPENSIVE:
        return EntryMethod.WAIT if confidence < 8 else EntryMethod.DCA
    return EntryMethod.DCA


def _fundamentals_summary(f: Fundamentals) -> str:
    """Format fundamentals into a readable summary for LLM prompts."""
    lines = []
    if f.market_cap:
        lines.append(f"Market Cap: ${f.market_cap / 1e9:.1f}B")
    if f.revenue_growth_3yr_cagr is not None:
        lines.append(f"Revenue Growth (3yr CAGR): {f.revenue_growth_3yr_cagr:.1f}%")
    if f.gross_margin is not None:
        lines.append(f"Gross Margin: {f.gross_margin:.1f}%")
    if f.operating_margin is not None:
        lines.append(f"Operating Margin: {f.operating_margin:.1f}%")
    if f.forward_pe is not None:
        lines.append(f"Forward P/E: {f.forward_pe:.1f}")
    if f.peg_ratio is not None:
        lines.append(f"PEG Ratio: {f.peg_ratio:.2f}")
    if f.debt_to_equity is not None:
        lines.append(f"Debt/Equity: {f.debt_to_equity:.1f}")
    if f.roe is not None:
        lines.append(f"ROE: {f.roe:.1f}%")
    if f.fcf_yield is not None:
        lines.append(f"FCF Yield: {f.fcf_yield:.1f}%")
    if f.drawdown_from_peak is not None:
        lines.append(f"Drawdown from 52wk high: {f.drawdown_from_peak:.1f}%")
    return "\n".join(lines) if lines else "No fundamental data available."
