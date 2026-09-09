from datetime import UTC, datetime
import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from alpha_holdings.cli import cli
from alpha_holdings.models import (
    EntryMethod,
    InstrumentMetadata,
    InstrumentPosition,
    InstrumentPrice,
    InstrumentType,
    MacroRegime,
    MacroRegimeType,
    PortfolioAllocation,
    PortfolioSleeve,
    RiskAppetite,
    RiskProfile,
    RunSnapshot,
    SnapshotCompleteness,
    SnapshotSourceFormat,
    ThemeScore,
    ThemeThesis,
    TimeHorizon,
)
from alpha_holdings.snapshots import RunSnapshotRepository


def test_position_records_an_explicit_investable_instrument() -> None:
    position = InstrumentPosition(
        ticker="vwce.de",
        instrument_type=InstrumentType.ETF,
        sleeve=PortfolioSleeve.CORE,
        weight_pct=72.5,
        currency="eur",
        entry_price=118.42,
        price_timestamp=datetime(2026, 9, 8, 10, 30, tzinfo=UTC),
    )

    assert position.ticker == "VWCE.DE"
    assert position.currency == "EUR"
    assert position.weight_pct == 72.5
    assert position.price_timestamp == datetime(2026, 9, 8, 10, 30, tzinfo=UTC)


def test_position_rejects_a_partial_entry_price_observation() -> None:
    with pytest.raises(ValidationError):
        InstrumentPosition(
            ticker="VWCE.DE",
            instrument_type=InstrumentType.ETF,
            sleeve=PortfolioSleeve.CORE,
            weight_pct=72.5,
            currency="EUR",
            entry_price=118.42,
            price_timestamp=None,
        )


def test_position_requires_a_complete_entry_price_observation() -> None:
    with pytest.raises(ValidationError):
        InstrumentPosition(
            ticker="VWCE.DE",
            instrument_type=InstrumentType.ETF,
            sleeve=PortfolioSleeve.CORE,
            weight_pct=72.5,
            currency="EUR",
        )


@pytest.mark.parametrize(
    ("model", "values"),
    [
        (
            InstrumentMetadata,
            {"ticker": " ", "instrument_type": InstrumentType.STOCK, "currency": "USD"},
        ),
        (
            InstrumentPrice,
            {
                "ticker": " ",
                "price": 1,
                "currency": "USD",
                "observed_at": datetime(2026, 9, 8, tzinfo=UTC),
            },
        ),
    ],
)
def test_snapshot_instrument_records_reject_blank_tickers(model, values) -> None:
    with pytest.raises(ValidationError, match="ticker must not be empty"):
        model(**values)


def _allocation(
    positions: list[InstrumentPosition] | None = None,
) -> PortfolioAllocation:
    return PortfolioAllocation(
        risk_profile=RiskProfile(
            appetite=RiskAppetite.MODERATE,
            time_horizon=TimeHorizon.MEDIUM,
        ),
        macro_regime=MacroRegime(
            regime=MacroRegimeType.NEUTRAL,
            confidence=7,
            drivers=["Stable growth"],
            allocation_modifier=0.8,
        ),
        positions=positions or [],
        core_pct=100,
    )


def test_run_snapshot_round_trips_a_coherent_research_run() -> None:
    observed_at = datetime(2026, 9, 8, 10, 30, tzinfo=UTC)
    positions = [
        InstrumentPosition(
            ticker="VT",
            instrument_type=InstrumentType.ETF,
            sleeve=PortfolioSleeve.CORE,
            weight_pct=100,
            currency="USD",
            entry_price=125,
            price_timestamp=observed_at,
        )
    ]
    snapshot = RunSnapshot(
        created_at=observed_at,
        themes=[],
        candidate_scores={
            "Grid": [
                ThemeScore(
                    ticker="ETN",
                    fundamental_score=80,
                    thesis_alignment_score=90,
                    pricing_gap_score=70,
                    composite_score=80,
                    entry_method=EntryMethod.DCA,
                )
            ]
        },
        instrument_metadata={
            "ETN": InstrumentMetadata(
                ticker="ETN",
                instrument_type=InstrumentType.STOCK,
                currency="USD",
                name="Eaton",
            )
        },
        prices={
            "ETN": InstrumentPrice(
                ticker="ETN",
                price=355.25,
                currency="USD",
                observed_at=observed_at,
            )
        },
        positions=positions,
        allocation=_allocation(positions),
        model_configuration={"scoring_model": "example-model"},
        provenance={"fundamentals": "example-provider"},
        completeness=SnapshotCompleteness(
            source_format=SnapshotSourceFormat.VERSIONED,
            is_complete=True,
            available_sections=RunSnapshot.SECTIONS,
        ),
    )

    restored = RunSnapshot.model_validate_json(snapshot.model_dump_json())

    assert restored.schema_version == 1
    assert restored.candidate_scores["Grid"][0].ticker == "ETN"
    assert restored.instrument_metadata["ETN"].name == "Eaton"
    assert restored.prices["ETN"].price == 355.25
    assert restored.positions[0].sleeve is PortfolioSleeve.CORE
    assert restored.allocation.core_pct == 100
    assert restored.model_configuration["scoring_model"] == "example-model"
    assert restored.provenance["fundamentals"] == "example-provider"


