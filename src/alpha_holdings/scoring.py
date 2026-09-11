"""Scoring engine: fundamental + thesis alignment + pricing gap."""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Callable, Optional, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from alpha_holdings import llm
from alpha_holdings.config import SCORING_WEIGHTS
from alpha_holdings.models import (
    Company,
    EntryMethod,
    Fundamentals,
    FundamentalsResult,
    OpportunitySignal,
    OpportunityType,
    ThemeScore,
    ThemeThesis,
    ThesisStatus,
    ValuationContext,
    ValuationLevel,
)
from alpha_holdings.prompts.thesis_validation import PRICING_GAP_PROMPT, THESIS_ALIGNMENT_PROMPT
from alpha_holdings.signals import _extract_json

log = logging.getLogger(__name__)

FUNDAMENTAL_METRIC_WEIGHTS = {
    "revenue_growth_cagr": 0.15,
    "roe": 0.10,
    "gross_margin": 0.10,
    "operating_margin": 0.15,
    "fcf_yield": 0.15,
    "forward_pe": 0.15,
    "peg_ratio": 0.10,
    "debt_to_equity": 0.10,
}
MISSING_METRIC_SCORE = 50.0
AI_VALIDATION_ATTEMPTS = 2


class ScoringValidationError(ValueError):
    """Raised when AI scoring cannot produce a validated assessment."""


class CandidateRejectedError(ValueError):
    """Raised when a candidate fails data-quality or identity validation."""


@dataclass
class CandidateScoringResult:
    """Validated themes, complete scores, and explicit candidate rejections."""

    themes: list[ThemeThesis]
    scores: dict[str, list[ThemeScore]] = field(default_factory=dict)
    rejections: dict[str, str] = field(default_factory=dict)


class ScoringProvider(Protocol):
    name: str
    model: str

    def score(self, prompt: str) -> str: ...


class CodexScoringProvider:
    name = "codex-cli"

    @property
    def model(self) -> str:
        return llm.get_model(mini=True)

    def score(self, prompt: str) -> str:
        return llm.respond_text(prompt, mini=True, web_search=True)


class _AIScorePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: str
    alignment_score: float = Field(ge=0, le=100)
    pricing_gap_score: float = Field(ge=0, le=100)
    revenue_exposure: float = Field(ge=0, le=100)
    alignment_reasoning: str = Field(min_length=1)
    pricing_gap_reasoning: str = Field(min_length=1)
    revenue_exposure_reasoning: str = Field(min_length=1)
    sources: list[str] = Field(min_length=1)

    @field_validator(
        "ticker",
        "alignment_reasoning",
        "pricing_gap_reasoning",
        "revenue_exposure_reasoning",
    )
    @classmethod
    def _strip_non_empty_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("sources")
    @classmethod
    def _validate_sources(cls, values: list[str]) -> list[str]:
        sources = [value.strip() for value in values if value.strip()]
        if not sources or any(
            not source.startswith(("https://", "http://")) for source in sources
        ):
            raise ValueError("sources must contain HTTP(S) evidence URLs")
        return sources


