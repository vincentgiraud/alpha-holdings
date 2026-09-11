"""Versioned, atomic persistence for coherent investment-research runs."""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from alpha_holdings.models import (
    ETFRecommendation,
    ETFRecommendationType,
    FundamentalsResult,
    InstrumentMetadata,
    InstrumentPrice,
    InstrumentType,
    MacroRegimeType,
    RUN_SNAPSHOT_SECTIONS,
    PortfolioAllocation,
    PriceBasis,
    RiskAppetite,
    RunSnapshot,
    SnapshotCompleteness,
    SnapshotSourceFormat,
    ThemeScore,
    ThemeThesis,
    TimeHorizon,
)


def build_discovery_snapshot(
    *,
    themes: list[ThemeThesis],
    scores: dict[str, list[ThemeScore]],
    market_data: dict[str, FundamentalsResult],
    allocation: PortfolioAllocation,
    etf_recommendations: dict[str, ETFRecommendation] | None = None,
    created_at: datetime | None = None,
    model_configuration: dict | None = None,
    provenance: dict | None = None,
) -> RunSnapshot:
    """Build a coherent run containing every successfully scored candidate."""
    from alpha_holdings.config import get_currency

    companies = {
        company.full_ticker.upper(): company
        for theme in themes
        for company in theme.all_companies
    }
    observations = {ticker.upper(): result for ticker, result in market_data.items()}
    recommendations = etf_recommendations or {}
    metadata: dict[str, InstrumentMetadata] = {}
    prices: dict[str, InstrumentPrice] = {}

    for theme_scores in scores.values():
        for score in theme_scores:
            ticker = score.ticker.upper()
            if not score.has_complete_evidence:
                raise ValueError(f"Scored candidate {ticker} is missing evidence")
            company = companies.get(ticker)
            observation = observations.get(ticker)
            if company is None:
                raise ValueError(f"Scored candidate {ticker} is missing company metadata")
            if (
                observation is None
                or observation.data is None
                or observation.as_of is None
                or observation.data.current_price is None
            ):
                raise ValueError(
                    f"Scored candidate {ticker} is missing a dated entry price"
                )
            fundamentals = observation.data
            currency = get_currency(company.exchange_suffix)
            metadata[ticker] = InstrumentMetadata(
                ticker=ticker,
                instrument_type=InstrumentType.STOCK,
                currency=currency,
                name=fundamentals.name or company.name,
                exchange=company.exchange_suffix,
            )
            prices[ticker] = InstrumentPrice(
                ticker=ticker,
                price=fundamentals.current_price,
                currency=currency,
                observed_at=observation.as_of,
                source=fundamentals.source,
            )

    for position in allocation.positions:
        ticker = position.ticker.upper()
        if (
            position.instrument_type is InstrumentType.ETF
            and position.sleeve.value == "thematic"
        ):
            matching_recommendations = [
                recommendation
                for recommendation in recommendations.values()
                if recommendation.recommendation
                is ETFRecommendationType.ETF_SUFFICIENT
                and (recommendation.etf_ticker or "").strip().upper() == ticker
            ]
            if not matching_recommendations:
                raise ValueError(
                    f"Funded ETF {ticker} is missing its validated selection audit"
                )
            recommendation = matching_recommendations[0]
            if (
                recommendation.adjusted_entry_price != position.entry_price
                or recommendation.price_as_of != position.price_timestamp
                or not recommendation.price_source
            ):
                raise ValueError(
                    f"Funded ETF {ticker} does not match its adjusted price audit"
                )
        if position.instrument_type is InstrumentType.CASH:
            metadata[ticker] = InstrumentMetadata(
                ticker=ticker,
                instrument_type=InstrumentType.CASH,
                currency=position.currency,
                name="Cash",
            )
            prices[ticker] = InstrumentPrice(
                ticker=ticker,
                price=position.entry_price,
                currency=position.currency,
                observed_at=position.price_timestamp,
                source="cash-par",
            )
            continue
        observation = observations.get(ticker)
        if observation is None or observation.data is None or observation.as_of is None:
            raise ValueError(f"Allocated position {ticker} is missing market evidence")
        fundamentals = observation.data
        if (
            fundamentals.current_price != position.entry_price
            or observation.as_of != position.price_timestamp
        ):
            raise ValueError(
                f"Allocated position {ticker} does not match its market observation"
            )
        if position.instrument_type is InstrumentType.ETF:
            if fundamentals.price_basis is not PriceBasis.ADJUSTED_CLOSE or not fundamentals.source:
                raise ValueError(
                    f"Funded ETF {ticker} is missing adjusted-close evidence"
                )
            if (
                position.sleeve.value == "thematic"
                and recommendation.price_source != fundamentals.source
            ):
                raise ValueError(
                    f"Funded ETF {ticker} does not match its adjusted price audit"
                )
        exchange = ticker.rsplit(".", 1)[1] if "." in ticker else None
        metadata[ticker] = InstrumentMetadata(
            ticker=ticker,
            instrument_type=position.instrument_type,
            currency=position.currency,
            name=fundamentals.name,
            exchange=exchange,
        )
        prices[ticker] = InstrumentPrice(
            ticker=ticker,
            price=position.entry_price,
            currency=position.currency,
            observed_at=position.price_timestamp,
            source=fundamentals.source,
        )

    snapshot_provenance = dict(provenance or {})
    snapshot_provenance["market_data"] = {
        ticker: {
            "status": result.status.value,
            "as_of": result.as_of.isoformat() if result.as_of else None,
            "source": result.data.source if result.data is not None else None,
            "reason": result.reason,
        }
        for ticker, result in sorted(observations.items())
    }
    configuration = dict(model_configuration or {})
    configuration["portfolio_construction_mode"] = (
        allocation.portfolio_construction_mode.value
    )
    return RunSnapshot(
        created_at=created_at or datetime.now(UTC),
        themes=themes,
        candidate_scores=scores,
        etf_recommendations=recommendations,
        instrument_metadata=metadata,
        prices=prices,
        positions=allocation.positions,
        allocation=allocation,
        model_configuration=configuration,
        provenance=snapshot_provenance,
    )