def test_run_snapshot_rejects_positions_that_disagree_with_the_allocation() -> None:
    observed_at = datetime(2026, 9, 8, 10, 30, tzinfo=UTC)
    core = InstrumentPosition(
        ticker="VT",
        instrument_type=InstrumentType.ETF,
        sleeve=PortfolioSleeve.CORE,
        weight_pct=100,
        currency="USD",
        entry_price=125,
        price_timestamp=observed_at,
    )
    allocation = _allocation().model_copy(update={"positions": [core]})
    inconsistent = core.model_copy(update={"entry_price": 126})

    with pytest.raises(ValidationError, match="positions must match allocation positions"):
        RunSnapshot(allocation=allocation, positions=[inconsistent])


def test_repeated_same_day_runs_receive_unique_identifiers() -> None:
    created_at = datetime(2026, 9, 8, 10, 30, tzinfo=UTC)

    first = RunSnapshot(created_at=created_at, allocation=_allocation())
    second = RunSnapshot(created_at=created_at, allocation=_allocation())

    assert first.run_id != second.run_id
    assert first.run_id.startswith("20260908T103000")
    assert second.run_id.startswith("20260908T103000")


@pytest.mark.parametrize(
    "values",
    [
        {
            "source_format": SnapshotSourceFormat.VERSIONED,
            "is_complete": False,
            "available_sections": ["allocation"],
            "missing_sections": ["allocation"],
        },
        {
            "source_format": SnapshotSourceFormat.VERSIONED,
            "is_complete": False,
            "available_sections": ["allocation"],
            "missing_sections": [],
        },
    ],
)
def test_snapshot_rejects_misleading_completeness_metadata(values) -> None:
    with pytest.raises(ValidationError):
        SnapshotCompleteness(**values)


def test_repository_atomically_persists_and_loads_the_latest_complete_run(tmp_path) -> None:
    repository = RunSnapshotRepository(tmp_path)
    earlier = RunSnapshot(
        created_at=datetime(2026, 9, 8, 10, 30, tzinfo=UTC),
        allocation=_allocation(),
        provenance={"run": "earlier"},
    )
    later = RunSnapshot(
        created_at=datetime(2026, 9, 8, 11, 30, tzinfo=UTC),
        allocation=_allocation(),
        provenance={"run": "later"},
    )

    first_path = repository.save(earlier)
    second_path = repository.save(later)

    assert first_path != second_path
    assert repository.load_latest() == later
    assert repository.load(earlier.run_id) == earlier
    assert not list((tmp_path / "runs").glob("*.tmp"))


def test_repository_does_not_discover_an_interrupted_run_as_latest(tmp_path) -> None:
    repository = RunSnapshotRepository(tmp_path)
    complete = RunSnapshot(
        created_at=datetime(2026, 9, 8, 10, 30, tzinfo=UTC),
        allocation=_allocation(),
    )
    repository.save(complete)
    pending = tmp_path / "runs" / ".20260908T123000-pending.json.tmp"
    pending.write_text('{"schema_version": 1, "run_id": "pending"')

    assert repository.load_latest() == complete


def test_repository_never_overwrites_an_existing_run_identifier(tmp_path) -> None:
    repository = RunSnapshotRepository(tmp_path)
    snapshot = RunSnapshot(run_id="fixed-run", allocation=_allocation())
    repository.save(snapshot)

    with pytest.raises(FileExistsError):
        repository.save(snapshot)

    assert repository.load("../fixed-run") is None


