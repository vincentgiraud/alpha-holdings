"""Prompt templates for course correction and rebalancing."""

COURSE_CORRECTION_PROMPT = """\
You are an investment analyst re-evaluating an existing investment thesis \
against fresh macro signals.

ORIGINAL THEME: {theme_name}
ORIGINAL THESIS: {thesis_summary}
ORIGINAL CATALYSTS: {why_now}
ORIGINAL CONFIDENCE: {confidence}/10
DISCOVERED ON: {discovered_date}

FRESH MACRO SIGNALS (search the web for the latest developments):
Evaluate whether the thesis has strengthened, remained unchanged, weakened, \
or been invalidated since it was originally identified.

Consider:
1. Have the original catalysts progressed, stalled, or reversed?
2. Are there NEW catalysts that strengthen or weaken the thesis?
3. Have competitive dynamics shifted?
4. Have any regulatory/policy changes affected the thesis?
5. Have any companies in the theme over/underperformed expectations?

Return a JSON object with:
- "status": "strengthened" | "unchanged" | "weakened" | "invalidated"
- "reason": 2-3 sentences explaining what changed (or didn't)
- "new_confidence": 1-10
- "companies_to_add": list of ticker strings to consider adding
- "companies_to_remove": list of ticker strings that no longer fit

Return ONLY valid JSON.
"""
