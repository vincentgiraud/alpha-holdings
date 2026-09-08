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
    RUN_SNAPSHOT_SECTIONS,
    PortfolioAllocation,
    RunSnapshot,
    SnapshotCompleteness,
    SnapshotSourceFormat,
    ThemeScore,
    ThemeThesis,
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
        """Load the newest usable snapshot across versioned and legacy storage."""
        candidates: list[RunSnapshot] = []
        if self.runs_dir.exists():
            for path in self.runs_dir.glob("*.json"):
                snapshot = self._load_versioned(path)
                if snapshot is not None and snapshot.completeness.is_complete:
                    candidates.append(snapshot)

        allocation_dir = self.data_dir / "allocations"
        if allocation_dir.exists():
            for path in allocation_dir.glob("*_allocation.json"):
                date_str = path.name.removesuffix("_allocation.json")
                snapshot = self._load_legacy(date_str)
                if snapshot is not None:
                    candidates.append(snapshot)

        if not candidates:
            return None
        return max(candidates, key=lambda item: _timestamp(item.created_at))

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
