"""Prompt templates for thesis validation and scoring."""

THESIS_ALIGNMENT_PROMPT = """\
You are an investment analyst evaluating how well a company is positioned \
to benefit from a specific investment theme over 3-5 years.

THEME: {theme_name}
THESIS: {thesis_summary}
WHY NOW: {why_now}

COMPANY: {company_name} ({ticker})
ROLE IN THEME: {role_in_theme}
SUPPLY CHAIN TIER: {tier}
SECTOR: {sector}

FUNDAMENTALS:
{fundamentals_summary}

Evaluate this company's thesis alignment considering:
1. Catalyst proximity — how directly does the company benefit from the theme's catalysts?
2. Competitive moat — does the company have a durable advantage within the theme?
3. Management execution — is the company actively positioning for this theme?
4. Regulatory environment — tailwinds or headwinds?
5. Revenue dependency — what % of revenue is theme-related (estimate)?

Return a JSON object with:
- "alignment_score": 0-100 (how well positioned for this theme)
- "reasoning": 2-3 sentences explaining the score
- "key_risk": single biggest risk to this company's theme positioning

Return ONLY valid JSON.
"""

PRICING_GAP_PROMPT = """\
You are a valuation analyst. Assess whether the market has priced in \
this company's exposure to the investment theme.

COMPANY: {company_name} ({ticker})
SECTOR: {sector}
SUPPLY CHAIN TIER: {tier}
ROLE IN THEME: {role_in_theme}

CURRENT VALUATION:
- Forward P/E: {forward_pe}
- EV/EBITDA: {ev_to_ebitda}
- Sector median forward P/E: {sector_median_pe} (approximate)

QUESTION: Is this company still valued like a typical {sector} company, \
or has the market already repriced it as a "{theme_name}" play?

Return a JSON object with:
- "pricing_gap_score": 0-100 (100 = massive unrecognized theme exposure, market hasn't repriced; \
0 = fully priced for the theme, no gap)
- "reasoning": 2-3 sentences
- "market_perception": what the market currently sees this company as

Return ONLY valid JSON.
"""
