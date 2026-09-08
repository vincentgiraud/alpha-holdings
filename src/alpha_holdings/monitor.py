"""Course correction, rebalancing signals, and opportunity scanning."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from alpha_holdings import llm
from alpha_holdings.fundamentals import fetch
from alpha_holdings.models import (
    Fundamentals,
    FundamentalsResult,
    MacroSignal,
    MarketDataStatus,
    OpportunitySignal,
    RebalanceAction,
    RebalanceSignal,
    ThemeThesis,
    ThesisStatus,
    ThesisUpdate,
    Urgency,
)
from alpha_holdings.prompts.course_correction import COURSE_CORRECTION_PROMPT
from alpha_holdings.scoring import detect_opportunity
from alpha_holdings.signals import _extract_json

log = logging.getLogger(__name__)


@dataclass
class OpportunityScanResult:
    signals: list[OpportunitySignal] = field(default_factory=list)
    market_data: dict[str, FundamentalsResult] = field(default_factory=dict)

    @property
    def incomplete(self) -> bool:
        return any(
            result.status is not MarketDataStatus.AVAILABLE
            for result in self.market_data.values()
        )

    @property
    def usable_count(self) -> int:
        return sum(result.data is not None for result in self.market_data.values())


def check_thesis(theme: ThemeThesis) -> ThesisUpdate:
    """Re-evaluate a theme against fresh web data."""
    prompt = COURSE_CORRECTION_PROMPT.format(
        theme_name=theme.name,
        thesis_summary=theme.thesis_summary,
        why_now=theme.why_now,
        confidence=theme.confidence_score,
        discovered_date=theme.discovered_at.strftime("%Y-%m-%d") if theme.discovered_at else "unknown",
    )

    try:
        raw = llm.respond_text(prompt, web_search=True)
        data = json.loads(_extract_json(raw))
        return ThesisUpdate(
            theme_name=theme.name,
            previous_confidence=theme.confidence_score,
            **data,
        )
    except Exception as exc:
        log.warning("Course correction failed for %s: %s", theme.name, exc)
        return ThesisUpdate(
            theme_name=theme.name,
            status=ThesisStatus.UNCHANGED,
            reason="Unable to re-evaluate — defaulting to unchanged.",
            previous_confidence=theme.confidence_score,
            new_confidence=theme.confidence_score,
        )


def generate_rebalance_signals(
    themes: list[ThemeThesis],
    updates: list[ThesisUpdate],
) -> list[RebalanceSignal]:
    """Generate rebalancing signals from thesis updates."""
    signals: list[RebalanceSignal] = []

    for update in updates:
        if update.status == ThesisStatus.WEAKENED:
            signals.append(RebalanceSignal(
                action=RebalanceAction.REDUCE_THEME,
                from_asset=update.theme_name,
                to_asset="broad market core or stronger themes",
                reason=update.reason,
                urgency=Urgency.MEDIUM,
            ))
        elif update.status == ThesisStatus.INVALIDATED:
            signals.append(RebalanceSignal(
                action=RebalanceAction.REDUCE_THEME,
                from_asset=update.theme_name,
                to_asset="broad market core",
                reason=f"THESIS INVALIDATED: {update.reason}",
                urgency=Urgency.HIGH,
            ))
        elif update.status == ThesisStatus.STRENGTHENED:
            signals.append(RebalanceSignal(
                action=RebalanceAction.ADD_THEME,
                to_asset=update.theme_name,
                reason=f"Thesis strengthened: {update.reason}",
                urgency=Urgency.LOW,
            ))

        # Flag companies to remove as holding-level rotations
        for ticker in update.companies_to_remove:
            signals.append(RebalanceSignal(
                action=RebalanceAction.ROTATE_HOLDING,
                from_asset=ticker,
                to_asset="better positioned company in same theme or theme ETF",
                reason=f"Removed from {update.theme_name} thesis.",
                urgency=Urgency.MEDIUM,
            ))

    return signals


def scan_opportunities(themes: list[ThemeThesis], *, skip_cache: bool = False) -> OpportunityScanResult:
    """Scan all funded themes for dip opportunities, filtering out low-quality companies."""
    from alpha_holdings.fundamentals import passes_quality_filter

    scan = OpportunityScanResult()
    for theme in themes:
        for company in theme.all_companies:
            observation = fetch(company.full_ticker, skip_cache=skip_cache)
            scan.market_data[company.full_ticker] = observation
            f = observation.data
            if f is None:
                continue
            # Skip companies that fail quality filters
            passes, reason = passes_quality_filter(f)
            if not passes:
                log.debug("Skipping %s in opportunity scan: %s", company.full_ticker, reason)
                continue
            opp = detect_opportunity(
                company.full_ticker,
                theme.confidence_score,
                f,
                theme_name=theme.name,
                supply_chain_tier=company.supply_chain_tier.value,
            )
            if opp:
                scan.signals.append(opp)
    return scan
