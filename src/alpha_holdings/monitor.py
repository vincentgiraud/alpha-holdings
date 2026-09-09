"""Course correction, rebalancing signals, and opportunity scanning."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from alpha_holdings import llm
from alpha_holdings.fundamentals import fetch
from alpha_holdings.models import (
    Fundamentals,
    FundamentalsResult,
    MacroSignal,
    MarketDataStatus,
    MonitoringEvent,
    OpportunityType,
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


class MonitoringEventRepository:
    """Append-only storage for course-correction events."""

    def __init__(self, data_dir: str | Path = "data") -> None:
        self.events_dir = Path(data_dir) / "monitoring-events"

    def save(self, event: MonitoringEvent) -> Path:
        self.events_dir.mkdir(parents=True, exist_ok=True)
        destination = self.events_dir / f"{event.event_id}.json"
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.events_dir,
                prefix=f".{event.event_id}.", suffix=".tmp", delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(event.model_dump_json(indent=2))
                temporary.flush()
                os.fsync(temporary.fileno())
            os.link(temporary_path, destination)
            temporary_path.unlink()
            temporary_path = None
        except Exception:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise
        return destination


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


def apply_thesis_updates(
    themes: list[ThemeThesis], updates: list[ThesisUpdate],
) -> tuple[list[ThemeThesis], dict[str, list[str]]]:
    """Apply safe thesis changes; additions stay pending until the discovery pipeline validates them."""
    by_name = {update.theme_name.casefold(): update for update in updates}
    revised: list[ThemeThesis] = []
    pending_additions: dict[str, list[str]] = {}
    for theme in themes:
        update = by_name.get(theme.name.casefold())
        if update is None:
            revised.append(theme.model_copy(deep=True))
            continue
        removals = {ticker.strip().upper() for ticker in update.companies_to_remove}
        sub_themes = [
            sub_theme.model_copy(update={
                "companies": [
                    company for company in sub_theme.companies
                    if company.full_ticker.upper() not in removals
                ]
            })
            for sub_theme in theme.sub_themes
        ]
        revised.append(theme.model_copy(update={
            "confidence_score": update.new_confidence,
            "sub_themes": sub_themes,
        }))
        additions = sorted({ticker.strip().upper() for ticker in update.companies_to_add if ticker.strip()})
        if additions:
            pending_additions[theme.name] = additions
    return revised, pending_additions


def funded_themes(
    themes: list[ThemeThesis], *, funded_by_theme: dict[str, set[str]],
) -> list[ThemeThesis]:
    """Return a non-mutating view containing only allocated theme instruments."""
    scoped: list[ThemeThesis] = []
    for theme in themes:
        tickers = funded_by_theme.get(theme.name.casefold(), set())
        if not tickers:
            continue
        sub_themes = [
            sub_theme.model_copy(update={
                "companies": [
                    company for company in sub_theme.companies
                    if company.full_ticker.upper() in tickers
                ]
            })
            for sub_theme in theme.sub_themes
        ]
        scoped.append(theme.model_copy(update={"sub_themes": sub_themes}))
    return scoped


def funded_theme_tickers(entries) -> dict[str, set[str]]:
    """Map every allocated thematic vehicle to its originating theme."""
    scope: dict[str, set[str]] = {}
    for entry in entries:
        scope.setdefault(entry.theme.casefold(), set()).update(
            ticker.upper() for ticker in entry.tickers
        )
    return scope


def scan_opportunities(
    themes: list[ThemeThesis], *, skip_cache: bool = False,
    theme_statuses: dict[str, ThesisStatus] | None = None,
) -> OpportunityScanResult:
    """Scan all funded themes for dip opportunities, filtering out low-quality companies."""
    from alpha_holdings.fundamentals import passes_quality_filter

    scan = OpportunityScanResult()
    statuses = {name.casefold(): status for name, status in (theme_statuses or {}).items()}
    for theme in themes:
        status = statuses.get(theme.name.casefold(), ThesisStatus.UNCHANGED)
        for company in theme.all_companies:
            observation = fetch(company.full_ticker, skip_cache=skip_cache)
            scan.market_data[company.full_ticker] = observation
            f = observation.data
            if f is None:
                scan.signals.append(OpportunitySignal(
                    ticker=company.full_ticker,
                    signal_type=OpportunityType.UNAVAILABLE,
                    thesis_confidence=theme.confidence_score,
                    fundamental_health="Unavailable",
                    recommended_action=(
                        "Market data unavailable — do not classify this instrument."
                    ),
                    theme_name=theme.name,
                    supply_chain_tier=company.supply_chain_tier.value,
                ))
                continue
            if status is ThesisStatus.INVALIDATED:
                scan.signals.append(detect_opportunity(
                    company.full_ticker,
                    theme.confidence_score,
                    f,
                    theme_name=theme.name,
                    supply_chain_tier=company.supply_chain_tier.value,
                    thesis_status=status,
                ))
                continue
            # Skip companies that fail quality filters
            passes, reason = passes_quality_filter(f)
            if not passes:
                log.debug("Skipping %s in opportunity scan: %s", company.full_ticker, reason)
                scan.signals.append(OpportunitySignal(
                    ticker=company.full_ticker,
                    signal_type=OpportunityType.CAUTION,
                    thesis_confidence=theme.confidence_score,
                    fundamental_health=reason,
                    current_price=f.current_price,
                    drawdown_pct=f.drawdown_from_peak,
                    recommended_action="Fundamentals fail the quality policy — do not add exposure.",
                    theme_name=theme.name,
                    supply_chain_tier=company.supply_chain_tier.value,
                ))
                continue
            opp = detect_opportunity(
                company.full_ticker,
                theme.confidence_score,
                f,
                theme_name=theme.name,
                supply_chain_tier=company.supply_chain_tier.value,
                thesis_status=status,
            )
            scan.signals.append(opp)
    return scan
