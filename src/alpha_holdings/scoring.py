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
    t_score, p_score, rev_exposure, reasonings = _combined_llm_scores(
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
        alignment_reasoning=reasonings.get("alignment"),
        pricing_gap_reasoning=reasonings.get("pricing_gap"),
        revenue_exposure_reasoning=reasonings.get("revenue_exposure"),
    )


def detect_opportunity(
    ticker: str,
    theme_confidence: int,
    fundamentals: Fundamentals,
    *,
    theme_name: str | None = None,
    supply_chain_tier: str | None = None,
) -> Optional[OpportunitySignal]:
    """Detect entry opportunities: on-sale, stabilized, or recovering."""
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

    # Volume ratio placeholder (enhanced in batch 2)
    volume_ratio = None

    drawdown = fundamentals.drawdown_from_peak
    common = dict(
        ticker=ticker, thesis_confidence=theme_confidence,
        fundamental_health=health_summary, current_price=fundamentals.current_price,
        drawdown_pct=drawdown, theme_name=theme_name,
        supply_chain_tier=supply_chain_tier, volume_vs_avg=volume_ratio,
    )

    # AVOID: thesis weak + fundamentals bad
    if theme_confidence < 7 and not fundamentals_intact:
        return OpportunitySignal(
            signal_type=OpportunityType.AVOID,
            recommended_action="Thesis weak and fundamentals deteriorating.",
            **common,
        )

    # CAUTION: dipped but fundamentals weakening
    if drawdown is not None and drawdown < -10 and theme_confidence >= 7 and not fundamentals_intact:
        return OpportunitySignal(
            signal_type=OpportunityType.CAUTION,
            recommended_action="Price dropped but some fundamental concerns — possible early warning.",
            **common,
        )

    # ON SALE: significant dip with thesis + fundamentals intact
    if drawdown is not None and drawdown < -10 and theme_confidence >= 7 and fundamentals_intact:
        return OpportunitySignal(
            signal_type=OpportunityType.ON_SALE,
            recommended_action=f"Discounted {drawdown:.0f}% from peak — thesis and fundamentals intact. Lump sum candidate.",
            **common,
        )

    # No significant dip — check for STABILIZED or RECOVERING
    if drawdown is not None and drawdown < -15 and theme_confidence >= 7 and fundamentals_intact:
        # Check stabilization: was down >15%, now trading in tight range for 30+ days
        stabilized = _check_stabilized(ticker)
        if stabilized:
            return OpportunitySignal(
                signal_type=OpportunityType.STABILIZED,
                recommended_action=f"Down {drawdown:.0f}% from peak but price stabilized — selling pressure exhausted. Good DCA entry.",
                **common,
            )

        # Check recovery: was down >15%, now bouncing up >10% from recent low
        recovering = _check_recovering(ticker)
        if recovering:
            return OpportunitySignal(
                signal_type=OpportunityType.RECOVERING,
                recommended_action=f"Down {drawdown:.0f}% from peak but trend reversing — momentum shifting positive.",
                **common,
            )

    return None


def _check_stabilized(ticker: str) -> bool:
    """Check if a stock has stabilized: trading in a <5% range for 30+ days."""
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="3mo")
        if hist is None or len(hist) < 30:
            return False
        last_30 = hist["Close"].tail(30)
        price_range = (last_30.max() - last_30.min()) / last_30.mean() * 100
        return price_range < 8  # less than 8% range = stabilized
    except Exception:
        return False


def _check_recovering(ticker: str) -> bool:
    """Check if a stock is recovering: up >10% from recent 3-month low."""
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="3mo")
        if hist is None or len(hist) < 20:
            return False
        recent_low = hist["Close"].min()
        current = hist["Close"].iloc[-1]
        if recent_low and recent_low > 0:
            recovery_pct = (current / recent_low - 1) * 100
            # Also check it's above 20-day moving average
            ma_20 = hist["Close"].tail(20).mean()
            return recovery_pct > 10 and current > ma_20
    except Exception:
        pass
    return False


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
) -> tuple[float, float, int, dict[str, str]]:
    """Get thesis alignment + pricing gap + revenue exposure in a single LLM call.
    
    Returns (alignment_score, pricing_gap_score, revenue_exposure_pct, reasonings).
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
        reasonings = {
            "alignment": data.get("alignment_reasoning", ""),
            "pricing_gap": data.get("pricing_gap_reasoning", ""),
            "revenue_exposure": data.get("revenue_exposure_reasoning", ""),
        }
        return alignment, pricing_gap, rev_exposure, reasonings
    except Exception as exc:
        log.warning("Combined scoring failed for %s: %s", company.ticker, exc)
        return 50.0, 50.0, 50, {}


def _pricing_gap_score(
    company: Company,
    theme: ThemeThesis,
    fundamentals: Fundamentals,
) -> float:
    """Standalone pricing gap score (delegates to combined call)."""
    _, gap, _, _ = _combined_llm_scores(company, theme, fundamentals)
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
