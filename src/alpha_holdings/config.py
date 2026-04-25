"""Risk profile configuration and allocation defaults."""

from __future__ import annotations

from alpha_holdings.models import RiskAppetite, TimeHorizon, RiskProfile

# ---------------------------------------------------------------------------
# Allocation matrix: (appetite, horizon) → thematic %
# ---------------------------------------------------------------------------

THEMATIC_PCT: dict[tuple[RiskAppetite, TimeHorizon], float] = {
    (RiskAppetite.CONSERVATIVE, TimeHorizon.SHORT): 0.30,
    (RiskAppetite.CONSERVATIVE, TimeHorizon.MEDIUM): 0.20,
    (RiskAppetite.CONSERVATIVE, TimeHorizon.LONG): 0.15,
    (RiskAppetite.MODERATE, TimeHorizon.SHORT): 0.50,
    (RiskAppetite.MODERATE, TimeHorizon.MEDIUM): 0.40,
    (RiskAppetite.MODERATE, TimeHorizon.LONG): 0.30,
    (RiskAppetite.AGGRESSIVE, TimeHorizon.SHORT): 0.75,
    (RiskAppetite.AGGRESSIVE, TimeHorizon.MEDIUM): 0.55,
    (RiskAppetite.AGGRESSIVE, TimeHorizon.LONG): 0.40,
}

# Max single-theme concentration by appetite
MAX_THEME_PCT: dict[RiskAppetite, float] = {
    RiskAppetite.CONSERVATIVE: 0.10,
    RiskAppetite.MODERATE: 0.15,
    RiskAppetite.AGGRESSIVE: 0.25,
}

# Minimum funded themes by appetite
MIN_THEMES: dict[RiskAppetite, int] = {
    RiskAppetite.CONSERVATIVE: 2,
    RiskAppetite.MODERATE: 3,
    RiskAppetite.AGGRESSIVE: 3,
}

# Regime confidence gates: minimum theme confidence to fund
REGIME_MIN_CONFIDENCE: dict[str, int] = {
    "bull": 5,
    "neutral": 7,
    "bear": 8,
}

# Regime allocation modifier (multiplied into thematic %)
REGIME_MODIFIER: dict[str, float] = {
    "bull": 1.0,
    "neutral": 0.8,
    "bear": 0.5,
}

# Scoring weights (must sum to 1.0)
SCORING_WEIGHTS = {
    "fundamental": 0.40,
    "thesis_alignment": 0.30,
    "pricing_gap": 0.30,
}

# Trusted financial news domains for web search filtering
NEWS_DOMAINS = [
    "reuters.com",
    "ft.com",
    "cnbc.com",
    "bloomberg.com",
    "seekingalpha.com",
    "wsj.com",
    "barrons.com",
    "economist.com",
    "marketwatch.com",
]

# Quality filters for robustness
MIN_MARKET_CAP = 500_000_000  # $500M
MIN_AVG_DAILY_VOLUME = 1_000_000  # $1M daily volume
QUALITY_FLOOR = {
    "max_debt_to_equity": 300,
    "min_operating_margin": -20,  # allow some negative, but not deep losses
    "require_revenue": True,  # must have non-zero market cap as proxy
}

# ---------------------------------------------------------------------------
# Exchange accessibility & currency
# ---------------------------------------------------------------------------

EXCHANGE_ACCESS: dict[str, str] = {
    "": "easy",          # No suffix = US
    "L": "easy",         # London
    "PA": "check_broker",  # Paris
    "DE": "check_broker",  # Frankfurt/Xetra
    "AS": "check_broker",  # Amsterdam
    "CO": "check_broker",  # Copenhagen
    "OL": "check_broker",  # Oslo
    "ST": "check_broker",  # Stockholm
    "HE": "check_broker",  # Helsinki
    "MI": "check_broker",  # Milan
    "MC": "check_broker",  # Madrid
    "SW": "check_broker",  # Swiss
    "T": "exotic",       # Tokyo
    "TW": "exotic",      # Taiwan
    "KS": "exotic",      # Korea
    "HK": "check_broker",  # Hong Kong
    "SS": "exotic",      # Shanghai
    "SZ": "exotic",      # Shenzhen
    "AX": "check_broker",  # Australia
    "TO": "check_broker",  # Toronto
    "NS": "exotic",      # India NSE
    "BO": "exotic",      # India BSE
    "MX": "exotic",      # Mexico
    "SA": "exotic",      # Brazil
}

EXCHANGE_CURRENCY: dict[str, str] = {
    "": "USD", "L": "GBP", "PA": "EUR", "DE": "EUR", "AS": "EUR",
    "CO": "DKK", "OL": "NOK", "ST": "SEK", "HE": "EUR", "MI": "EUR",
    "MC": "EUR", "SW": "CHF", "T": "JPY", "TW": "TWD", "KS": "KRW",
    "HK": "HKD", "SS": "CNY", "SZ": "CNY", "AX": "AUD", "TO": "CAD",
    "NS": "INR", "BO": "INR", "MX": "MXN", "SA": "BRL",
}


def get_accessibility(exchange_suffix: str | None) -> str:
    return EXCHANGE_ACCESS.get(exchange_suffix or "", "check_broker")


def get_currency(exchange_suffix: str | None) -> str:
    return EXCHANGE_CURRENCY.get(exchange_suffix or "", "USD")


def get_thematic_pct(profile: RiskProfile) -> float:
    return THEMATIC_PCT[(profile.appetite, profile.time_horizon)]


def get_max_theme_pct(profile: RiskProfile) -> float:
    return MAX_THEME_PCT[profile.appetite]
