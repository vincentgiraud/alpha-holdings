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
    FundamentalsResult,
    InstrumentMetadata,
    InstrumentPrice,
    InstrumentType,
    RUN_SNAPSHOT_SECTIONS,
    PortfolioAllocation,
    RunSnapshot,
    SnapshotCompleteness,
    SnapshotSourceFormat,
    ThemeScore,
    ThemeThesis,
)


def build_discovery_snapshot(
    *,
    themes: list[ThemeThesis],
    scores: dict[str, list[ThemeScore]],
    market_data: dict[str, FundamentalsResult],
    allocation: PortfolioAllocation,
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
    metadata: dict[str, InstrumentMetadata] = {}
    prices: dict[str, InstrumentPrice] = {}

    for theme_scores in scores.values():
        for score in theme_scores:
            ticker = score.ticker.upper()
            if (
                score.revenue_exposure_score is None
                or score.score_as_of is None
                or not score.evidence_sources
                or not score.scoring_provider
                or not score.scoring_model
                or not score.alignment_reasoning
                or not score.pricing_gap_reasoning
                or not score.revenue_exposure_reasoning
            ):
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
    return RunSnapshot(
        created_at=created_at or datetime.now(UTC),
        themes=themes,
        candidate_scores=scores,
        instrument_metadata=metadata,
        prices=prices,
        allocation=allocation,
        model_configuration=model_configuration or {},
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
        versioned: list[RunSnapshot] = []
        if self.runs_dir.exists():
            for path in self.runs_dir.glob("*.json"):
                snapshot = self._load_versioned(path)
                if snapshot is not None and snapshot.completeness.is_complete:
                    versioned.append(snapshot)

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
            allocation = PortfolioAllocation.model_validate_json(allocation_path.read_text())
            created_at = allocation.generated_at or datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=UTC)
        except (OSError, ValidationError, ValueError):
            return None

        available = ["allocation"]
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
            allocation=allocation,
            completeness=SnapshotCompleteness(
                source_format=SnapshotSourceFormat.LEGACY,
                is_complete=False,
                available_sections=ordered_available,
                missing_sections=missing,
            ),
        )


def _timestamp(value: datetime) -> float:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.timestamp()
