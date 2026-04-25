"""Macro signal collector using agentic web search."""

from __future__ import annotations

import json
import logging
from datetime import datetime

from alpha_holdings import llm
from alpha_holdings.config import NEWS_DOMAINS
from alpha_holdings.models import MacroRegime, MacroRegimeType, MacroSignal
from alpha_holdings.prompts.macro_regime import MACRO_REGIME_PROMPT, MACRO_SIGNALS_PROMPT

log = logging.getLogger(__name__)


def collect_signals(*, domain_filter: bool = True) -> list[MacroSignal]:
    """Collect current macro signals via agentic web search."""
    domains = NEWS_DOMAINS if domain_filter else None
    log.info("Collecting macro signals via web search...")

    raw = llm.respond_text(MACRO_SIGNALS_PROMPT, web_search=True, domain_filter=domains)
    try:
        data = json.loads(_extract_json(raw))
    except (json.JSONDecodeError, ValueError):
        log.warning("Failed to parse macro signals JSON, returning empty list.")
        return []

    signals: list[MacroSignal] = []
    for item in data:
        try:
            signals.append(MacroSignal(**item))
        except Exception:
            log.warning("Skipping malformed signal: %s", item)
    log.info("Collected %d macro signals.", len(signals))
    return signals


def assess_regime(*, domain_filter: bool = True) -> MacroRegime:
    """Assess the current macro regime via agentic web search."""
    domains = NEWS_DOMAINS if domain_filter else None
    log.info("Assessing macro regime via web search...")

    raw = llm.respond_text(MACRO_REGIME_PROMPT, web_search=True, domain_filter=domains)
    try:
        data = json.loads(_extract_json(raw))
        return MacroRegime(**data)
    except Exception:
        log.warning("Failed to parse macro regime, defaulting to neutral.")
        return MacroRegime(
            regime=MacroRegimeType.NEUTRAL,
            confidence=5,
            drivers=["Unable to assess — defaulting to neutral"],
            allocation_modifier=0.8,
        )


def get_macro_briefing(signals: list[MacroSignal]) -> str:
    """Format signals into a text briefing for theme discovery prompts."""
    if not signals:
        return "No current macro signals available."
    lines = []
    for s in signals[:20]:  # cap to avoid context overflow
        tags = ", ".join(s.tags) if s.tags else "general"
        lines.append(f"- [{tags}] {s.headline}: {s.summary}")
    return "\n".join(lines)


def _extract_json(text: str) -> str:
    """Extract JSON from LLM output that may contain markdown fences or preamble."""
    text = text.strip()

    # Handle ```json ... ``` or ``` ... ```
    if "```" in text:
        # Find the first opening fence
        start = text.index("```")
        first_newline = text.index("\n", start)
        last_fence = text.rfind("```")
        if last_fence > first_newline:
            text = text[first_newline + 1 : last_fence].strip()
            return text

    # Handle preamble text before JSON array or object
    # Find the first [ or { which starts the actual JSON
    for i, ch in enumerate(text):
        if ch in ("[", "{"):
            # Find the matching closing bracket from the end
            close = "]" if ch == "[" else "}"
            last = text.rfind(close)
            if last > i:
                return text[i : last + 1]

    return text