def score_candidates(
    themes: list[ThemeThesis],
    market_data: dict[str, FundamentalsResult],
    *,
    provider: ScoringProvider | None = None,
    clock: Callable[[], datetime] | None = None,
) -> CandidateScoringResult:
    """Validate and score every eligible candidate without silent fallbacks."""
    from alpha_holdings.fundamentals import passes_quality_filter

    validated_themes = [theme.model_copy(deep=True) for theme in themes]
    all_fundamentals = {
        ticker: result.data
        for ticker, result in market_data.items()
        if result.data is not None
    }
    result = CandidateScoringResult(themes=[])
    for theme in validated_themes:
        theme_scores: list[ThemeScore] = []
        for sub_theme in theme.sub_themes:
            accepted: list[Company] = []
            for company in sub_theme.companies:
                ticker = company.full_ticker
                observation = market_data.get(ticker)
                if observation is None:
                    result.rejections[ticker] = "Market data unavailable: no observation"
                    continue
                passes, reason = passes_quality_filter(observation)
                if not passes or observation.data is None:
                    result.rejections[ticker] = reason
                    continue
                try:
                    score = score_company(
                        company,
                        theme,
                        observation.data,
                        all_fundamentals=all_fundamentals,
                        provider=provider,
                        clock=clock,
                    )
                except (CandidateRejectedError, ScoringValidationError) as exc:
                    result.rejections[ticker] = str(exc)
                    continue
                accepted.append(company)
                theme_scores.append(score)
            sub_theme.companies = accepted
        theme.sub_themes = [
            sub_theme for sub_theme in theme.sub_themes if sub_theme.companies
        ]
        if theme_scores:
            result.themes.append(theme)
            result.scores[theme.name] = theme_scores
    return result

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
- "ticker": exactly "{ticker}"
- "alignment_score": 0-100
- "pricing_gap_score": 0-100
- "revenue_exposure": 0-100
- "alignment_reasoning": one sentence
- "pricing_gap_reasoning": one sentence
- "revenue_exposure_reasoning": one sentence
- "sources": one or more HTTP(S) evidence URLs supporting the assessment

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
    provider: ScoringProvider | None = None,
    clock: Callable[[], datetime] | None = None,
) -> ThemeScore:
    """Score a company on all three dimensions (1 LLM call for thesis+pricing+revenue)."""
    from alpha_holdings.fundamentals import (
        passes_quality_filter,
        validate_company_identity,
    )

    passes, reason = passes_quality_filter(fundamentals)
    if not passes:
        raise CandidateRejectedError(reason)
    matches, reason = validate_company_identity(company, fundamentals)
    if not matches:
        raise CandidateRejectedError(reason)

    f_score = score_fundamentals(fundamentals)
    sector_median = "~18"
    if all_fundamentals:
        sector_median = compute_sector_median_pe(all_fundamentals, company.sector)
    payload, scoring_provider = _combined_llm_scores(
        company,
        theme,
        fundamentals,
        sector_median,
        provider=provider,
    )
    provider_name = scoring_provider.name
    provider_model = scoring_provider.model
    if (
        not isinstance(provider_name, str)
        or not provider_name.strip()
        or not isinstance(provider_model, str)
        or not provider_model.strip()
    ):
        raise ScoringValidationError("Scoring provider provenance is missing")
    t_score = payload.alignment_score
    p_score = payload.pricing_gap_score
    rev_exposure = payload.revenue_exposure

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

    valuation = assess_valuation(fundamentals)
    entry = _determine_entry(valuation, theme.confidence_score, fundamentals)

    return ThemeScore(
        ticker=fundamentals.ticker,
        fundamental_score=round(f_score, 1),
        thesis_alignment_score=round(t_score, 1),
        pricing_gap_score=round(p_score, 1),
        revenue_exposure_score=round(rev_exposure, 1),
        composite_score=round(composite, 1),
        valuation=valuation,
        entry_method=entry,
        alignment_reasoning=payload.alignment_reasoning,
        pricing_gap_reasoning=payload.pricing_gap_reasoning,
        revenue_exposure_reasoning=payload.revenue_exposure_reasoning,
        score_as_of=(clock or (lambda: datetime.now(UTC)))(),
        evidence_sources=payload.sources,
        scoring_provider=provider_name.strip(),
        scoring_model=provider_model.strip(),
    )


