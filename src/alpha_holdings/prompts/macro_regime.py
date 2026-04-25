"""Prompt templates for macro signal collection and regime assessment."""

MACRO_SIGNALS_PROMPT = """\
You are a macro research analyst producing educational economic analysis. \
This is for research and informational purposes only, not financial advice. \
Search the web for the most important current events, \
trends, and developments relevant to the global economy over a 3-5 year horizon.

Focus on:
- Geopolitical events and policy shifts (trade policy, sanctions, regulation)
- Monetary policy and economic indicators (interest rates, inflation, GDP trends)
- Technology breakthroughs and adoption milestones
- Energy policy and infrastructure spending
- Supply chain shifts and reshoring trends
- Sector-specific catalysts (new legislation, large contracts, capacity buildouts)

Cover global scope: US, Europe, Asia, Australia, emerging markets.

Return a structured JSON array of signals, each with:
- "headline": concise headline
- "summary": 2-3 sentence explanation of why this matters for the economy and markets
- "source": source name or URL
- "date": date if known (YYYY-MM-DD) or null
- "tags": list of relevant sector/country tags

Return ONLY valid JSON. No commentary outside the JSON.
"""

MACRO_REGIME_PROMPT = """\
You are a macro research analyst. This is for educational and informational purposes only. \
Based on the current global economic conditions \
(search the web for the latest data on GDP growth, inflation, central bank policy, \
corporate earnings trends, credit conditions, and geopolitical risk), assess the \
overall economic environment.

Classify as one of:
- "bull": economic expansion, strong earnings, accommodative policy, risk-on sentiment
- "neutral": mixed signals, slowing growth but no recession, uncertain policy direction
- "bear": recession signals, earnings contraction, tightening conditions, elevated geopolitical crisis

Return a JSON object with:
- "regime": "bull" | "neutral" | "bear"
- "confidence": 1-10 (how confident in this assessment)
- "drivers": list of 3-5 key factors driving this assessment
- "allocation_modifier": float 0.0-1.0 (1.0 = favorable conditions, 0.5 = cautious conditions, etc.)

Return ONLY valid JSON. No commentary outside the JSON.
"""
