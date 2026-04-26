"""Portfolio allocation engine."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from alpha_holdings.config import (
    MAX_THEME_PCT,
    MAX_STOCKS_PER_THEME,
    MIN_THEMES,
    REGIME_MIN_CONFIDENCE,
    REGIME_MODIFIER,
    get_max_theme_pct,
    get_thematic_pct,
)
from alpha_holdings.models import (
    AllocationEntry,
    ETFRecommendation,
    ETFRecommendationType,
    EntryMethod,
    MacroRegime,
    PortfolioAllocation,
    RiskProfile,
    ThemeScore,
    ThemeThesis,
)

log = logging.getLogger(__name__)


def allocate(
    themes: list[ThemeThesis],
    scores: dict[str, list[ThemeScore]],
    etf_recs: dict[str, ETFRecommendation],
    profile: RiskProfile,
    regime: MacroRegime,
    *,
    fund_data: dict | None = None,
    capital: float | None = None,
) -> PortfolioAllocation:
    """Generate a model portfolio allocation."""
    base_thematic = get_thematic_pct(profile)
    modifier = REGIME_MODIFIER.get(regime.regime.value, 1.0)
    thematic_pct = base_thematic * modifier
    max_per_theme = get_max_theme_pct(profile)
    min_confidence = REGIME_MIN_CONFIDENCE.get(regime.regime.value, 5)

    # Filter themes by confidence gate
    eligible = [t for t in themes if t.confidence_score >= min_confidence]
    if not eligible:
        log.warning("No themes meet confidence threshold (%d). All to core.", min_confidence)
        return PortfolioAllocation(
            risk_profile=profile,
            macro_regime=regime,
            core_pct=1.0,
            generated_at=datetime.utcnow(),
        )

    # Score themes by average composite score
    theme_scores: dict[str, float] = {}
    for t in eligible:
        t_scores = scores.get(t.name, [])
        if t_scores:
            theme_scores[t.name] = sum(s.composite_score for s in t_scores) / len(t_scores)
        else:
            theme_scores[t.name] = t.confidence_score * 10  # fallback

    # Overlap penalty: check ticker overlap between theme pairs
    overlap_penalties = _compute_overlap_penalties(eligible)

    # Conviction-weighted allocation
    total_conviction = sum(theme_scores.values())
    entries: list[AllocationEntry] = []
    allocated = 0.0

    for t in sorted(eligible, key=lambda x: theme_scores.get(x.name, 0), reverse=True):
        if total_conviction == 0:
            break
        raw_pct = (theme_scores[t.name] / total_conviction) * thematic_pct

        # Apply overlap penalty
        penalty = overlap_penalties.get(t.name, 1.0)
        raw_pct *= penalty

        # Cap per theme
        pct = min(raw_pct, max_per_theme)
        if allocated + pct > thematic_pct:
            pct = thematic_pct - allocated
        if pct <= 0.005:
            continue

        # Vehicle selection
        etf = etf_recs.get(t.name)
        t_scores = scores.get(t.name, [])
        vehicle, v_type = _select_vehicle(t, etf, t_scores, profile)

        # Entry method: use best score's recommendation
        entry = EntryMethod.DCA
        if t_scores:
            best = max(t_scores, key=lambda s: s.composite_score)
            entry = best.entry_method

        # Entry prices for sell discipline tracking
        entry_prices: dict[str, float] = {}
        if fund_data:
            for ticker_str in vehicle.split(", "):
                ticker_str = ticker_str.strip()
                f = fund_data.get(ticker_str)
                if f and f.current_price:
                    entry_prices[ticker_str] = f.current_price

        entries.append(AllocationEntry(
            theme=t.name,
            vehicle=vehicle,
            vehicle_type=v_type,
            pct_allocation=round(pct * 100, 1),
            entry_method=entry,
            rationale=f"Confidence {t.confidence_score}/10, avg score {theme_scores[t.name]:.0f}",
            entry_prices=entry_prices,
        ))
        allocated += pct

    core_pct = 1.0 - allocated
    defensive_pct = 0.0
    if regime.regime.value == "bear" and core_pct > 0.5:
        defensive_pct = round(core_pct * 0.2, 3)
        core_pct -= defensive_pct

    return PortfolioAllocation(
        risk_profile=profile,
        macro_regime=regime,
        entries=entries,
        core_pct=round(core_pct * 100, 1),
        defensive_pct=round(defensive_pct * 100, 1),
        capital=capital,
        generated_at=datetime.utcnow(),
    )


def _compute_overlap_penalties(themes: list[ThemeThesis]) -> dict[str, float]:
    """Compute per-theme penalty for ticker + sector overlap with other themes."""
    penalties: dict[str, float] = {t.name: 1.0 for t in themes}
    for i, a in enumerate(themes):
        a_tickers = {c.ticker for c in a.all_companies}
        a_sectors = {c.sector for c in a.all_companies}
        for b in themes[i + 1 :]:
            b_tickers = {c.ticker for c in b.all_companies}
            b_sectors = {c.sector for c in b.all_companies}

            ticker_overlap = len(a_tickers & b_tickers) / max(len(a_tickers | b_tickers), 1)
            sector_overlap = len(a_sectors & b_sectors) / max(len(a_sectors | b_sectors), 1)
            combined = (ticker_overlap + sector_overlap) / 2

            if combined > 0.3:
                penalty = 1.0 - (combined - 0.3)  # reduces allocation
                penalties[a.name] = min(penalties[a.name], max(penalty, 0.5))
                penalties[b.name] = min(penalties[b.name], max(penalty, 0.5))

    return penalties


def _select_vehicle(
    theme: ThemeThesis,
    etf: Optional[ETFRecommendation],
    scores: list[ThemeScore],
    profile: RiskProfile,
) -> tuple[str, str]:
    """Choose ETF or individual stocks, biased toward Tier 2-3 picks.

    Selects all T2-3 stocks scoring above the pool median, bounded by
    the risk-profile max (conservative: 3, moderate: 5, aggressive: 7).
    Minimum 2 stocks.
    """
    if etf and etf.recommendation == ETFRecommendationType.ETF_SUFFICIENT and etf.etf_ticker:
        return etf.etf_ticker, "etf"

    # Build a ticker→tier lookup from theme companies
    tier_map = {c.full_ticker: c.supply_chain_tier for c in theme.all_companies}

    if scores:
        # Separate by tier
        from alpha_holdings.models import SupplyChainTier

        tier23 = [s for s in scores if tier_map.get(s.ticker) in (
            SupplyChainTier.TIER_2_DIRECT_ENABLER,
            SupplyChainTier.TIER_3_PICKS_AND_SHOVELS,
        )]
        # Prefer Tier 2-3 picks (the shovels); fall back to all if not enough
        pool = tier23 if len(tier23) >= 2 else scores
        ranked = sorted(pool, key=lambda s: s.composite_score, reverse=True)

        # Score-based selection: include all above median, bounded by profile max
        max_stocks = MAX_STOCKS_PER_THEME.get(profile.appetite, 5)
        min_stocks = 2
        if ranked:
            median_score = ranked[len(ranked) // 2].composite_score
            above_median = [s for s in ranked if s.composite_score >= median_score]
            n = max(min_stocks, min(len(above_median), max_stocks))
        else:
            n = min_stocks

        top = ranked[:n]
        tickers = ", ".join(s.ticker for s in top)
        return tickers, "stocks"

    # Fallback to ETF if available
    if etf and etf.etf_ticker:
        return etf.etf_ticker, "etf"
    return theme.name, "theme"