def detect_opportunity(
    ticker: str,
    theme_confidence: int,
    fundamentals: Fundamentals,
    *,
    theme_name: str | None = None,
    supply_chain_tier: str | None = None,
    thesis_status: ThesisStatus = ThesisStatus.UNCHANGED,
) -> Optional[OpportunitySignal]:
    """Classify an entry observation without allowing invalidated themes to buy."""
    # Assess fundamental health
    health_issues = []
    if fundamentals.revenue_growth_cagr is not None and fundamentals.revenue_growth_cagr < 0:
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

    if thesis_status is ThesisStatus.INVALIDATED:
        return OpportunitySignal(
            signal_type=OpportunityType.AVOID,
            recommended_action="Thesis invalidated — do not add exposure.",
            **common,
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

    # A weakened thesis is never a buy, even when price action looks attractive.
    if thesis_status is ThesisStatus.WEAKENED:
        return OpportunitySignal(
            signal_type=OpportunityType.CAUTION,
            recommended_action="Thesis weakened — defer new exposure pending re-validation.",
            **common,
        )

    # Stabilization and recovery are more specific diagnoses than a drawdown.
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

    # ON SALE: significant dip with thesis + fundamentals intact
    if drawdown is not None and drawdown < -10 and theme_confidence >= 7 and fundamentals_intact:
        return OpportunitySignal(
            signal_type=OpportunityType.ON_SALE,
            recommended_action=f"Discounted {drawdown:.0f}% from peak — thesis and fundamentals intact. Lump sum candidate.",
            **common,
        )

    return OpportunitySignal(
        signal_type=OpportunityType.NO_SIGNAL,
        recommended_action="No actionable entry signal from the current observation.",
        **common,
    )


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

def score_fundamentals(f: Fundamentals) -> float:
    """Score fundamentals using fixed weights and neutral missing values."""
    values: dict[str, float | None] = {
        "revenue_growth_cagr": _metric_score(
            f.revenue_growth_cagr, lambda value: value * 3
        ),
        "roe": _metric_score(f.roe, lambda value: value * 3),
        "gross_margin": _metric_score(
            f.gross_margin, lambda value: value * 1.5
        ),
        "operating_margin": _metric_score(
            f.operating_margin, lambda value: (value + 10) * 2.5
        ),
        "fcf_yield": _metric_score(f.fcf_yield, lambda value: value * 10),
        "forward_pe": _metric_score(
            f.forward_pe,
            lambda value: 100 - value * 1.5,
            positive_only=True,
        ),
        "peg_ratio": _metric_score(
            f.peg_ratio,
            lambda value: 100 - value * 30,
            positive_only=True,
        ),
        "debt_to_equity": _metric_score(
            f.debt_to_equity, lambda value: 100 - value * 0.3
        ),
    }
    score = sum(
        FUNDAMENTAL_METRIC_WEIGHTS[name]
        * (value if value is not None else MISSING_METRIC_SCORE)
        for name, value in values.items()
    )
    return round(score, 1)


def _bounded(value: float) -> float:
    return min(100.0, max(0.0, value))


def _metric_score(
    value: float | None,
    transform: Callable[[float], float],
    *,
    positive_only: bool = False,
) -> float | None:
    if (
        not isinstance(value, (int, float))
        or not math.isfinite(value)
        or (positive_only and value <= 0)
    ):
        return None
    return _bounded(transform(float(value)))


def _combined_llm_scores(
    company: Company,
    theme: ThemeThesis,
    fundamentals: Fundamentals,
    sector_median_pe: str = "~18",
    *,
    provider: ScoringProvider | None = None,
) -> tuple[_AIScorePayload, ScoringProvider]:
    """Get a validated AI assessment, retrying malformed responses once."""
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

    scoring_provider = provider or CodexScoringProvider()
    last_error: Exception | None = None
    for attempt in range(AI_VALIDATION_ATTEMPTS):
        try:
            raw = scoring_provider.score(prompt)
            payload = _AIScorePayload.model_validate(
                json.loads(_extract_json(raw))
            )
            if payload.ticker.upper() != company.full_ticker.upper():
                raise ValueError(
                    f"AI ticker '{payload.ticker}' does not match "
                    f"'{company.full_ticker}'"
                )
            return payload, scoring_provider
        except llm.CodexCLIError as exc:
            if not exc.retryable:
                raise
            last_error = exc
            log.warning(
                "AI scoring provider failed for %s (attempt %d/%d): %s",
                company.full_ticker,
                attempt + 1,
                AI_VALIDATION_ATTEMPTS,
                exc,
            )
        except Exception as exc:
            last_error = exc
            log.warning(
                "Invalid AI score for %s (attempt %d/%d): %s",
                company.full_ticker,
                attempt + 1,
                AI_VALIDATION_ATTEMPTS,
                exc,
            )
    raise ScoringValidationError(
        f"Unable to produce a valid score for {company.full_ticker}"
    ) from last_error


def assess_valuation(f: Fundamentals) -> ValuationContext:
    """Heuristic valuation assessment."""
    forward_pe = f.forward_pe if f.forward_pe is not None and f.forward_pe > 0 else None
    peg_ratio = f.peg_ratio if f.peg_ratio is not None and f.peg_ratio > 0 else None
    if forward_pe is None and peg_ratio is None:
        return ValuationContext(
            level=ValuationLevel.FAIR,
            summary="Insufficient positive earnings multiples for valuation.",
        )

    if peg_ratio is not None and peg_ratio < 1:
        level = ValuationLevel.CHEAP
    elif forward_pe is not None and forward_pe < 15:
        level = ValuationLevel.CHEAP
    elif forward_pe is not None and forward_pe > 35:
        level = ValuationLevel.EXPENSIVE
    else:
        level = ValuationLevel.FAIR

    parts = []
    if forward_pe is not None:
        parts.append(f"Forward P/E: {forward_pe:.1f}")
    if peg_ratio is not None:
        parts.append(f"PEG: {peg_ratio:.2f}")
    summary = ", ".join(parts)
    return ValuationContext(
        level=level,
        forward_pe_vs_sp500=parts[0] if forward_pe is not None else None,
        summary=f"{level.value.capitalize()} — {summary}",
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
    if f.revenue_growth_cagr is not None:
        period = (
            f" over {f.revenue_growth_period_years:.1f}y"
            if f.revenue_growth_period_years is not None
            else ""
        )
        lines.append(f"Revenue CAGR{period}: {f.revenue_growth_cagr:.1f}%")
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
