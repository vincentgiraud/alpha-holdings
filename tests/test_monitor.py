from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from click.testing import CliRunner

from alpha_holdings import monitor
from alpha_holdings.cli import cli
from alpha_holdings.models import (
    Company,
    Fundamentals,
    MarketCapCategory,
    MarketDataStatus,
    SubTheme,
    SupplyChainTier,
    ThemeThesis,
    ThesisStatus,
    ThesisUpdate,
)


def _company(ticker: str) -> Company:
    return Company(
        ticker=ticker,
        name=f"{ticker} Corp",
        role_in_theme="Test role",
        rationale="Test rationale",
        market_cap_category=MarketCapCategory.LARGE,
        supply_chain_tier=SupplyChainTier.TIER_2_DIRECT_ENABLER,
        sector="Technology",
    )


def _theme(*tickers: str, confidence: int = 8) -> ThemeThesis:
    return ThemeThesis(
        name="Grid",
        thesis_summary="Grid demand grows.",
        why_now="Demand is rising.",
        bull_case="Investment accelerates.",
        bear_case="Investment stalls.",
        confidence_score=confidence,
        sub_themes=[SubTheme(name="Power", description="Power", companies=[_company(t) for t in tickers])],
    )


def _fundamentals(ticker: str, drawdown: float = -20) -> Fundamentals:
    return Fundamentals(
        ticker=ticker,
        current_price=80,
        high_52w=100,
        drawdown_from_peak=drawdown,
        market_cap=1_000_000_000,
        avg_daily_volume=1_000_000,
        revenue_growth_cagr=10,
        gross_margin=40,
        operating_margin=15,
        forward_pe=20,
    )


def test_stabilized_and_recovering_precede_generic_on_sale(monkeypatch) -> None:
    monkeypatch.setattr("alpha_holdings.scoring._check_stabilized", lambda ticker: True)
    assert monitor.detect_opportunity("GRID", 8, _fundamentals("GRID")).signal_type.value == "stabilized"

    monkeypatch.setattr("alpha_holdings.scoring._check_stabilized", lambda ticker: False)
    monkeypatch.setattr("alpha_holdings.scoring._check_recovering", lambda ticker: True)
    assert monitor.detect_opportunity("GRID", 8, _fundamentals("GRID")).signal_type.value == "recovering"


def test_invalidated_theme_never_emits_a_buy_signal(monkeypatch) -> None:
    monkeypatch.setattr(monitor, "fetch", lambda *args, **kwargs: monitor.FundamentalsResult(
        ticker="GRID", status=MarketDataStatus.AVAILABLE, data=_fundamentals("GRID"),
        observed_at=datetime.now(UTC), as_of=datetime.now(UTC), age=0,
    ))

    scan = monitor.scan_opportunities(
        [_theme("GRID")],
        theme_statuses={"Grid": ThesisStatus.INVALIDATED},
    )

    assert [signal.signal_type.value for signal in scan.signals] == ["avoid"]


def test_weakened_theme_is_caution_not_a_buy(monkeypatch) -> None:
    monkeypatch.setattr(monitor, "fetch", lambda *args, **kwargs: monitor.FundamentalsResult(
        ticker="GRID", status=MarketDataStatus.AVAILABLE, data=_fundamentals("GRID"),
        observed_at=datetime.now(UTC), as_of=datetime.now(UTC), age=0,
    ))

    scan = monitor.scan_opportunities(
        [_theme("GRID")],
        theme_statuses={"Grid": ThesisStatus.WEAKENED},
    )

    assert [signal.signal_type.value for signal in scan.signals] == ["caution"]


