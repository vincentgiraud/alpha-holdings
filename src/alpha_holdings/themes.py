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
    MacroRegime,
    MacroSignal,
    ThemeDependency,
    ThemeThesis,
)
from alpha_holdings.prompts.theme_discovery import (
    THEME_DEPENDENCIES_PROMPT,
    THEME_DISCOVERY_PROMPT,
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

    log.info("Discovering themes via web search + reasoning...")
    raw = llm.respond_text(prompt, web_search=True)

    if not raw or not raw.strip():
        log.warning("Theme discovery returned empty response. Retrying without web search...")
        raw = llm.respond_text(prompt)

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
        raw = llm.respond_text(softer_prompt, web_search=True)

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

    log.info("Discovered %d themes.", len(themes))
    # Validate tickers
    for theme in themes:
        _validate_tickers(theme)
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
