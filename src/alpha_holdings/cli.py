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
def discover(risk: str, horizon: str, focus: tuple[str, ...], base_currency: str, capital: float | None) -> None:
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

    # Save
    theme_mod.save_themes(themes)
    _save_allocation(allocation)
    _save_scores(scores)

    console.print()
    console.print(DISCLAIMER)


@cli.command()
@click.argument("file", type=click.Path(exists=True))
def holdings(file: str) -> None:
    """Analyze overlap between your existing holdings and the latest saved allocation."""
    from alpha_holdings.holdings import load_holdings, get_existing_exposure, analyze_overlap

    # Load latest allocation
    alloc_path = Path("data/allocations")
    if not alloc_path.exists():
        console.print("[yellow]No saved allocations. Run 'discover' first.[/yellow]")
        return
    files = sorted(alloc_path.glob("*.json"), reverse=True)
    if not files:
        console.print("[yellow]No saved allocations. Run 'discover' first.[/yellow]")
        return

    import json as json_mod
    alloc_data = json_mod.loads(files[0].read_text())
    entries = alloc_data.get("entries", [])
    core_pct = alloc_data.get("core_pct", 60)
    capital = alloc_data.get("capital")

    hp = load_holdings(file)
    if not hp.holdings:
        console.print("[yellow]No holdings found in the file.[/yellow]")
        return

    existing = get_existing_exposure(hp)
    held_tickers = {h.ticker.upper() for h in hp.holdings}

    console.rule("[bold]Holdings Overlap Analysis[/bold]")
    has_overlap = False

    # Core overlap
    core_vehicles = {"VT", "VOO", "SPY", "IWDA.AS", "VWCE.DE"}
    core_overlap = held_tickers & core_vehicles
    if core_overlap:
        has_overlap = True
        core_amt = f" (${capital * core_pct / 100:,.0f})" if capital else ""
        for t in core_overlap:
            console.print(
                f"  [cyan]ℹ {t}[/cyan] — you already hold this. "
                f"Core allocation of {core_pct:.0f}%{core_amt} adds to your existing position."
            )

    # Thematic overlap
    for entry in entries:
        tickers = [t.strip() for t in entry.get("vehicle", "").split(",")]
        theme_name = entry.get("theme", "?")
        pct = entry.get("pct_allocation", 0)

        for t in tickers:
            if t.upper() in held_tickers:
                has_overlap = True
                console.print(
                    f"  [yellow]⚠ {t}[/yellow] — you already hold this directly. "
                    f"[bold]{theme_name}[/bold] adds {pct:.1f}% — "
                    f"review your total desired exposure before sizing."
                )

        overlaps = analyze_overlap(existing, tickers, pct)
        for o in overlaps:
            if o["ticker"].upper() in held_tickers:
                continue
            has_overlap = True
            console.print(
                f"  [yellow]⚠ {o['ticker']}[/yellow] — "
                f"you already hold ~{o['existing_pct']:.1f}% via index funds. "
                f"Adding [bold]{theme_name}[/bold] brings effective weight to "
                f"~{o['combined_pct']:.1f}%"
            )

    if not has_overlap:
        console.print("  [green]No significant overlap between your holdings and recommended themes.[/green]")

    console.print()
    console.print(DISCLAIMER)


