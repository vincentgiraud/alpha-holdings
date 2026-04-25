"""CLI entry point for Alpha Holdings."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from rich.tree import Tree
from rich.panel import Panel
from rich.text import Text

from alpha_holdings.models import (
    EntryMethod,
    MacroRegimeType,
    OpportunityType,
    RiskAppetite,
    RiskProfile,
    SupplyChainTier,
    TimeHorizon,
    Urgency,
)

console = Console()

DISCLAIMER = (
    "[dim italic]NOT FINANCIAL ADVICE — this is an AI-assisted research tool. "
    "Verify all data before making investment decisions. "
    "Rebalancing may trigger taxable events. International tickers carry FX risk and spread costs.[/dim italic]"
)

UNCERTAINTY_NOTICE = (
    "[yellow]⚠ Before You Act[/yellow]\n"
    "• Scores combine quantitative data (fundamentals) and AI judgment (thesis, pricing gap, revenue exposure).\n"
    "  They are [bold]estimates, NOT precise measurements[/bold].\n"
    "• Thesis alignment & pricing gap scores are LLM-generated — not verified against analyst consensus.\n"
    "• This tool discovers themes from public news and LLM reasoning. It cannot detect insider information,\n"
    "  unpublished regulatory actions, or black swan events.\n"
    "• Your broad market core allocation is your protection against what this tool cannot see.\n"
    "• Items marked [dim italic]†[/dim italic] are AI-estimated."
)

REGIME_BADGE = {
    MacroRegimeType.BULL: "[bold green]🟢 BULL[/bold green]",
    MacroRegimeType.NEUTRAL: "[bold yellow]🟡 NEUTRAL[/bold yellow]",
    MacroRegimeType.BEAR: "[bold red]🔴 BEAR[/bold red]",
}


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging.")
@click.option("--debug", is_flag=True, help="Dump raw API responses to data/debug/.")
@click.pass_context
def cli(ctx: click.Context, verbose: bool, debug: bool) -> None:
    """Alpha Holdings — Autonomous thematic investment research."""
    ctx.ensure_object(dict)
    ctx.obj["debug"] = debug

    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")

    # Silence noisy third-party loggers — only show at DEBUG (-v)
    if not verbose:
        for noisy in ("azure", "httpx", "httpcore", "urllib3", "openai._base_client"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

    if debug:
        from alpha_holdings import llm as llm_mod
        llm_mod.DEBUG_DUMP = True
        console.print("[yellow]Debug mode: raw API responses will be saved to data/debug/[/yellow]")


@cli.command()
@click.option(
    "--risk",
    type=click.Choice(["conservative", "moderate", "aggressive"]),
    default="moderate",
    help="Risk appetite.",
)
@click.option(
    "--horizon",
    type=click.Choice(["3-5yr", "5-10yr", "10yr+"]),
    default="3-5yr",
    help="Investment time horizon.",
)
@click.option("--focus", multiple=True, help="Optional focus areas to bias discovery.")
@click.option("--base-currency", default="USD", help="Your base currency (for FX risk flags).")
@click.option("--capital", type=float, default=None, help="Total capital to invest (shows $ amounts in allocation).")
@click.option("--holdings", type=click.Path(exists=True), default=None, help="Path to holdings JSON file for overlap detection.")
def discover(risk: str, horizon: str, focus: tuple[str, ...], base_currency: str, capital: float | None, holdings: str | None) -> None:
    """Full pipeline: signals → themes → fundamentals → scoring → allocation."""
    from alpha_holdings import allocation as alloc_mod
    from alpha_holdings import etfs as etfs_mod
    from alpha_holdings import fundamentals as fund_mod
    from alpha_holdings import scoring as score_mod
    from alpha_holdings import signals as sig_mod
    from alpha_holdings import themes as theme_mod

    profile = RiskProfile(appetite=RiskAppetite(risk), time_horizon=TimeHorizon(horizon))
    focus_areas = list(focus) if focus else None

    # Step 1: Macro signals
    console.rule("[bold]Step 1: Collecting Macro Signals[/bold]")
    signals = sig_mod.collect_signals()
    regime = sig_mod.assess_regime()
    _print_regime(regime)
    _print_signals(signals)

    # Step 2: Theme discovery
    console.rule("[bold]Step 2: Discovering Themes[/bold]")
    themes = theme_mod.discover_themes(signals, regime, focus_areas=focus_areas)
    if not themes:
        console.print("[red]No themes discovered. Exiting.[/red]")
        return
    deps = theme_mod.map_dependencies(themes)
    _print_themes(themes)
    if deps:
        _print_dependencies(deps)

    # Step 3: Fundamentals
    console.rule("[bold]Step 3: Fetching Fundamentals[/bold]")
    all_tickers = []
    for t in themes:
        all_tickers.extend(c.full_ticker for c in t.all_companies)
    fund_data = fund_mod.fetch_batch(list(set(all_tickers)))
    console.print(f"Fetched fundamentals for {len(fund_data)} tickers.")

    # Step 3b: Quality filter — remove companies that fail minimum thresholds
    filtered_count = 0
    for t in themes:
        for sub in t.sub_themes:
            original = len(sub.companies)
            kept = []
            for c in sub.companies:
                f = fund_data.get(c.full_ticker)
                if f:
                    passes, reason = fund_mod.passes_quality_filter(f)
                    if passes:
                        kept.append(c)
                    else:
                        console.print(f"  [dim]Filtered out {c.full_ticker} ({c.name}): {reason}[/dim]")
                        filtered_count += 1
                else:
                    kept.append(c)  # keep if no data — score with defaults
            sub.companies = kept
    if filtered_count:
        console.print(f"[yellow]Filtered {filtered_count} companies below quality thresholds.[/yellow]")

    # Step 4: Scoring
    console.rule("[bold]Step 4: Scoring Companies[/bold]")
    scores: dict[str, list] = {}
    for t in themes:
        theme_scores = []
        for c in t.all_companies:
            f = fund_data.get(c.full_ticker)
            if f:
                s = score_mod.score_company(c, t, f, all_fundamentals=fund_data)
                theme_scores.append(s)
        scores[t.name] = theme_scores

    # Step 5: ETF mapping
    console.rule("[bold]Step 5: ETF Mapping[/bold]")
    etf_recs = {}
    for t in themes:
        etf_recs[t.name] = etfs_mod.find_etf(t)

    # Step 6: Allocation
    console.rule("[bold]Step 6: Portfolio Allocation[/bold]")
    allocation = alloc_mod.allocate(
        themes, scores, etf_recs, profile, regime,
        fund_data=fund_data, capital=capital,
    )

    # Display results
    console.rule("[bold]Results[/bold]")
    console.print(Panel(UNCERTAINTY_NOTICE, title="⚠ Uncertainty Notice", border_style="yellow"))
    console.print()
    for t in themes:
        _print_supply_chain_tree(t, scores.get(t.name, []), fund_data, etf_recs.get(t.name), base_currency=base_currency.upper())
    _print_allocation(allocation)

    # Holdings overlap analysis
    if holdings:
        from alpha_holdings.holdings import load_holdings, get_existing_exposure, analyze_overlap

        hp = load_holdings(holdings)
        if hp.holdings:
            existing = get_existing_exposure(hp)
            console.rule("[bold]Holdings Overlap Analysis[/bold]")
            has_overlap = False
            for entry in allocation.entries:
                tickers = [t.strip() for t in entry.vehicle.split(",")]
                overlaps = analyze_overlap(existing, tickers, entry.pct_allocation)
                for o in overlaps:
                    has_overlap = True
                    console.print(
                        f"  [yellow]⚠ {o['ticker']}[/yellow] — "
                        f"you already hold ~{o['existing_pct']:.1f}% via index funds. "
                        f"Adding [bold]{entry.theme}[/bold] brings effective weight to "
                        f"~{o['combined_pct']:.1f}%"
                    )
            if not has_overlap:
                console.print("  [green]No significant overlap between your holdings and recommended themes.[/green]")

    # Save
    theme_mod.save_themes(themes)
    _save_allocation(allocation)

    console.print()
    console.print(DISCLAIMER)


@cli.command()
@click.option("--theme", default=None, help="Re-evaluate a specific theme only.")
@click.option("--since", default=None, help="Date of allocation to track returns from (YYYYMMDD).")
def monitor(theme: str | None, since: str | None) -> None:
    """Course correction: re-evaluate saved themes against fresh signals."""
    from alpha_holdings import monitor as mon_mod
    from alpha_holdings import themes as theme_mod

    themes = theme_mod.load_latest_themes()
    if not themes:
        console.print("[yellow]No saved themes found. Run 'discover' first.[/yellow]")
        return

    if theme:
        themes = [t for t in themes if t.name.lower() == theme.lower()]

    console.rule("[bold]Course Correction[/bold]")
    updates = []
    for t in themes:
        console.print(f"Re-evaluating: [bold]{t.name}[/bold]...")
        update = mon_mod.check_thesis(t)
        updates.append(update)
        status_color = {
            "strengthened": "green",
            "unchanged": "white",
            "weakened": "yellow",
            "invalidated": "red",
        }
        color = status_color.get(update.status.value, "white")
        console.print(
            f"  [{color}]{update.status.value.upper()}[/{color}] "
            f"({update.previous_confidence} → {update.new_confidence}/10): {update.reason}"
        )

    # Rebalancing signals
    rebal = mon_mod.generate_rebalance_signals(themes, updates)
    if rebal:
        _print_rebalance_signals(rebal)

    # Opportunity scan
    console.rule("[bold]Opportunity Scan[/bold]")
    opps = mon_mod.scan_opportunities(themes)
    _print_opportunities(opps)

    # Sell discipline: track returns from a saved allocation
    if since:
        console.rule("[bold]Sell Discipline — Returns Since Allocation[/bold]")
        _print_sell_discipline(since)

    console.print()
    console.print(DISCLAIMER)


@cli.command()
def opportunities() -> None:
    """Quick scan for dip opportunities across funded themes."""
    from alpha_holdings import monitor as mon_mod
    from alpha_holdings import themes as theme_mod

    themes = theme_mod.load_latest_themes()
    if not themes:
        console.print("[yellow]No saved themes found. Run 'discover' first.[/yellow]")
        return

    console.rule("[bold]Opportunity Scan[/bold]")
    opps = mon_mod.scan_opportunities(themes)
    _print_opportunities(opps)
    console.print()
    console.print(DISCLAIMER)


@cli.command()
@click.argument("what", type=click.Choice(["themes", "allocation"]))
def show(what: str) -> None:
    """Display saved themes or allocation data."""
    if what == "themes":
        from alpha_holdings import themes as theme_mod

        themes = theme_mod.load_latest_themes()
        if themes:
            _print_themes(themes)
        else:
            console.print("[yellow]No saved themes.[/yellow]")
    elif what == "allocation":
        path = Path("data/allocations")
        if path.exists():
            files = sorted(path.glob("*.json"), reverse=True)
            if files:
                data = json.loads(files[0].read_text())
                console.print_json(json.dumps(data, indent=2))
            else:
                console.print("[yellow]No saved allocations.[/yellow]")
        else:
            console.print("[yellow]No saved allocations.[/yellow]")
    console.print()
    console.print(DISCLAIMER)


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def _print_regime(regime) -> None:
    badge = REGIME_BADGE.get(regime.regime, str(regime.regime.value))
    console.print(Panel(
        f"Macro Regime: {badge} (confidence: {regime.confidence}/10)\n"
        + "\n".join(f"  • {d}" for d in regime.drivers),
        title="Macro Environment",
    ))


def _print_signals(signals) -> None:
    if not signals:
        console.print("[yellow]No signals collected.[/yellow]")
        return
    table = Table(title="Macro Signals", show_lines=True)
    table.add_column("Tags", style="cyan", max_width=20)
    table.add_column("Headline", style="bold")
    table.add_column("Summary", max_width=60)
    for s in signals[:15]:
        tags = ", ".join(s.tags[:3]) if s.tags else ""
        table.add_row(tags, s.headline, s.summary)
    console.print(table)


def _print_themes(themes) -> None:
    table = Table(title="Discovered Themes", show_lines=True)
    table.add_column("Theme", style="bold cyan")
    table.add_column("Confidence", justify="center")
    table.add_column("Why Now", max_width=50)
    table.add_column("Companies", justify="center")
    for t in themes:
        conf_color = "green" if t.confidence_score >= 7 else "yellow" if t.confidence_score >= 5 else "red"
        table.add_row(
            t.name,
            f"[{conf_color}]{t.confidence_score}/10[/{conf_color}]",
            t.why_now[:100] + "..." if len(t.why_now) > 100 else t.why_now,
            str(len(t.all_companies)),
        )
    console.print(table)


def _print_dependencies(deps) -> None:
    table = Table(title="Theme Dependencies", show_lines=True)
    table.add_column("Source", style="bold")
    table.add_column("→", justify="center")
    table.add_column("Target", style="bold")
    table.add_column("Relationship")
    for d in deps:
        table.add_row(d.source_theme, "→", d.target_theme, d.explanation)
    console.print(table)


def _print_supply_chain_tree(theme, scores, fund_data, etf_rec, base_currency="USD") -> None:
    from alpha_holdings.config import get_accessibility, get_currency

    score_map = {s.ticker: s for s in scores}
    tree = Tree(f"[bold cyan]{theme.name}[/bold cyan] [dim](confidence: {theme.confidence_score}/10)[/dim]")

    for tier_label, tier_enum, style in [
        ("Tier 1 — Demand Drivers (typically priced in)", SupplyChainTier.TIER_1_DEMAND_DRIVER, "dim"),
        ("Tier 2 — Direct Enablers (partially priced)", SupplyChainTier.TIER_2_DIRECT_ENABLER, "yellow"),
        ("Tier 3 — Picks & Shovels (pricing gap) 🟢", SupplyChainTier.TIER_3_PICKS_AND_SHOVELS, "bold green"),
    ]:
        companies = [c for c in theme.all_companies if c.supply_chain_tier == tier_enum]
        if not companies:
            continue
        tier_branch = tree.add(f"[{style}]{tier_label}[/{style}]")
        for c in companies:
            sc = score_map.get(c.full_ticker)
            f = fund_data.get(c.full_ticker)
            pe_str = f"({f.forward_pe:.0f}x fwd P/E)" if f and f.forward_pe else ""

            # Currency & broker accessibility
            ccy = get_currency(c.exchange_suffix)
            access = get_accessibility(c.exchange_suffix)
            fx_tag = f" [red]⚠ FX[/red]" if ccy != base_currency else ""
            access_tag = ""
            if access == "exotic":
                access_tag = " [red]\\[exotic][/red]"
            elif access == "check_broker":
                access_tag = " [yellow]\\[check broker][/yellow]"
            ccy_str = f"({ccy}{fx_tag})"

            # Data-derived score in bold, LLM scores in dim italic with †
            score_parts = []
            if sc:
                score_parts.append(f"[bold]{sc.composite_score:.0f}[/bold]")
                score_parts.append(f"[dim italic]F:{sc.fundamental_score:.0f}[/dim italic]")
                score_parts.append(f"[dim italic]T:{sc.thesis_alignment_score:.0f}†[/dim italic]")
                score_parts.append(f"[dim italic]P:{sc.pricing_gap_score:.0f}†[/dim italic]")
            score_str = f"[{'/'.join(score_parts)}]" if score_parts else ""
            entry_str = ""
            if sc and sc.entry_method == EntryMethod.LUMP_SUM:
                entry_str = " 🟢 lump sum"
            elif sc and sc.entry_method == EntryMethod.DCA:
                entry_str = " ⚡ DCA"
            elif sc and sc.entry_method == EntryMethod.WAIT:
                entry_str = " 🔴 wait"
            tier_branch.add(f"[{style}]{c.full_ticker}[/{style}] {c.name} {ccy_str} {pe_str} {score_str}{entry_str}{access_tag}")

    if etf_rec and etf_rec.etf_ticker:
        tree.add(f"[blue]ETF: {etf_rec.etf_ticker} — {etf_rec.reasoning}[/blue]")

    console.print(tree)
    console.print("[dim italic]  † = AI-estimated (thesis alignment, pricing gap)[/dim italic]")
    console.print()


def _print_allocation(allocation) -> None:
    has_capital = allocation.capital is not None and allocation.capital > 0
    min_position = 200  # minimum viable position size

    table = Table(title="Model Portfolio Allocation", show_lines=True)
    table.add_column("Theme", style="bold")
    table.add_column("Vehicle", style="cyan")
    table.add_column("Allocation %", justify="right")
    if has_capital:
        table.add_column("Amount", justify="right")
    table.add_column("Entry", justify="center")
    table.add_column("Rationale", max_width=50)

    for e in allocation.entries:
        entry_style = {
            EntryMethod.LUMP_SUM: "[green]Lump Sum[/green]",
            EntryMethod.DCA: "[yellow]DCA[/yellow]",
            EntryMethod.WAIT: "[red]Wait[/red]",
        }
        row = [
            e.theme,
            e.vehicle,
            f"{e.pct_allocation:.1f}%",
        ]
        if has_capital:
            amount = allocation.capital * e.pct_allocation / 100
            amount_str = f"${amount:,.0f}"
            if amount < min_position:
                amount_str += " [red]⚠ min[/red]"
            row.append(amount_str)
        row.extend([
            entry_style.get(e.entry_method, str(e.entry_method.value)),
            e.rationale,
        ])
        table.add_row(*row)

    core_row = [
        "[dim]Broad Market Core[/dim]",
        "VT / VOO",
        f"{allocation.core_pct:.1f}%",
    ]
    if has_capital:
        core_row.append(f"${allocation.capital * allocation.core_pct / 100:,.0f}")
    core_row.extend(["[yellow]DCA[/yellow]", "Stability + diversification"])
    table.add_row(*core_row, style="dim")

    if allocation.defensive_pct > 0:
        def_row = [
            "[dim]Defensive[/dim]",
            "BND / TLT / GLD",
            f"{allocation.defensive_pct:.1f}%",
        ]
        if has_capital:
            def_row.append(f"${allocation.capital * allocation.defensive_pct / 100:,.0f}")
        def_row.extend(["[yellow]DCA[/yellow]", "Bear regime defensive allocation"])
        table.add_row(*def_row, style="dim")

    console.print(table)

    if has_capital:
        small_entries = [e for e in allocation.entries if allocation.capital * e.pct_allocation / 100 < min_position]
        if small_entries:
            console.print(
                f"[yellow]⚠ {len(small_entries)} positions below ${min_position} minimum. "
                f"With ${allocation.capital:,.0f} capital, consider using ETFs for small allocations "
                f"instead of individual stocks.[/yellow]"
            )


def _print_rebalance_signals(signals) -> None:
    console.rule("[bold]Rebalancing Signals[/bold]")
    for s in signals:
        urgency_style = {
            Urgency.HIGH: "[bold red]🔴 HIGH[/bold red]",
            Urgency.MEDIUM: "[yellow]🟡 MED[/yellow]",
            Urgency.LOW: "[green]🟢 LOW[/green]",
        }
        badge = urgency_style.get(s.urgency, str(s.urgency.value))
        action_str = s.action.value.replace("_", " ").upper()
        console.print(f"  {badge} {action_str}: {s.reason}")
        if s.from_asset:
            console.print(f"    From: {s.from_asset}")
        if s.to_asset:
            console.print(f"    To: {s.to_asset}")


def _print_opportunities(opps) -> None:
    if not opps:
        console.print("[dim]No dip opportunities detected.[/dim]")
        return
    table = Table(title="Dip Opportunities", show_lines=True)
    table.add_column("Ticker", style="bold")
    table.add_column("Signal", justify="center")
    table.add_column("Drawdown", justify="right")
    table.add_column("Thesis", justify="center")
    table.add_column("Action", max_width=60)
    for o in opps:
        signal_style = {
            OpportunityType.BUY_THE_DIP: "[bold green]BUY DIP[/bold green]",
            OpportunityType.CAUTION: "[yellow]CAUTION[/yellow]",
            OpportunityType.AVOID: "[red]AVOID[/red]",
        }
        table.add_row(
            o.ticker,
            signal_style.get(o.signal_type, str(o.signal_type.value)),
            f"{o.drawdown_pct:.1f}%" if o.drawdown_pct else "N/A",
            f"{o.thesis_confidence}/10",
            o.recommended_action,
        )
    console.print(table)


def _print_sell_discipline(since: str) -> None:
    """Load allocation from a date and show returns + sell signals."""
    import json as json_mod
    from alpha_holdings.fundamentals import fetch

    alloc_path = Path(f"data/allocations/{since}_allocation.json")
    if not alloc_path.exists():
        console.print(f"[yellow]No allocation found for date {since}. Available:[/yellow]")
        alloc_dir = Path("data/allocations")
        if alloc_dir.exists():
            for f in sorted(alloc_dir.glob("*.json")):
                console.print(f"  {f.stem}")
        return

    alloc_data = json_mod.loads(alloc_path.read_text())
    entries = alloc_data.get("entries", [])

    table = Table(title=f"Returns Since {since}", show_lines=True)
    table.add_column("Ticker", style="bold")
    table.add_column("Entry Price", justify="right")
    table.add_column("Current", justify="right")
    table.add_column("Return %", justify="right")
    table.add_column("Signal", max_width=50)

    for entry in entries:
        entry_prices = entry.get("entry_prices", {})
        for ticker, ep in entry_prices.items():
            if not ep or ep <= 0:
                continue
            try:
                f = fetch(ticker)
                cp = f.current_price
                if not cp:
                    continue
                ret = (cp - ep) / ep * 100
                ret_str = f"[green]+{ret:.1f}%[/green]" if ret >= 0 else f"[red]{ret:.1f}%[/red]"

                # Sell discipline signals
                signal = ""
                if ret > 50:
                    signal = "[yellow]📈 Up >50% — review whether to take profits[/yellow]"
                elif ret > 30 and f.drawdown_from_peak and f.drawdown_from_peak < -10:
                    signal = "[yellow]📉 Up >30% from entry but declining from peak — consider trimming[/yellow]"
                elif ret < -20:
                    signal = "[red]⚠ Down >20% — review thesis validity[/red]"

                table.add_row(
                    ticker,
                    f"${ep:.2f}",
                    f"${cp:.2f}",
                    ret_str,
                    signal,
                )
            except Exception:
                pass

    console.print(table)


def _save_allocation(allocation) -> None:
    from datetime import datetime

    path = Path("data/allocations")
    path.mkdir(parents=True, exist_ok=True)
    f = path / f"{datetime.utcnow().strftime('%Y%m%d')}_allocation.json"
    f.write_text(allocation.model_dump_json(indent=2))


if __name__ == "__main__":
    cli()