class RunSnapshotRepository:
    """Store and retrieve whole runs through one public persistence boundary."""

    def __init__(self, data_dir: str | Path = "data") -> None:
        self.data_dir = Path(data_dir)
        self.runs_dir = self.data_dir / "runs"

    def save(self, snapshot: RunSnapshot) -> Path:
        """Atomically publish one complete versioned snapshot."""
        completeness = snapshot.completeness
        if completeness.source_format is not SnapshotSourceFormat.VERSIONED:
            raise ValueError("only versioned snapshots can be persisted")
        if not completeness.is_complete:
            raise ValueError("an incomplete snapshot cannot be persisted")
        absent = set(RUN_SNAPSHOT_SECTIONS) - set(completeness.available_sections)
        if absent:
            raise ValueError(
                "a complete snapshot must declare every section available: "
                + ", ".join(sorted(absent))
            )

        self.runs_dir.mkdir(parents=True, exist_ok=True)
        destination = self.runs_dir / f"{snapshot.run_id}.json"
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.runs_dir,
                prefix=f".{snapshot.run_id}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(snapshot.model_dump_json(indent=2))
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

    def load(self, run_id: str) -> RunSnapshot | None:
        """Load a specific versioned run or an adapted ``legacy-YYYYMMDD`` run."""
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", run_id):
            return None
        if run_id.startswith("legacy-"):
            return self._load_legacy(run_id.removeprefix("legacy-"))
        path = self.runs_dir / f"{run_id}.json"
        return self._load_versioned(path)

    def load_latest(self) -> RunSnapshot | None:
        """Load the newest complete run, falling back to legacy storage."""
        versioned = self.list_complete()
        if versioned:
            return max(versioned, key=lambda item: _timestamp(item.created_at))

        legacy: list[RunSnapshot] = []
        allocation_dir = self.data_dir / "allocations"
        if allocation_dir.exists():
            for path in allocation_dir.glob("*_allocation.json"):
                date_str = path.name.removesuffix("_allocation.json")
                snapshot = self._load_legacy(date_str)
                if snapshot is not None:
                    legacy.append(snapshot)

        if not legacy:
            return None
        return max(legacy, key=lambda item: _timestamp(item.created_at))

    def list_complete(self) -> list[RunSnapshot]:
        """Return all complete versioned runs in chronological order."""
        if not self.runs_dir.exists():
            return []
        snapshots = []
        for path in self.runs_dir.glob("*.json"):
            snapshot = self._load_versioned(path)
            if snapshot is not None and snapshot.completeness.is_complete:
                snapshots.append(snapshot)
        return sorted(snapshots, key=lambda item: _timestamp(item.created_at))

    @staticmethod
    def _load_versioned(path: Path) -> RunSnapshot | None:
        if not path.exists():
            return None
        try:
            snapshot = RunSnapshot.model_validate_json(path.read_text())
        except (OSError, ValidationError, ValueError):
            return None
        if snapshot.completeness.source_format is not SnapshotSourceFormat.VERSIONED:
            return None
        return snapshot

    def _load_legacy(self, date_str: str) -> RunSnapshot | None:
        allocation_path = self.data_dir / "allocations" / f"{date_str}_allocation.json"
        if not allocation_path.exists():
            return None
        try:
            raw_allocation = json.loads(allocation_path.read_text())
            try:
                allocation = PortfolioAllocation.model_validate(raw_allocation)
            except ValidationError:
                allocation = _adapt_legacy_allocation(raw_allocation, date_str)
            created_at = allocation.generated_at or datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=UTC)
        except (OSError, json.JSONDecodeError, ValidationError, TypeError, ValueError):
            return None

        available = ["allocation"]
        if allocation.positions:
            available.append("positions")
        themes: list[ThemeThesis] = []
        candidate_scores: dict[str, list[ThemeScore]] = {}

        themes_path = self.data_dir / "themes" / f"{date_str}_themes.json"
        if themes_path.exists():
            try:
                raw_themes = json.loads(themes_path.read_text())
                themes = [ThemeThesis.model_validate(item) for item in raw_themes]
                available.append("themes")
            except (OSError, json.JSONDecodeError, ValidationError, TypeError):
                pass

        scores_path = self.data_dir / "scores" / f"{date_str}_scores.json"
        if scores_path.exists():
            try:
                raw_scores = json.loads(scores_path.read_text())
                candidate_scores = {
                    theme: [ThemeScore.model_validate(item) for item in scores]
                    for theme, scores in raw_scores.items()
                }
                available.append("candidate_scores")
            except (OSError, json.JSONDecodeError, ValidationError, TypeError, AttributeError):
                pass

        ordered_available = [section for section in RUN_SNAPSHOT_SECTIONS if section in available]
        missing = [section for section in RUN_SNAPSHOT_SECTIONS if section not in available]
        return RunSnapshot(
            run_id=f"legacy-{date_str}",
            created_at=created_at,
            themes=themes,
            candidate_scores=candidate_scores,
            positions=allocation.positions,
            allocation=allocation,
            completeness=SnapshotCompleteness(
                source_format=SnapshotSourceFormat.LEGACY,
                is_complete=False,
                available_sections=ordered_available,
                missing_sections=missing,
            ),
        )