def test_repository_adapts_a_legacy_three_file_snapshot_with_completeness_metadata(tmp_path) -> None:
    allocation_dir = tmp_path / "allocations"
    themes_dir = tmp_path / "themes"
    scores_dir = tmp_path / "scores"
    allocation_dir.mkdir()
    themes_dir.mkdir()
    scores_dir.mkdir()
    (allocation_dir / "20260908_allocation.json").write_text(
        _allocation().model_dump_json(indent=2)
    )
    (themes_dir / "20260908_themes.json").write_text("[]")
    (scores_dir / "20260908_scores.json").write_text(
        json.dumps(
            {
                "Grid": [
                    ThemeScore(
                        ticker="ETN",
                        fundamental_score=80,
                        thesis_alignment_score=90,
                        pricing_gap_score=70,
                        composite_score=80,
                    ).model_dump(mode="json")
                ]
            }
        )
    )

    snapshot = RunSnapshotRepository(tmp_path).load_latest()

    assert snapshot is not None
    assert snapshot.run_id == "legacy-20260908"
    assert snapshot.completeness.source_format is SnapshotSourceFormat.LEGACY
    assert snapshot.completeness.is_complete is False
    assert snapshot.completeness.available_sections == [
        "themes",
        "candidate_scores",
        "allocation",
    ]
    assert snapshot.completeness.missing_sections == [
        "etf_recommendations",
        "instrument_metadata",
        "prices",
        "positions",
        "model_configuration",
        "provenance",
    ]
    assert snapshot.candidate_scores["Grid"][0].ticker == "ETN"
    assert snapshot.allocation.core_pct == 100


def test_show_allocation_displays_a_versioned_snapshot() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        observed_at = datetime(2026, 9, 8, 10, 30, tzinfo=UTC)
        positions = [
            InstrumentPosition(
                ticker="VWCE.DE",
                instrument_type=InstrumentType.ETF,
                sleeve=PortfolioSleeve.CORE,
                weight_pct=100,
                currency="EUR",
                entry_price=118.42,
                price_timestamp=observed_at,
            )
        ]
        snapshot = RunSnapshot(
            created_at=observed_at,
            allocation=_allocation(positions),
            positions=positions,
        )
        RunSnapshotRepository().save(snapshot)

        result = runner.invoke(cli, ["show", "allocation"])

    assert result.exit_code == 0
    assert snapshot.run_id in result.output
    assert '"ticker": "VWCE.DE"' in result.output
    assert '"weight_pct": 100.0' in result.output


def test_show_allocation_still_displays_a_legacy_snapshot() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        allocation_dir = Path("data/allocations")
        allocation_dir.mkdir(parents=True)
        (allocation_dir / "20260908_allocation.json").write_text(
            _allocation().model_dump_json(indent=2)
        )

        result = runner.invoke(cli, ["show", "allocation"])

    assert result.exit_code == 0
    assert '"core_pct": 100.0' in result.output
    assert "No saved allocations" not in result.output


def test_show_themes_displays_themes_from_a_versioned_snapshot() -> None:
    runner = CliRunner()
    theme = ThemeThesis(
        name="Grid Modernization",
        thesis_summary="Electricity demand requires grid investment.",
        why_now="Load growth is accelerating.",
        bull_case="Investment remains elevated.",
        bear_case="Permitting delays projects.",
        confidence_score=8,
    )
    with runner.isolated_filesystem():
        RunSnapshotRepository().save(
            RunSnapshot(themes=[theme], allocation=_allocation())
        )

        result = runner.invoke(cli, ["show", "themes"])

    assert result.exit_code == 0
    assert "Grid Modernization" in result.output
    assert "No saved themes" not in result.output


def test_snapshot_rejects_top_level_positions_when_allocation_has_none() -> None:
    observed_at = datetime(2026, 9, 8, 10, 30, tzinfo=UTC)
    position = InstrumentPosition(
        ticker="VWCE.DE",
        instrument_type=InstrumentType.ETF,
        sleeve=PortfolioSleeve.CORE,
        weight_pct=100,
        currency="EUR",
        entry_price=118.42,
        price_timestamp=observed_at,
    )

    with pytest.raises(
        ValidationError,
        match="snapshot positions must match allocation positions",
    ):
        RunSnapshot(allocation=_allocation(), positions=[position])
