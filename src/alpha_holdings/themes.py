"""Autonomous theme discovery with supply chain tiering."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

import yfinance as yf

from alpha_holdings import llm
from alpha_holdings.models import (
    Company,
    MacroRegime,
    MacroSignal,
    SubTheme,
    ThemeDependency,
    ThemeThesis,
)
from alpha_holdings.prompts.theme_discovery import (
    THEME_DEPENDENCIES_PROMPT,
    THEME_DISCOVERY_PROMPT,
    THEME_GAP_CHECK_PROMPT,
)
from alpha_holdings.signals import _extract_json, get_macro_briefing

log = logging.getLogger(__name__)

DATA_DIR = Path("data/themes")


def discover_themes(
    signals: list[MacroSignal],
    regime: MacroRegime,
    *,
    focus_areas: list[str] | None = None,
) -> list[ThemeThesis]:
    """Discover investment themes from macro signals via agentic web search."""
    briefing = get_macro_briefing(signals)
    focus_clause = ""
    if focus_areas:
        areas = ", ".join(focus_areas)
        focus_clause = f"OPTIONAL FOCUS AREAS (bias toward but don't limit to): {areas}"

    prompt = THEME_DISCOVERY_PROMPT.format(
        macro_briefing=briefing,
        regime=regime.regime.value,
        regime_confidence=regime.confidence,
        focus_clause=focus_clause,
    )

    log.info("Discovering themes via web search + reasoning (high)...")
    raw = llm.respond_text(prompt, web_search=True, reasoning="high")

    if not raw or not raw.strip():
        log.warning("Theme discovery returned empty response. Retrying without web search...")
        raw = llm.respond_text(prompt, reasoning="high")

    # Detect content safety refusal and retry with softer framing
    refusal_phrases = ("i'm sorry", "i cannot", "i can't", "unable to assist", "cannot assist")
    if raw and any(phrase in raw.lower()[:200] for phrase in refusal_phrases):
        log.warning("Model refused the request (content safety). Retrying with softer framing...")
        softer_prompt = (
            "For educational and research purposes only — not financial advice. "
            "As an economic research analyst, analyze current global macro trends "
            "and identify 5-8 significant structural economic themes supported by "
            "recent developments. For each theme, list relevant publicly traded "
            "companies across the supply chain (globally). "
            "Return ONLY a JSON array in the same format as described below.\n\n"
            + prompt
        )
        raw = llm.respond_text(softer_prompt, web_search=True, reasoning="high")

    try:
        data = json.loads(_extract_json(raw))
    except (json.JSONDecodeError, ValueError) as exc:
        log.error("Failed to parse theme discovery JSON: %s", exc)
        log.error("Raw response (first 500 chars): %s", raw[:500] if raw else "<empty>")
        return []

    themes: list[ThemeThesis] = []
    now = datetime.utcnow()
    for item in data:
        try:
            theme = ThemeThesis(**item, discovered_at=now)
            themes.append(theme)
        except Exception as exc:
            log.warning("Skipping malformed theme: %s — %s", item.get("name", "?"), exc)

    log.info("Discovered %d themes (pass 1).", len(themes))
    # Validate tickers from pass 1
    for theme in themes:
        _validate_tickers(theme)

    # Pass 2: Gap check — find missing supply chain layers and companies
    log.info("Running gap-check pass 2...")
    gap_companies = _gap_check(themes)
    if gap_companies:
        _merge_gap_companies(themes, gap_companies)
        # Validate the newly added tickers
        for theme in themes:
            _validate_tickers(theme)
        log.info("After gap-check merge: %d themes, %d total companies.",
                 len(themes), sum(len(t.all_companies) for t in themes))

    return themes


def map_dependencies(themes: list[ThemeThesis]) -> list[ThemeDependency]:
    """Map causal dependencies between discovered themes."""
    summary = "\n".join(
        f"- {t.name}: {t.thesis_summary}" for t in themes
    )
    prompt = THEME_DEPENDENCIES_PROMPT.format(themes_summary=summary)

    log.info("Mapping theme dependencies...")
    raw = llm.respond_text(prompt, mini=True)

    try:
        data = json.loads(_extract_json(raw))
    except (json.JSONDecodeError, ValueError):
        log.warning("Failed to parse theme dependencies.")
        return []

    deps: list[ThemeDependency] = []
    for item in data:
        try:
            deps.append(ThemeDependency(**item))
        except Exception:
            pass
    log.info("Found %d theme dependencies.", len(deps))
    return deps


def _gap_check(themes: list[ThemeThesis]) -> list[dict]:
    """Pass 2: Ask the LLM to find gaps in the first-pass discovery."""
    # Build a summary of what pass 1 found
    lines = []
    for t in themes:
        lines.append(f"\n## {t.name} (confidence: {t.confidence_score}/10)")
        lines.append(f"Thesis: {t.thesis_summary}")
        for st in t.sub_themes:
            lines.append(f"  Sub-theme: {st.name}")
            for c in st.companies:
                lines.append(f"    - {c.name} ({c.full_ticker}) [{c.supply_chain_tier.value}] — {c.role_in_theme}")

    pass1_summary = "\n".join(lines)

    prompt = THEME_GAP_CHECK_PROMPT.format(pass1_summary=pass1_summary)

    log.info("Gap-check: asking LLM to find missing companies and layers (high reasoning)...")
    raw = llm.respond_text(prompt, web_search=True, reasoning="high")

    if not raw or not raw.strip():
        log.warning("Gap-check returned empty response.")
        return []

    # Handle refusal
    refusal_phrases = ("i'm sorry", "i cannot", "i can't", "unable to assist", "cannot assist")
    if any(phrase in raw.lower()[:200] for phrase in refusal_phrases):
        log.warning("Gap-check refused by model. Skipping pass 2.")
        return []

    try:
        data = json.loads(_extract_json(raw))
        if not isinstance(data, list):
            data = [data]
        log.info("Gap-check found %d gap entries to merge.", len(data))
        return data
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning("Failed to parse gap-check JSON: %s", exc)
        return []


def _merge_gap_companies(themes: list[ThemeThesis], gap_entries: list[dict]) -> None:
    """Merge gap-check companies into existing themes, deduplicating by ticker."""
    now = datetime.utcnow()

    # Index existing themes by name (lowercase for fuzzy matching)
    theme_map = {t.name.lower(): t for t in themes}

    # Collect all existing tickers for dedup
    existing_tickers: set[str] = set()
    for t in themes:
        for c in t.all_companies:
            existing_tickers.add(c.full_ticker)

    new_themes: dict[str, ThemeThesis] = {}  # for "NEW: ..." entries

    for entry in gap_entries:
        theme_name = entry.get("theme_name", "")
        sub_name = entry.get("sub_theme_name", "Unknown")
        sub_desc = entry.get("sub_theme_description", "")
        companies_data = entry.get("companies", [])

        if not companies_data:
            continue

        # Parse companies, dedup
        new_companies: list[Company] = []
        for c_data in companies_data:
            try:
                company = Company(**c_data)
                if company.full_ticker not in existing_tickers:
                    new_companies.append(company)
                    existing_tickers.add(company.full_ticker)
            except Exception as exc:
                log.debug("Skipping malformed gap company: %s", exc)

        if not new_companies:
            continue

        new_sub = SubTheme(name=sub_name, description=sub_desc, companies=new_companies)

        # Handle new themes
        if theme_name.upper().startswith("NEW:"):
            real_name = theme_name[4:].strip()
            key = real_name.lower()
            if key in new_themes:
                new_themes[key].sub_themes.append(new_sub)
            else:
                new_themes[key] = ThemeThesis(
                    name=real_name,
                    thesis_summary=f"Gap-check discovered theme: {real_name}",
                    why_now="Identified as a missing theme during supply chain gap analysis.",
                    bull_case="Strong structural tailwinds if thesis plays out.",
                    bear_case="May overlap with existing themes.",
                    confidence_score=7,
                    time_horizon="3-5 years",
                    sub_themes=[new_sub],
                    discovered_at=now,
                )
            continue

        # Find matching existing theme
        matched = None
        for key, t in theme_map.items():
            if theme_name.lower() in key or key in theme_name.lower():
                matched = t
                break

        if matched:
            # Try to find matching sub-theme
            sub_matched = False
            for existing_sub in matched.sub_themes:
                if sub_name.lower() in existing_sub.name.lower() or existing_sub.name.lower() in sub_name.lower():
                    existing_sub.companies.extend(new_companies)
                    sub_matched = True
                    break
            if not sub_matched:
                matched.sub_themes.append(new_sub)
            log.info("Merged %d gap companies into theme '%s' / sub-theme '%s'.",
                     len(new_companies), matched.name, sub_name)
        else:
            # Theme name doesn't match anything — create as new
            key = theme_name.lower()
            if key in new_themes:
                new_themes[key].sub_themes.append(new_sub)
            else:
                new_themes[key] = ThemeThesis(
                    name=theme_name,
                    thesis_summary=f"Gap-check discovered theme: {theme_name}",
                    why_now="Identified during supply chain gap analysis.",
                    bull_case="Strong structural tailwinds if thesis plays out.",
                    bear_case="May overlap with existing themes.",
                    confidence_score=7,
                    time_horizon="3-5 years",
                    sub_themes=[new_sub],
                    discovered_at=now,
                )

    # Append any new themes
    for nt in new_themes.values():
        themes.append(nt)
        log.info("Added new gap-check theme: '%s' with %d companies.",
                 nt.name, len(nt.all_companies))


def _validate_tickers(theme: ThemeThesis) -> None:
    """Validate LLM-suggested tickers via yfinance; drop invalid ones."""
    for sub in theme.sub_themes:
        valid = []
        for company in sub.companies:
            ticker_str = company.full_ticker
            try:
                info = yf.Ticker(ticker_str).info
                if info and info.get("regularMarketPrice") is not None:
                    valid.append(company)
                else:
                    log.warning("Dropping invalid ticker: %s (%s)", ticker_str, company.name)
            except Exception:
                log.warning("Dropping unresolvable ticker: %s (%s)", ticker_str, company.name)
        sub.companies = valid


def save_themes(themes: list[ThemeThesis]) -> Path:
    """Persist discovered themes to data/themes/."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.utcnow().strftime("%Y%m%d")
    path = DATA_DIR / f"{date_str}_themes.json"
    data = [t.model_dump(mode="json") for t in themes]
    path.write_text(json.dumps(data, indent=2, default=str))
    log.info("Saved %d themes to %s", len(themes), path)
    return path


def load_latest_themes() -> list[ThemeThesis]:
    """Load the most recent saved themes."""
    if not DATA_DIR.exists():
        return []
    files = sorted(DATA_DIR.glob("*_themes.json"), reverse=True)
    if not files:
        return []
    data = json.loads(files[0].read_text())
    return [ThemeThesis(**item) for item in data]