def _adapt_legacy_allocation(raw: dict, date_str: str) -> PortfolioAllocation:
    """Adapt older allocation JSON into an explicit-position compatibility model."""
    data = dict(raw)
    data.setdefault(
        "risk_profile",
        {"appetite": RiskAppetite.MODERATE.value, "time_horizon": TimeHorizon.MEDIUM.value},
    )
    data.setdefault(
        "macro_regime",
        {
            "regime": MacroRegimeType.NEUTRAL.value,
            "confidence": 5,
            "drivers": ["Legacy snapshot did not persist a macro regime."],
        },
    )
    data.setdefault("generated_at", datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=UTC))

    raw_positions = data.get("positions") or []
    if not raw_positions:
        return PortfolioAllocation.model_validate(data)

    # Positions are authoritative when a partially migrated legacy file has
    # grouped entries whose weights or vehicle strings no longer agree.
    positions = [dict(position) for position in raw_positions]
    core_pct = sum(
        float(position.get("weight_pct", 0))
        for position in positions
        if position.get("sleeve") == "core"
    )
    defensive_pct = sum(
        float(position.get("weight_pct", 0))
        for position in positions
        if position.get("sleeve") == "defensive"
    )
    cash_pct = sum(
        float(position.get("weight_pct", 0))
        for position in positions
        if position.get("sleeve") == "cash" or position.get("instrument_type") == "cash"
    )
    thematic = {
        str(position["ticker"]).strip().upper(): float(position.get("weight_pct", 0))
        for position in positions
        if position.get("sleeve") == "thematic"
    }

    theme_by_ticker: dict[str, str] = {}
    normalized_entries: list[dict] = []
    for raw_entry in data.get("entries", []):
        tickers = [
            ticker.strip().upper()
            for ticker in raw_entry.get("tickers", raw_entry.get("vehicle", "").split(","))
            if ticker.strip()
        ]
        matched = [ticker for ticker in tickers if ticker in thematic]
        if not matched:
            continue
        theme = raw_entry.get("theme", "Legacy thematic positions")
        theme_by_ticker.update({ticker: theme for ticker in matched})
        normalized_entries.append(
            {
                **raw_entry,
                "theme": theme,
                "vehicle": ", ".join(matched),
                "tickers": matched,
                "pct_allocation": sum(thematic[ticker] for ticker in matched),
                "entry_method": raw_entry.get("entry_method", "dca"),
                "rationale": raw_entry.get(
                    "rationale", "Converted from explicit legacy positions."
                ),
                "entry_prices": {
                    ticker: price
                    for ticker, price in (raw_entry.get("entry_prices") or {}).items()
                    if ticker.strip().upper() in matched
                },
            }
        )

    ungrouped = [ticker for ticker in thematic if ticker not in theme_by_ticker]
    if ungrouped:
        normalized_entries.append(
            {
                "theme": "Legacy thematic positions",
                "vehicle": ", ".join(ungrouped),
                "tickers": ungrouped,
                "vehicle_type": "stocks",
                "pct_allocation": sum(thematic[ticker] for ticker in ungrouped),
                "entry_method": "dca",
                "rationale": "Converted from explicit legacy positions.",
            }
        )

    data.update(
        {
            "positions": positions,
            "entries": normalized_entries,
            "core_pct": core_pct,
            "defensive_pct": defensive_pct,
            "cash_pct": cash_pct,
        }
    )
    return PortfolioAllocation.model_validate(data)


def _timestamp(value: datetime) -> float:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.timestamp()