@cli.command()
@click.option("--theme", "theme_filter", default=None, help="Filter to a specific theme.")
@click.option("--tier", type=click.Choice(["1", "2", "3"]), default=None, help="Filter to a specific supply chain tier.")
def explain(theme_filter: str | None, tier: str | None) -> None:
    """Show LLM reasoning behind each company's scores."""
    from alpha_holdings import themes as theme_mod
    from alpha_holdings.models import SupplyChainTier

    themes = theme_mod.load_latest_themes()
    if not themes:
        console.print("[yellow]No saved themes. Run 'discover' first.[/yellow]")
        return

    scores = _load_scores()
    if not scores:
        console.print("[yellow]No saved scores. Run 'discover' first to generate scores with reasoning.[/yellow]")
        return

    tier_map = {"1": SupplyChainTier.TIER_1_DEMAND_DRIVER, "2": SupplyChainTier.TIER_2_DIRECT_ENABLER, "3": SupplyChainTier.TIER_3_PICKS_AND_SHOVELS}
    tier_filter = tier_map.get(tier) if tier else None

    if theme_filter:
        themes = [t for t in themes if theme_filter.lower() in t.name.lower()]
        if not themes:
            console.print(f"[yellow]No theme matching '{theme_filter}'.[/yellow]")
            return

    console.rule("[bold]Score Explanations[/bold]")

    for t in themes:
        theme_scores = {s["ticker"]: s for s in scores.get(t.name, [])}
        companies = t.all_companies
        if tier_filter:
            companies = [c for c in companies if c.supply_chain_tier == tier_filter]
        if not companies:
            continue

        console.print(f"\n[bold cyan]{t.name}[/bold cyan] (confidence: {t.confidence_score}/10)")
        for c in companies:
            sc = theme_scores.get(c.full_ticker, {})
            if not sc:
                continue
            composite = sc.get("composite_score", "?")
            console.print(f"\n  [bold]{c.full_ticker}[/bold] {c.name} — score: {composite}")
            console.print(f"    Role: {c.role_in_theme}")
            ar = sc.get("alignment_reasoning")
            pr = sc.get("pricing_gap_reasoning")
            rr = sc.get("revenue_exposure_reasoning")
            if ar:
                console.print(f"    [dim]T†: {ar}[/dim]")
            if pr:
                console.print(f"    [dim]P†: {pr}[/dim]")
            if rr:
                console.print(f"    [dim]R†: {rr}[/dim]")
            if not (ar or pr or rr):
                console.print(f"    [dim]No reasoning available — run 'discover' again to generate.[/dim]")

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
@click.option("--from", "from_date", default=None, help="Start date YYYYMMDD (default: earliest allocation).")
@click.option("--to", "to_date", default=None, help="End date YYYYMMDD (default: today).")
@click.option("--benchmark", default="SPY", help="Benchmark ticker (default: SPY).")
def backtest(from_date: str | None, to_date: str | None, benchmark: str) -> None:
    """Compare historical theme allocations vs benchmark."""
    from alpha_holdings.backtest import list_snapshots, load_allocation, compute_returns

    snapshots = list_snapshots()
    if not snapshots:
        console.print("[yellow]No saved allocations found. Run 'discover' first.[/yellow]")
        return

    if not from_date:
        from_date = snapshots[0]
        console.print(f"Using earliest allocation: {from_date}")

    alloc = load_allocation(from_date)
    if not alloc:
        console.print(f"[red]No allocation found for {from_date}. Available: {', '.join(snapshots)}[/red]")
        return

    console.rule(f"[bold]Backtest: {from_date} → {to_date or 'today'}[/bold]")
    console.print(f"Benchmark: {benchmark}")
    console.print()

    results = compute_returns(alloc, from_date, to_date, benchmark)

    # Per-ticker table
    table = Table(title="Per-Ticker Returns", show_lines=True)
    table.add_column("Ticker", style="bold")
    table.add_column("Theme", max_width=30)
    table.add_column("Weight %", justify="right")
    table.add_column("Entry", justify="right")
    table.add_column("Current", justify="right")
    table.add_column("Return %", justify="right")

    for tr in sorted(results["ticker_returns"], key=lambda x: x.get("return_pct") or 0, reverse=True):
        ret = tr["return_pct"]
        ret_str = f"[green]+{ret:.1f}%[/green]" if ret and ret >= 0 else f"[red]{ret:.1f}%[/red]" if ret else "N/A"
        ep_str = f"${tr['entry_price']:.2f}" if tr["entry_price"] else "N/A"
        cp_str = f"${tr['current_price']:.2f}" if tr["current_price"] else "N/A"
        table.add_row(
            tr["ticker"],
            tr["theme"][:30],
            f"{tr['weight_pct']:.1f}%",
            ep_str,
            cp_str,
            ret_str,
        )
    console.print(table)

    # Summary
    console.print()
    summary = Table(title="Portfolio Summary", show_lines=True)
    summary.add_column("Metric", style="bold")
    summary.add_column("Value", justify="right")

    tr_str = f"{results['thematic_return']:+.2f}%" if results["thematic_return"] else "N/A"
    cr_str = f"{results['core_return']:+.2f}%" if results["core_return"] is not None else "N/A"
    bl_str = f"{results['blended_return']:+.2f}%" if results["blended_return"] else "N/A"
    bm_str = f"{results['benchmark_return']:+.2f}%" if results["benchmark_return"] is not None else "N/A"
    alpha = results.get("alpha")
    alpha_str = f"[green]+{alpha:.2f}%[/green]" if alpha and alpha > 0 else f"[red]{alpha:.2f}%[/red]" if alpha else "N/A"
    dd_str = f"{results['max_drawdown']:.1f}%"

    summary.add_row("Thematic return (weighted)", tr_str)
    summary.add_row("Core return (benchmark proxy)", cr_str)
    summary.add_row("Blended portfolio return", bl_str)
    summary.add_row(f"Benchmark ({benchmark})", bm_str)
    summary.add_row("Alpha (blended - benchmark)", alpha_str)
    summary.add_row("Max single-ticker drawdown", dd_str)
    console.print(summary)

    console.print()
    console.print(
        "[dim italic]This backtest uses the tool's historical recommendations. "
        "Past performance does not predict future results. "
        "Only works from when themes were first saved — cannot simulate past runs retroactively.[/dim italic]"
    )
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


def _save_scores(scores: dict[str, list]) -> None:
    """Save scores with reasoning to data/scores/ for the explain command."""
    import json as json_mod
    from datetime import datetime

    path = Path("data/scores")
    path.mkdir(parents=True, exist_ok=True)
    # Convert ThemeScore objects to dicts
    data = {}
    for theme_name, score_list in scores.items():
        data[theme_name] = [s.model_dump(mode="json") if hasattr(s, "model_dump") else s for s in score_list]
    f = path / f"{datetime.utcnow().strftime('%Y%m%d')}_scores.json"
    f.write_text(json_mod.dumps(data, indent=2, default=str))


def _load_scores() -> dict[str, list[dict]]:
    """Load the most recent saved scores."""
    import json as json_mod

    path = Path("data/scores")
    if not path.exists():
        return {}
    files = sorted(path.glob("*_scores.json"), reverse=True)
    if not files:
        return {}
    return json_mod.loads(files[0].read_text())


if __name__ == "__main__":
    cli()
