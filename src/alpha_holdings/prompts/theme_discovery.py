"""Prompt templates for autonomous theme discovery."""

THEME_DISCOVERY_PROMPT = """\
You are a thematic research analyst producing educational market analysis. \
This is for research and informational purposes only, not financial advice. \
Given the macro briefing below, identify the most significant emerging \
economic and industrial themes over a 3-5 year horizon.

MACRO BRIEFING:
{macro_briefing}

MACRO REGIME: {regime} (confidence: {regime_confidence}/10)

INSTRUCTIONS:
1. Identify 5-8 distinct structural themes supported by strong macro catalysts visible today.
2. For EACH theme, map the COMPLETE supply chain across ALL of these layers — do not skip any:
   - Tier 1 (demand_driver): the well-known companies at the center of the theme. Typically widely followed.
   - Tier 2 (direct_enabler): companies that directly supply or serve Tier 1. Less widely followed.
   - Tier 3 (picks_and_shovels): companies that enable the enablers — infrastructure, raw materials, \
services. Often overlooked and valued at standard sector multiples rather than theme multiples.
3. For each theme, ensure you cover EVERY relevant supply chain layer. Common layers include (not all apply to every theme):
   - Core technology / IP holders
   - Equipment & tooling suppliers
   - Component & materials suppliers
   - Energy & power suppliers (including nuclear, renewables, grid)
   - Physical infrastructure (data centers, real estate, logistics)
   - Networking & connectivity
   - Services & integration
   - Raw materials & mining
4. Aim for 5-8 companies per sub-theme. Cast a WIDE net — it's better to include a borderline \
company than to miss an important one. The scoring engine downstream will filter weak picks.
5. Include companies GLOBALLY — US, European, Asian, Australian. Include small, mid, AND large caps.
6. For each company: explain its specific role and why it matters to the theme.
7. Focus on themes with VISIBLE catalysts now — legislation passed, contracts signed, tech deployed — \
not speculative themes.

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

THEME_GAP_CHECK_PROMPT = """\
You are a thematic research analyst reviewing a first-pass theme discovery for COMPLETENESS. \
This is for educational research only, not financial advice.

A first pass identified these themes and companies:

{pass1_summary}

YOUR TASK: Identify GAPS in the supply chain coverage. For each theme, check whether these \
critical supply chain layers are covered:

SUPPLY CHAIN LAYERS TO CHECK:
- Core technology / IP holders (e.g. chipmakers, software platforms)
- Equipment & tooling (e.g. lithography, testing, manufacturing equipment)
- Components & advanced materials (e.g. specialty chemicals, substrates, optics)
- Energy & power (nuclear, gas, renewables, grid infrastructure, utilities)
- Physical infrastructure (data centers, REITs, cooling, construction)
- Networking & connectivity (switches, routers, fiber, custom silicon)
- Services & integration (cloud, consulting, managed services)
- Raw materials & mining (uranium, copper, rare earths, lithium)

For each gap found, suggest SPECIFIC companies (with tickers) that should have been included. \
Search the web for the most relevant companies in each gap area.

Also check if any ENTIRE THEMES were missed. Common high-conviction themes in the current \
macro environment that may be absent:
- Nuclear energy renaissance (for AI data center power)
- Data center infrastructure buildout
- AI custom silicon & networking
- Defense & cybersecurity
- Nearshoring / friend-shoring
- Water scarcity & infrastructure
- Aging demographics & healthcare

Return a JSON array of additional companies to add. Each entry:
- "theme_name": which existing theme to add to (use EXACT name from above), or "NEW: <theme name>" for a new theme
- "sub_theme_name": sub-theme name
- "sub_theme_description": brief description
- "companies": array of companies, each with:
    - "ticker": stock ticker
    - "exchange_suffix": exchange suffix or null for US
    - "name": company name
    - "role_in_theme": specific role
    - "rationale": why this company fills a gap
    - "market_cap_category": "small" | "mid" | "large"
    - "supply_chain_tier": "tier_1_demand_driver" | "tier_2_direct_enabler" | "tier_3_picks_and_shovels"
    - "sector": GICS sector

Be aggressive — include 5-8 companies per gap. It is better to over-include than to miss \
an important player. The scoring engine will filter weak picks downstream.

Return ONLY valid JSON. No commentary outside the JSON.
"""
