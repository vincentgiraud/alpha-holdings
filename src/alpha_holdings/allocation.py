"""Portfolio allocation engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable

from alpha_holdings.config import (
    CASH_TICKER,
    CORE_TICKER,
    DEFENSIVE_TICKER,
    MAX_COMPANY_PCT,
    MAX_STOCKS_PER_THEME,
    MIN_THEMES,
    REGIME_MIN_CONFIDENCE,
    REGIME_MODIFIER,
    get_max_theme_pct,
    get_currency,
    get_thematic_pct,
)
from alpha_holdings.models import (
    AllocationEntry,
    ETFRecommendation,
    ETFRecommendationType,
    EntryMethod,
    Fundamentals,
    InstrumentPosition,
    InstrumentType,
    MacroRegime,
    PortfolioAllocation,
    PortfolioSleeve,
    RiskProfile,
    ThemeScore,
    ThemeThesis,
)

class AllocationError(ValueError):
    """Raised when a required sleeve cannot be backed by a priced instrument."""


@dataclass(frozen=True)
class _Candidate:
    theme: str
    ticker: str
    exposure_keys: frozenset[str]
    instrument_type: InstrumentType
    conviction: float
    entry_method: EntryMethod


def allocate(
    themes: list[ThemeThesis],
    scores: dict[str, list[ThemeScore]],
    etf_recs: dict[str, ETFRecommendation],
    profile: RiskProfile,
    regime: MacroRegime,
    *,
    fund_data: dict | None = None,
    capital: float | None = None,
    clock: Callable[[], datetime] | None = None,
    base_currency: str = "USD",
) -> PortfolioAllocation:
    """Generate a fully invested model portfolio from validated instruments."""
    observed_at = (clock or (lambda: datetime.now(UTC)))()
    priced = {ticker.upper(): data for ticker, data in (fund_data or {}).items()}
    base_thematic = get_thematic_pct(profile)
    modifier = REGIME_MODIFIER.get(regime.regime.value, 1.0)
    thematic_pct = base_thematic * modifier
    min_confidence = REGIME_MIN_CONFIDENCE.get(regime.regime.value, 5)

    confidence_eligible = [
        theme for theme in themes if theme.confidence_score >= min_confidence
    ]
    candidates_by_theme = {
        theme.name: candidates
        for theme in confidence_eligible
        if (
            candidates := _validated_candidates(
                theme,
                scores.get(theme.name, []),
                etf_recs.get(theme.name),
                profile,
                priced,
            )
        )
    }
    eligible = [
        theme for theme in confidence_eligible if theme.name in candidates_by_theme
    ]
    minimum_themes = MIN_THEMES[profile.appetite]
    if len(eligible) < minimum_themes:
        reason = None
        if eligible:
            reason = (
                f"{len(eligible)} validated themes is below the "
                f"{profile.appetite.value} minimum of {minimum_themes}; "
                f"{thematic_pct * 100:.1f}% moved to core"
            )
        else:
            reason = (
                f"No validated themes met the allocation policy; "
                f"{thematic_pct * 100:.1f}% moved to core"
            )
        return _non_thematic_allocation(
            profile=profile,
            regime=regime,
            priced=priced,
            capital=capital,
            modifier=modifier,
            observed_at=observed_at,
            base_currency=base_currency,
            residual_reason=reason,
        )

    overlap_penalties = _compute_overlap_penalties(eligible)
    target_units = round(thematic_pct * 1000)
    candidate_units = _allocate_candidate_units(
        candidates_by_theme,
        overlap_penalties,
        target_units=target_units,
        max_theme_units=round(get_max_theme_pct(profile) * 1000),
        max_company_units=round(MAX_COMPANY_PCT[profile.appetite] * 1000),
    )
    thematic_units = sum(candidate_units.values())

    positions: list[InstrumentPosition] = []
    ticker_units: dict[str, int] = {}
    ticker_types: dict[str, InstrumentType] = {}
    for (theme_name, ticker), units in candidate_units.items():
        if units <= 0:
            continue
        ticker_units[ticker] = ticker_units.get(ticker, 0) + units
        candidate = next(
            item
            for item in candidates_by_theme[theme_name]
            if item.ticker == ticker
        )
        ticker_types[ticker] = candidate.instrument_type

    for ticker in sorted(ticker_units):
        positions.append(
            _priced_position(
                ticker,
                ticker_types[ticker],
                PortfolioSleeve.THEMATIC,
                ticker_units[ticker] / 10,
                priced,
                currency=_currency_for_ticker(ticker),
            )
        )

    entries: list[AllocationEntry] = []
    for theme in eligible:
        allocations = {
            ticker: units
            for (theme_name, ticker), units in candidate_units.items()
            if theme_name == theme.name and units > 0
        }
        if not allocations:
            continue
        candidates = candidates_by_theme[theme.name]
        candidate_by_ticker = {candidate.ticker: candidate for candidate in candidates}
        allocated_candidates = [candidate_by_ticker[ticker] for ticker in allocations]
        entry_method = max(
            allocated_candidates,
            key=lambda candidate: candidate.conviction,
        ).entry_method
        entries.append(
            AllocationEntry(
                theme=theme.name,
                vehicle=", ".join(sorted(allocations)),
                tickers=sorted(allocations),
                vehicle_type=(
                    "etf"
                    if all(
                        candidate.instrument_type is InstrumentType.ETF
                        for candidate in allocated_candidates
                    )
                    else "stocks"
                ),
                pct_allocation=sum(allocations.values()) / 10,
                entry_method=entry_method,
                rationale=(
                    f"Confidence {theme.confidence_score}/10; "
                    f"overlap modifier {overlap_penalties[theme.name]:.2f}"
                ),
                entry_prices={
                    ticker: priced[ticker].current_price
                    for ticker in allocations
                    if priced[ticker].current_price is not None
                },
            )
        )

    defensive_units = 0
    routing_reasons: list[str] = []
    if regime.regime.value == "bear":
        defensive_units = round((1000 - target_units) * 0.2)
    core_units = 1000 - thematic_units - defensive_units
    cash_units = 0
    if defensive_units:
        if _is_valid_priced_instrument(
            DEFENSIVE_TICKER, InstrumentType.ETF, priced
        ):
            positions.append(
                _priced_position(
                    DEFENSIVE_TICKER,
                    InstrumentType.ETF,
                    PortfolioSleeve.DEFENSIVE,
                    defensive_units / 10,
                    priced,
                    currency=_currency_for_ticker(DEFENSIVE_TICKER),
                )
            )
        else:
            cash_units += defensive_units
            routing_reasons.append(
                f"Configured defensive {DEFENSIVE_TICKER} was unavailable; "
                f"{defensive_units / 10:.1f}% moved to cash"
            )
            defensive_units = 0

    if core_units:
        if _is_valid_priced_instrument(CORE_TICKER, InstrumentType.ETF, priced):
            positions.append(
                _priced_position(
                    CORE_TICKER,
                    InstrumentType.ETF,
                    PortfolioSleeve.CORE,
                    core_units / 10,
                    priced,
                    currency=_currency_for_ticker(CORE_TICKER),
                )
            )
        else:
            cash_units += core_units
            routing_reasons.append(
                f"Configured core {CORE_TICKER} was unavailable; "
                f"{core_units / 10:.1f}% moved to cash"
            )
            core_units = 0

    if cash_units:
        positions.append(
            _cash_position(
                cash_units / 10,
                currency=base_currency,
                observed_at=observed_at,
            )
        )

    residual_units = target_units - thematic_units
    if residual_units:
        destination = "core" if core_units else "cash"
        routing_reasons.append(
            f"{residual_units / 10:.1f}% exceeded thematic capacity and moved "
            f"to {destination}"
        )
    residual_reason = "; ".join(routing_reasons) or None

    return PortfolioAllocation(
        risk_profile=profile,
        macro_regime=regime,
        positions=_with_capital_amounts(positions, capital),
        entries=entries,
        core_pct=core_units / 10,
        defensive_pct=defensive_units / 10,
        cash_pct=cash_units / 10,
        capital=capital,
        effective_allocation_modifier=modifier,
        residual_reason=residual_reason,
        generated_at=observed_at,
    )


def _non_thematic_allocation(
    *,
    profile: RiskProfile,
    regime: MacroRegime,
    priced: dict[str, Fundamentals],
    capital: float | None,
    modifier: float,
    observed_at: datetime,
    base_currency: str,
    residual_reason: str | None,
) -> PortfolioAllocation:
    if _is_valid_priced_instrument(CORE_TICKER, InstrumentType.ETF, priced):
        positions = [
            _priced_position(
                CORE_TICKER,
                InstrumentType.ETF,
                PortfolioSleeve.CORE,
                100.0,
                priced,
                currency=_currency_for_ticker(CORE_TICKER),
            )
        ]
        core_pct = 100.0
        cash_pct = 0.0
    else:
        positions = [
            _cash_position(100.0, currency=base_currency, observed_at=observed_at)
        ]
        core_pct = 0.0
        cash_pct = 100.0
        suffix = "configured core was unavailable; capital retained as cash"
        residual_reason = (
            f"{residual_reason}; {suffix}" if residual_reason else suffix
        )
    return PortfolioAllocation(
        risk_profile=profile,
        macro_regime=regime,
        positions=_with_capital_amounts(positions, capital),
        core_pct=core_pct,
        cash_pct=cash_pct,
        capital=capital,
        effective_allocation_modifier=modifier,
        residual_reason=residual_reason,
        generated_at=observed_at,
    )


def _validated_candidates(
    theme: ThemeThesis,
    scores: list[ThemeScore],
    etf: ETFRecommendation | None,
    profile: RiskProfile,
    priced: dict[str, Fundamentals],
) -> list[_Candidate]:
    if (
        etf is not None
        and etf.recommendation is ETFRecommendationType.ETF_SUFFICIENT
        and etf.etf_ticker
    ):
        ticker = etf.etf_ticker.strip().upper()
        if _is_valid_priced_instrument(ticker, InstrumentType.ETF, priced):
            conviction = (
                sum(score.composite_score for score in scores) / len(scores)
                if scores
                else theme.confidence_score * 10
            )
            return [
                _Candidate(
                    theme=theme.name,
                    ticker=ticker,
                    exposure_keys=frozenset({f"instrument:{ticker}"}),
                    instrument_type=InstrumentType.ETF,
                    conviction=conviction,
                    entry_method=EntryMethod.DCA,
                )
            ]

    from alpha_holdings.fundamentals import validate_company_identity

    companies = {company.full_ticker.upper(): company for company in theme.all_companies}
    ranked: list[ThemeScore] = []
    for score in scores:
        ticker = score.ticker.strip().upper()
        company = companies.get(ticker)
        fundamentals = priced.get(ticker)
        if (
            company is None
            or fundamentals is None
            or not score.has_complete_evidence
            or not _is_valid_priced_instrument(ticker, InstrumentType.STOCK, priced)
        ):
            continue
        identity_matches, _reason = validate_company_identity(company, fundamentals)
        if identity_matches:
            ranked.append(score)

    ranked.sort(key=lambda score: (-score.composite_score, score.ticker))
    return [
        _Candidate(
            theme=theme.name,
            ticker=score.ticker.strip().upper(),
            exposure_keys=companies[score.ticker.strip().upper()].exposure_keys,
            instrument_type=InstrumentType.STOCK,
            conviction=max(score.composite_score, 1.0),
            entry_method=score.entry_method,
        )
        for score in ranked[: MAX_STOCKS_PER_THEME[profile.appetite]]
    ]


def _allocate_candidate_units(
    candidates_by_theme: dict[str, list[_Candidate]],
    overlap_penalties: dict[str, float],
    *,
    target_units: int,
    max_theme_units: int,
    max_company_units: int,
) -> dict[tuple[str, str], int]:
    candidates = [
        candidate
        for theme_candidates in candidates_by_theme.values()
        for candidate in theme_candidates
    ]
    exposure_groups = _resolve_exposure_groups(candidates)
    allocated = {(candidate.theme, candidate.ticker): 0 for candidate in candidates}
    theme_units = {theme: 0 for theme in candidates_by_theme}
    exposure_units: dict[str, int] = {}

    # Seed every eligible theme before conviction weighting so the configured
    # minimum means funded themes, not merely themes admitted to the pool.
    for theme in sorted(candidates_by_theme):
        if sum(allocated.values()) >= target_units:
            break
        theme_candidates = candidates_by_theme[theme]
        winner = max(
            theme_candidates,
            key=lambda candidate: (candidate.conviction, candidate.ticker),
        )
        ticker_cap = (
            max_theme_units
            if winner.instrument_type is InstrumentType.ETF
            else max_company_units
        )
        exposure_group = exposure_groups[(winner.theme, winner.ticker)]
        if exposure_units.get(exposure_group, 0) >= ticker_cap:
            continue
        key = (winner.theme, winner.ticker)
        allocated[key] += 1
        theme_units[winner.theme] += 1
        exposure_units[exposure_group] = exposure_units.get(exposure_group, 0) + 1

    for _unit in range(target_units - sum(allocated.values())):
        eligible = [
            candidate
            for candidate in candidates
            if theme_units[candidate.theme] < max_theme_units
            and exposure_units.get(
                exposure_groups[(candidate.theme, candidate.ticker)],
                0,
            )
            < (
                max_theme_units
                if candidate.instrument_type is InstrumentType.ETF
                else max_company_units
            )
        ]
        if not eligible:
            break
        winner = max(
            eligible,
            key=lambda candidate: (
                candidate.conviction
                * overlap_penalties.get(candidate.theme, 1.0)
                / (allocated[(candidate.theme, candidate.ticker)] + 1),
                candidate.theme,
                candidate.ticker,
            ),
        )
        key = (winner.theme, winner.ticker)
        allocated[key] += 1
        theme_units[winner.theme] += 1
        exposure_group = exposure_groups[(winner.theme, winner.ticker)]
        exposure_units[exposure_group] = exposure_units.get(exposure_group, 0) + 1
    return allocated


def _resolve_exposure_groups(
    candidates: list[_Candidate],
) -> dict[tuple[str, str], str]:
    """Collapse overlapping issuer aliases into transitive exposure groups."""
    parents: dict[str, str] = {}

    def find(alias: str) -> str:
        parent = parents.setdefault(alias, alias)
        if parent != alias:
            parents[alias] = find(parent)
        return parents[alias]

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            first, second = sorted((left_root, right_root))
            parents[second] = first

    for candidate in candidates:
        aliases = sorted(candidate.exposure_keys)
        for alias in aliases[1:]:
            union(aliases[0], alias)

    return {
        (candidate.theme, candidate.ticker): find(
            sorted(candidate.exposure_keys)[0]
        )
        for candidate in candidates
    }


def _is_valid_priced_instrument(
    ticker: str,
    instrument_type: InstrumentType,
    fund_data: dict[str, Fundamentals],
) -> bool:
    fundamentals = fund_data.get(ticker)
    if fundamentals is None:
        return False
    from alpha_holdings.fundamentals import validate_priced_instrument

    valid, _reason = validate_priced_instrument(
        ticker,
        instrument_type,
        fundamentals,
    )
    return valid


def _priced_position(
    ticker: str,
    instrument_type: InstrumentType,
    sleeve: PortfolioSleeve,
    weight_pct: float,
    fund_data: dict[str, Fundamentals],
    *,
    currency: str,
) -> InstrumentPosition:
    fundamentals = fund_data.get(ticker)
    if not _is_valid_priced_instrument(ticker, instrument_type, fund_data):
        raise AllocationError(f"{ticker} is not a validated priced instrument")
    assert fundamentals is not None
    assert fundamentals.current_price is not None
    assert fundamentals.fetched_at is not None
    return InstrumentPosition(
        ticker=ticker,
        instrument_type=instrument_type,
        sleeve=sleeve,
        weight_pct=weight_pct,
        currency=currency,
        entry_price=fundamentals.current_price,
        price_timestamp=fundamentals.price_as_of or fundamentals.fetched_at,
    )


def _cash_position(
    weight_pct: float,
    *,
    currency: str,
    observed_at: datetime,
) -> InstrumentPosition:
    return InstrumentPosition(
        ticker=CASH_TICKER,
        instrument_type=InstrumentType.CASH,
        sleeve=PortfolioSleeve.CASH,
        weight_pct=weight_pct,
        currency=currency,
        entry_price=1.0,
        price_timestamp=observed_at,
    )


def _with_capital_amounts(
    positions: list[InstrumentPosition],
    capital: float | None,
) -> list[InstrumentPosition]:
    if capital is None:
        return positions
    total_cents = round(capital * 100)
    raw_cents = [
        total_cents * position.weight_pct / 100 for position in positions
    ]
    allocated_cents = [int(value) for value in raw_cents]
    remaining_cents = total_cents - sum(allocated_cents)
    remainder_order = sorted(
        range(len(positions)),
        key=lambda index: (
            -(raw_cents[index] - allocated_cents[index]),
            positions[index].ticker,
        ),
    )
    for index in remainder_order[:remaining_cents]:
        allocated_cents[index] += 1
    return [
        position.model_copy(
            update={"capital_amount": allocated_cents[index] / 100}
        )
        for index, position in enumerate(positions)
    ]


def _currency_for_ticker(ticker: str) -> str:
    suffix = ticker.rsplit(".", 1)[1] if "." in ticker else None
    return get_currency(suffix)


def _compute_overlap_penalties(themes: list[ThemeThesis]) -> dict[str, float]:
    """Compute per-theme penalty for ticker + sector overlap with other themes."""
    penalties: dict[str, float] = {t.name: 1.0 for t in themes}
    for i, a in enumerate(themes):
        a_tickers = {company.full_ticker.upper() for company in a.all_companies}
        a_sectors = {company.sector.casefold() for company in a.all_companies}
        for b in themes[i + 1 :]:
            b_tickers = {company.full_ticker.upper() for company in b.all_companies}
            b_sectors = {company.sector.casefold() for company in b.all_companies}

            ticker_overlap = len(a_tickers & b_tickers) / max(len(a_tickers | b_tickers), 1)
            sector_overlap = len(a_sectors & b_sectors) / max(len(a_sectors | b_sectors), 1)
            combined = (ticker_overlap + sector_overlap) / 2

            if combined > 0.3:
                penalty = 1.0 - (combined - 0.3)  # reduces allocation
                penalties[a.name] = min(penalties[a.name], max(penalty, 0.5))
                penalties[b.name] = min(penalties[b.name], max(penalty, 0.5))

    return penalties