def test_scan_reports_no_signal_and_unavailable_outcomes(monkeypatch) -> None:
    def fake_fetch(ticker: str, **kwargs):
        if ticker == "GOOD":
            return monitor.FundamentalsResult(
                ticker=ticker, status=MarketDataStatus.AVAILABLE,
                data=_fundamentals(ticker, drawdown=-2), observed_at=datetime.now(UTC),
                as_of=datetime.now(UTC), age=0,
            )
        return monitor.FundamentalsResult(
            ticker=ticker, status=MarketDataStatus.UNAVAILABLE,
            observed_at=datetime.now(UTC), reason="offline",
        )

    monkeypatch.setattr(monitor, "fetch", fake_fetch)
    scan = monitor.scan_opportunities([_theme("GOOD", "BAD")])
    assert {signal.signal_type.value for signal in scan.signals} == {"no_signal", "unavailable"}


def test_apply_thesis_updates_changes_confidence_and_removes_only_named_companies() -> None:
    updated, pending = monitor.apply_thesis_updates(
        [_theme("KEEP", "REMOVE")],
        [ThesisUpdate(
            theme_name="Grid", status=ThesisStatus.WEAKENED, reason="Demand delayed.",
            previous_confidence=8, new_confidence=5,
            companies_to_add=["UNVALIDATED"], companies_to_remove=["REMOVE"],
        )],
    )

    assert updated[0].confidence_score == 5
    assert [company.ticker for company in updated[0].all_companies] == ["KEEP"]
    assert pending == {"Grid": ["UNVALIDATED"]}


def test_funded_scope_excludes_unallocated_themes_and_companies() -> None:
    other = _theme("OTHER")
    other.name = "Other"
    scoped = monitor.funded_themes(
        [_theme("FUNDED", "NOT_FUNDED"), other],
        funded_by_theme={"grid": {"FUNDED"}},
    )

    assert [theme.name for theme in scoped] == ["Grid"]
    assert [company.ticker for company in scoped[0].all_companies] == ["FUNDED"]


def test_monitoring_events_are_append_only_and_linked_to_a_run(tmp_path) -> None:
    event = monitor.MonitoringEvent(
        event_id="event-1", source_run_id="run-1", updates=[], signals=[],
    )
    repository = monitor.MonitoringEventRepository(tmp_path)
    path = repository.save(event)

    assert path.exists()
    try:
        repository.save(event)
    except FileExistsError:
        pass
    else:
        raise AssertionError("monitoring events must not overwrite an existing event")


def test_monitor_command_scans_the_updated_funded_theme_and_persists_event(monkeypatch) -> None:
    captured: dict[str, object] = {}
    snapshot = SimpleNamespace(
        run_id="run-1",
        themes=[_theme("FUNDED", "UNFUNDED")],
        allocation=SimpleNamespace(entries=[SimpleNamespace(theme="Grid", tickers=["FUNDED"])]),
    )
    monkeypatch.setattr(
        "alpha_holdings.snapshots.RunSnapshotRepository",
        lambda: SimpleNamespace(load_latest=lambda: snapshot),
    )
    monkeypatch.setattr(monitor, "check_thesis", lambda theme: ThesisUpdate(
        theme_name=theme.name, status=ThesisStatus.INVALIDATED, reason="Broken.",
        previous_confidence=8, new_confidence=1,
    ))

    def fake_scan(themes, *, theme_statuses, **kwargs):
        captured["themes"] = themes
        captured["statuses"] = theme_statuses
        return monitor.OpportunityScanResult()

    monkeypatch.setattr(monitor, "scan_opportunities", fake_scan)
    monkeypatch.setattr(
        monitor,
        "MonitoringEventRepository",
        lambda: SimpleNamespace(save=lambda event: captured.setdefault("event", event)),
    )

    result = CliRunner().invoke(cli, ["monitor"])

    assert result.exit_code == 0
    assert [company.ticker for company in captured["themes"][0].all_companies] == ["FUNDED"]
    assert captured["themes"][0].confidence_score == 1
    assert captured["statuses"] == {"Grid": ThesisStatus.INVALIDATED}
    assert captured["event"].source_run_id == "run-1"
