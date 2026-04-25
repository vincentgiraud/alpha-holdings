"""Prompt templates for autonomous theme discovery."""

THEME_DISCOVERY_PROMPT = """\
You are an elite thematic investment analyst. Given the macro briefing below, \
identify the most compelling bullish investment themes for a 3-5 year horizon.

MACRO BRIEFING:
{macro_briefing}

MACRO REGIME: {regime} (confidence: {regime_confidence}/10)

INSTRUCTIONS:
1. Identify 5-8 distinct investment themes with strong macro catalysts RIGHT NOW.
2. For EACH theme, map the full supply chain in tiers:
   - Tier 1 (demand_driver): the headline companies everyone knows. Typically fully priced.
   - Tier 2 (direct_enabler): companies that directly supply/serve Tier 1. Partially priced.
   - Tier 3 (picks_and_shovels): companies that enable the enablers — infrastructure, raw materials, \
services. Often still valued at boring sector multiples, NOT yet priced for theme exposure. \
THIS IS WHERE THE BEST OPPORTUNITIES ARE.
3. Discover companies GLOBALLY — US, European, Asian, Australian. Include small, mid, AND large caps.
4. For each company: explain its specific role and why it matters to the theme.
5. Focus on themes with VISIBLE catalysts now — legislation passed, contracts signed, tech deployed — \
not speculative "maybe someday" themes.

{focus_clause}

Return a JSON array of themes, each with:
- "name": theme name
- "thesis_summary": 2-3 sentence investment thesis
- "why_now": specific current catalysts (be concrete — cite events, not generalities)
- "bull_case": what happens if thesis plays out over 5 years
- "bear_case": what could invalidate or delay the thesis
- "confidence_score": 1-10
- "time_horizon": "3-5 years"
- "sub_themes": array of sub-themes, each with:
  - "name": sub-theme name
  - "description": brief description
  - "companies": array of companies, each with:
    - "ticker": stock ticker symbol
    - "exchange_suffix": exchange suffix if non-US (e.g. "L" for London, "PA" for Paris, \
"T" for Tokyo, "TW" for Taiwan, "KS" for Korea, "AX" for Australia, "TO" for Toronto) or null for US
    - "name": company name
    - "role_in_theme": specific role in the supply chain
    - "rationale": why this company specifically (not generic)
    - "market_cap_category": "small" | "mid" | "large"
    - "supply_chain_tier": "tier_1_demand_driver" | "tier_2_direct_enabler" | "tier_3_picks_and_shovels"
    - "sector": GICS sector (e.g. "Information Technology", "Industrials", "Energy", "Materials")

Return ONLY valid JSON. No commentary outside the JSON.
"""

THEME_DEPENDENCIES_PROMPT = """\
You are an investment strategist analyzing cross-theme dependencies.

Given these investment themes:
{themes_summary}

Identify causal dependency chains between themes. Which themes create demand \
that flows into other themes? For example:
- A compute-heavy theme drives demand for energy/power themes
- A defense spending theme amplifies cybersecurity themes
- An electrification theme shares infrastructure with EV themes

Return a JSON array of dependencies, each with:
- "source_theme": name of the theme that creates demand
- "target_theme": name of the theme that receives demand
- "relationship": "drives_demand_for" | "amplified_by" | "shares_infrastructure"
- "explanation": one sentence explaining the causal chain

Only include meaningful, non-obvious dependencies. Return ONLY valid JSON.
"""
