"""Backtesting: validate theme allocations, scores, and tiers vs actual returns."""

from __future__ import annotations

import json
import logging
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)

ALLOC_DIR = Path("data/allocations")
THEMES_DIR = Path("data/themes")
SCORES_DIR = Path("data/scores")

PricePanel = dict[str, pd.Series]

PERFORMANCE_ASSUMPTIONS = {
    "price_basis": "adjusted_close",
    "dividend_treatment": "included in adjusted close",
    "fee_treatment": "no additional fees; fund expenses are reflected in adjusted prices",
    "fx_treatment": "no FX conversion; position currencies are reported as stored",
    "transaction_cost_pct": 0.0,
    "cash_return_pct": 0.0,
    "risk_free_rate_pct": 0.0,
}


# ---------------------------------------------------------------------------
# Snapshot management
# ---------------------------------------------------------------------------


def list_snapshots() -> list[str]:
    """List available allocation dates (YYYYMMDD)."""
    if not ALLOC_DIR.exists():
        return []
    return sorted(
        f.stem.replace("_allocation", "")
        for f in ALLOC_DIR.glob("*_allocation.json")
    )


def load_allocation(date_str: str) -> Optional[dict]:
    """Load a saved allocation by date string (YYYYMMDD)."""
    path = ALLOC_DIR / f"{date_str}_allocation.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def load_scores(date_str: str) -> dict[str, list[dict]]:
    """Load saved scores by date string (YYYYMMDD)."""
    path = SCORES_DIR / f"{date_str}_scores.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def load_themes(date_str: str) -> list[dict]:
    """Load saved themes by date string (YYYYMMDD)."""
    path = THEMES_DIR / f"{date_str}_themes.json"
    if not path.exists():
        return []
    return json.loads(path.read_text())


def load_snapshot(date_str: str) -> dict | None:
    """Load a full snapshot: allocation + scores + themes."""
    alloc = load_allocation(date_str)
    if not alloc:
        return None
    return {
        "date": date_str,
        "allocation": alloc,
        "scores": load_scores(date_str),
        "themes": load_themes(date_str),
    }


# ---------------------------------------------------------------------------
# Price data
# ---------------------------------------------------------------------------


def fetch_price_history(
    tickers: list[str],
    start: datetime,
    end: datetime,
) -> PricePanel:
    """Fetch one bounded daily adjusted-close panel for a list of tickers.

    ``Adj Close`` is required so splits and distributions are represented in
    the same price series used by every backtest calculation. A provider that
    exposes only raw ``Close`` data is treated as unavailable.
    """
    prices: PricePanel = {}
    start_str = start.strftime("%Y-%m-%d")
    end_str = (end + timedelta(days=1)).strftime("%Y-%m-%d")

    for ticker in tickers:
        try:
            hist = yf.Ticker(ticker).history(
                start=start_str,
                end=end_str,
                auto_adjust=False,
            )
            if hist is None or hist.empty:
                continue
            if "Adj Close" not in hist.columns:
                log.warning("Adjusted price unavailable for %s", ticker)
                continue
            column = "Adj Close"
            series = pd.to_numeric(hist[column], errors="coerce").dropna()
            prices[ticker] = _bound_price_series(series, start, end)
            if prices[ticker].empty:
                prices.pop(ticker)
        except Exception as exc:
            log.warning("Price fetch failed for %s: %s", ticker, exc)

    return prices


def _bound_price_series(
    series: pd.Series,
    start: datetime,
    end: datetime,
) -> pd.Series:
    """Keep only observations inside the requested inclusive date range."""
    if not isinstance(series.index, pd.DatetimeIndex):
        # A few legacy test/provider seams return an unindexed two-point
        # series. It is already bounded by the provider call in that case.
        return series

    index = series.index
    if index.tz is not None:
        index = index.tz_convert(None)
    else:
        index = index.tz_localize(None)
    bounded = series.copy()
    bounded.index = index
    return bounded[
        (bounded.index.normalize() >= pd.Timestamp(start.date()))
        & (bounded.index.normalize() <= pd.Timestamp(end.date()))
    ]


def _first_price_on_or_after(
    series: pd.Series | None,
    target: datetime,
) -> float | None:
    if series is None or series.empty:
        return None
    if not isinstance(series.index, pd.DatetimeIndex):
        return float(series.iloc[0])
    candidates = series[series.index.normalize() >= pd.Timestamp(target.date())]
    if candidates.empty:
        return None
    return float(candidates.iloc[0])


def _last_price_on_or_before(
    series: pd.Series | None,
    target: datetime,
) -> float | None:
    if series is None or series.empty:
        return None
    if not isinstance(series.index, pd.DatetimeIndex):
        return float(series.iloc[-1])
    candidates = series[series.index.normalize() <= pd.Timestamp(target.date())]
    if candidates.empty:
        return None
    return float(candidates.iloc[-1])


def _panel_return(
    ticker: str,
    prices: PricePanel,
    start: datetime,
    end: datetime,
    entry_price: float | None = None,
) -> tuple[float | None, float | None, float | None]:
    """Return start reference, bounded end price, and percentage return."""
    series = prices.get(ticker)
    end_price = _last_price_on_or_before(series, end)
    start_price = (
        float(entry_price)
        if entry_price is not None and entry_price > 0
        else _first_price_on_or_after(series, start)
    )
    if (
        start_price is None
        or end_price is None
        or start_price <= 0
        or end_price <= 0
    ):
        return start_price, end_price, None
    return start_price, end_price, round((end_price - start_price) / start_price * 100, 2)


def _get_current_price(ticker: str) -> Optional[float]:
    """Get current price for a ticker."""
    try:
        info = yf.Ticker(ticker).info or {}
        return info.get("regularMarketPrice") or info.get("currentPrice")
    except Exception:
        return None


@dataclass(frozen=True)
class _AllocationPosition:
    ticker: str
    weight_pct: float
    entry_price: float | None
    sleeve: str
    theme: str


def _allocation_positions(alloc: dict) -> tuple[list[_AllocationPosition], bool]:
    """Normalize current positions and legacy entries at one compatibility seam."""
    raw_positions = alloc.get("positions") or []
    if raw_positions:
        themes_by_ticker = {
            ticker.strip().upper(): entry.get("theme", "?")
            for entry in alloc.get("entries", [])
            for ticker in entry.get("tickers", entry.get("vehicle", "").split(","))
            if ticker.strip()
        }
        positions = []
        for position in raw_positions:
            ticker = position.get("ticker", "").strip().upper()
            if not ticker:
                continue
            sleeve = position.get("sleeve", "?")
            is_cash = position.get("instrument_type") == "cash" or sleeve == "cash"
            positions.append(
                _AllocationPosition(
                    ticker=ticker,
                    weight_pct=float(position.get("weight_pct", 0)),
                    entry_price=position.get("entry_price"),
                    sleeve=sleeve,
                    theme=(
                        "Cash"
                        if is_cash
                        else themes_by_ticker.get(ticker, str(sleeve).capitalize())
                    ),
                )
            )
        return positions, True

    positions = []
    for entry in alloc.get("entries", []):
        tickers = [
            ticker.strip().upper()
            for ticker in entry.get("tickers", entry.get("vehicle", "").split(","))
            if ticker.strip()
        ]
        count = max(len(tickers), 1)
        prices = {
            ticker.upper(): price
            for ticker, price in entry.get("entry_prices", {}).items()
        }
        for ticker in tickers:
            positions.append(
                _AllocationPosition(
                    ticker=ticker,
                    weight_pct=float(entry.get("pct_allocation", 0)) / count,
                    entry_price=prices.get(ticker),
                    sleeve="thematic",
                    theme=entry.get("theme", "?"),
                )
            )
    return positions, False


def _analysis_tickers(
    alloc: dict,
    scores: dict[str, list[dict]],
    themes: list[dict],
    benchmark: str | None = None,
) -> list[str]:
    """Collect the instruments needed by every backtest analysis."""
    positions, _authoritative = _allocation_positions(alloc)
    tickers = {
        position.ticker for position in positions if position.sleeve != "cash"
    }
    tickers.update(
        score.get("ticker", "").strip().upper()
        for score_list in scores.values()
        for score in score_list
        if score.get("ticker", "").strip()
    )
    for theme in themes:
        for sub_theme in theme.get("sub_themes", []):
            for company in sub_theme.get("companies", []):
                ticker = company.get("ticker", "").strip().upper()
                suffix = company.get("exchange_suffix")
                if ticker:
                    tickers.add(f"{ticker}.{suffix}" if suffix else ticker)
    if benchmark:
        tickers.add(benchmark.upper())
    return sorted(tickers)


def _position_return(
    position: _AllocationPosition,
    *,
    prices: PricePanel | None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> tuple[float | None, float | None]:
    """Return the end price and percentage return for one position."""
    if position.sleeve == "cash":
        return position.entry_price or 1.0, PERFORMANCE_ASSUMPTIONS["cash_return_pct"]
    if prices is None:
        current_price = _get_current_price(position.ticker)
        if (
            position.entry_price
            and position.entry_price > 0
            and current_price
            and current_price > 0
        ):
            return current_price, round(
                (current_price - position.entry_price) / position.entry_price * 100,
                2,
            )
        return current_price, None
    assert start is not None and end is not None
    _start_price, end_price, return_pct = _panel_return(
        position.ticker,
        prices,
        start,
        end,
        position.entry_price,
    )
    return end_price, return_pct


# ---------------------------------------------------------------------------
# Core return computation
# ---------------------------------------------------------------------------


def compute_returns(
    alloc: dict,
    from_date: str,
    to_date: str | None = None,
    benchmark: str = "SPY",
    *,
    prices: PricePanel | None = None,
) -> dict:
    """Compute portfolio and benchmark returns between two dates.

    All prices come from one bounded adjusted-price panel. ``prices`` is
    supplied by ``full_backtest`` so every analysis shares the same data; the
    public function fetches its own panel when called directly.
    """
    start = datetime.strptime(from_date, "%Y%m%d")
    end = datetime.strptime(to_date, "%Y%m%d") if to_date else datetime.utcnow()

    positions, authoritative = _allocation_positions(alloc)
    if prices is None:
        prices = fetch_price_history(
            [
                position.ticker
                for position in positions
                if position.sleeve != "cash"
            ]
            + [benchmark],
            start,
            end,
        )

    ticker_returns: list[dict] = []
    for position in positions:
        if position.sleeve == "cash":
            current_price = position.entry_price or 1.0
            return_pct = PERFORMANCE_ASSUMPTIONS["cash_return_pct"]
        else:
            _start_price, current_price, return_pct = _panel_return(
                position.ticker,
                prices,
                start,
                end,
                position.entry_price,
            )
        ticker_returns.append(
            {
                "ticker": position.ticker,
                "theme": position.theme,
                "entry_price": position.entry_price,
                "current_price": current_price,
                "return_pct": return_pct,
                "weight_pct": round(position.weight_pct, 2),
                "sleeve": position.sleeve,
                "price_as_of": to_date or end.strftime("%Y%m%d"),
                "price_basis": PERFORMANCE_ASSUMPTIONS["price_basis"],
            }
        )

    missing_tickers = sorted(
        {
            row["ticker"]
            for row in ticker_returns
            if row["return_pct"] is None and row["sleeve"] != "cash"
        }
    )

    def sleeve_return(sleeve: str) -> float | None:
        sleeve_rows = [row for row in ticker_returns if row["sleeve"] == sleeve]
        if not sleeve_rows:
            return 0.0
        if any(row["return_pct"] is None for row in sleeve_rows):
            return None
        weight = sum(row["weight_pct"] for row in sleeve_rows)
        if weight <= 0:
            return 0.0
        return round(
            sum(row["return_pct"] * row["weight_pct"] for row in sleeve_rows)
            / weight,
            2,
        )

    thematic_return = sleeve_return("thematic")
    core_return: float | None
    defensive_return: float | None
    cash_return = sleeve_return("cash")
    unpriced_sleeves: list[str] = []
    legacy_core_pct = 0.0
    legacy_defensive_pct = 0.0
    legacy_cash_pct = 0.0

    if authoritative:
        core_return = sleeve_return("core")
        defensive_return = sleeve_return("defensive")
        blended_return = (
            round(
                sum(row["return_pct"] * row["weight_pct"] for row in ticker_returns)
                / 100,
                2,
            )
            if positions and not missing_tickers
            else None
        )
    else:
        core_pct = float(alloc.get("core_pct", 60))
        _benchmark_start, _benchmark_end, core_return = _panel_return(
            benchmark,
            prices,
            start,
            end,
        )
        defensive_pct = float(alloc.get("defensive_pct", 0))
        cash_pct = float(alloc.get("cash_pct", 0))
        legacy_core_pct = core_pct
        legacy_defensive_pct = defensive_pct
        legacy_cash_pct = cash_pct
        unpriced_sleeves = ["defensive"] if defensive_pct else []
        thematic_share = sum(position.weight_pct for position in positions) / 100
        blended_return = (
            round(
                (thematic_return or 0) * thematic_share
                + (core_return or 0) * core_pct / 100
                + cash_pct * PERFORMANCE_ASSUMPTIONS["cash_return_pct"] / 100,
                2,
            )
            if thematic_return is not None
            and core_return is not None
            and not unpriced_sleeves
            else None
        )
        defensive_return = None if defensive_pct else 0.0

    _benchmark_start, _benchmark_end, benchmark_return = _panel_return(
        benchmark,
        prices,
        start,
        end,
    )
    alpha = (
        round(blended_return - benchmark_return, 2)
        if blended_return is not None and benchmark_return is not None
        else None
    )

    total_weight = sum(position.weight_pct for position in positions)
    covered_weight = sum(
        row["weight_pct"]
        for row in ticker_returns
        if row["return_pct"] is not None
    )
    total_instruments = len(ticker_returns)
    covered_instruments = sum(
        1 for row in ticker_returns if row["return_pct"] is not None
    )
    if not authoritative:
        total_weight += legacy_core_pct + legacy_defensive_pct + legacy_cash_pct
        total_instruments += sum(
            1 for weight in (
                legacy_core_pct,
                legacy_defensive_pct,
                legacy_cash_pct,
            ) if weight > 0
        )
        if benchmark_return is not None:
            covered_weight += legacy_core_pct
            covered_instruments += 1 if legacy_core_pct > 0 else 0
        covered_weight += legacy_cash_pct
        covered_instruments += 1 if legacy_cash_pct > 0 else 0

    return {
        "from_date": from_date,
        "to_date": to_date or end.strftime("%Y%m%d"),
        "ticker_returns": ticker_returns,
        "thematic_return": thematic_return,
        "core_return": core_return,
        "defensive_return": defensive_return,
        "cash_return": cash_return,
        "blended_return": blended_return,
        "benchmark_ticker": benchmark,
        "benchmark_return": benchmark_return,
        "alpha": alpha,
        "max_drawdown": None,
        "incomplete": bool(missing_tickers or unpriced_sleeves),
        "missing_tickers": missing_tickers,
        "coverage": {
            "instruments_total": total_instruments,
            "instruments_with_data": covered_instruments,
            "weight_total_pct": round(total_weight, 2),
            "weight_with_data_pct": round(covered_weight, 2),
            "weight_missing_pct": round(total_weight - covered_weight, 2),
            "unpriced_sleeves": unpriced_sleeves,
        },
        "assumptions": dict(PERFORMANCE_ASSUMPTIONS),
    }


# ---------------------------------------------------------------------------
# Theme attribution
# ---------------------------------------------------------------------------


def theme_attribution(
    alloc: dict,
    scores: dict[str, list[dict]],
    themes: list[dict],
    *,
    prices: PricePanel | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[dict]:
    """P&L attribution by theme: return, weight, contribution, confidence."""
    start = datetime.strptime(from_date, "%Y%m%d") if from_date else None
    end = datetime.strptime(to_date, "%Y%m%d") if to_date else None
    positions, _authoritative = _allocation_positions(alloc)
    if prices is None and start is not None and end is not None:
        prices = fetch_price_history(
            [position.ticker for position in positions if position.sleeve != "cash"],
            start,
            end,
        )
    # Build confidence lookup from themes
    confidence_map: dict[str, int] = {}
    for t in themes:
        confidence_map[t.get("name", "")] = t.get("confidence_score", 0)

    results: list[dict] = []
    by_theme: dict[str, list[_AllocationPosition]] = {}
    for position in positions:
        if position.sleeve == "thematic":
            by_theme.setdefault(position.theme, []).append(position)

    for theme_name, theme_positions in by_theme.items():
        pct = sum(position.weight_pct for position in theme_positions)
        weighted_returns: list[tuple[float, float]] = []
        missing = 0
        for position in theme_positions:
            _current_price, return_pct = _position_return(
                position,
                prices=prices,
                start=start,
                end=end,
            )
            if return_pct is not None:
                weighted_returns.append(
                    (
                        return_pct,
                        position.weight_pct,
                    )
                )
            else:
                missing += 1

        avg_ret = (
            sum(value * weight for value, weight in weighted_returns) / pct
            if pct > 0 and not missing
            else None
        )

        # Get average composite score for this theme
        theme_scores = scores.get(theme_name, [])
        allocated_tickers = {position.ticker for position in theme_positions}
        relevant = [s for s in theme_scores if s.get("ticker", "").upper() in allocated_tickers]
        avg_score = (
            statistics.mean(s["composite_score"] for s in relevant)
            if relevant
            else None
        )

        results.append({
            "theme": theme_name,
            "weight_pct": round(pct, 2),
            "return_pct": round(avg_ret, 2) if avg_ret is not None else None,
            "contribution": round(avg_ret * pct / 100, 3) if avg_ret is not None else None,
            "confidence": confidence_map.get(theme_name, 0),
            "avg_score": round(avg_score, 1) if avg_score is not None else None,
            "n_tickers": len(theme_positions),
            "tickers_with_data": len(weighted_returns),
        })

    results.sort(key=lambda r: r.get("contribution") or -999, reverse=True)
    return results


# ---------------------------------------------------------------------------
# Tier analysis
# ---------------------------------------------------------------------------


def tier_analysis(
    alloc: dict,
    scores: dict[str, list[dict]],
    themes: list[dict],
    *,
    prices: PricePanel | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[dict]:
    """Compare returns by supply chain tier (Tier 1 / 2 / 3).

    Uses all scored companies, not just allocated ones, for broader signal.
    """
    start = datetime.strptime(from_date, "%Y%m%d") if from_date else None
    end = datetime.strptime(to_date, "%Y%m%d") if to_date else None

    # Build company→tier map from themes
    ticker_tier: dict[str, str] = {}
    for t in themes:
        for sub in t.get("sub_themes", []):
            for c in sub.get("companies", []):
                full = c.get("ticker", "")
                suffix = c.get("exchange_suffix")
                if suffix:
                    full = f"{full}.{suffix}"
                tier = c.get("supply_chain_tier", "")
                if full and tier:
                    ticker_tier[full.upper()] = tier

    positions, _authoritative = _allocation_positions(alloc)
    entry_price_map = {
        position.ticker: position.entry_price
        for position in positions
        if position.entry_price is not None
    }
    if prices is None and start is not None and end is not None:
        prices = fetch_price_history(
            _analysis_tickers(alloc, scores, themes),
            start,
            end,
        )

    # Build per-tier return lists
    tier_data: dict[str, list[dict]] = {
        "tier_1_demand_driver": [],
        "tier_2_direct_enabler": [],
        "tier_3_picks_and_shovels": [],
    }

    # Use all scored tickers for breadth
    seen_tickers: set[str] = set()
    for theme_name, score_list in scores.items():
        for s in score_list:
            ticker = s.get("ticker", "")
            upper = ticker.upper()
            if upper in seen_tickers:
                continue
            seen_tickers.add(upper)

            tier = ticker_tier.get(upper, "")
            if tier not in tier_data:
                continue

            ep = entry_price_map.get(upper)

            if prices is None:
                if not ep:
                    # Legacy direct calls have no historical panel from which
                    # to recover an unallocated candidate's entry price.
                    continue
                cp = _get_current_price(ticker)
                ret = (
                    round((cp - ep) / ep * 100, 2)
                    if ep and ep > 0 and cp and cp > 0
                    else None
                )
            else:
                assert start is not None and end is not None
                _start_price, cp, ret = _panel_return(
                    upper,
                    prices,
                    start,
                    end,
                    ep,
                )
                if ret is None:
                    continue

            tier_data[tier].append({
                "ticker": ticker,
                "return_pct": ret,
                "composite_score": s.get("composite_score"),
            })

    # Also include allocated tickers that may not be in scores
    for position in positions:
        ticker = position.ticker
        if ticker in seen_tickers:
            continue
        seen_tickers.add(ticker)
        tier = ticker_tier.get(ticker, "")
        if tier not in tier_data:
            continue
        current_price, return_pct = _position_return(
            position,
            prices=prices,
            start=start,
            end=end,
        )
        tier_data[tier].append(
            {
                "ticker": ticker,
                "return_pct": return_pct,
                "composite_score": None,
            }
        )

    tier_labels = {
        "tier_1_demand_driver": "Tier 1 — Demand Drivers",
        "tier_2_direct_enabler": "Tier 2 — Direct Enablers",
        "tier_3_picks_and_shovels": "Tier 3 — Picks & Shovels",
    }

    results: list[dict] = []
    for tier_key, items in tier_data.items():
        rets = [i["return_pct"] for i in items if i["return_pct"] is not None]
        results.append({
            "tier": tier_key,
            "label": tier_labels.get(tier_key, tier_key),
            "n_total": len(items),
            "n_with_data": len(rets),
            "avg_return": round(statistics.mean(rets), 2) if rets else None,
            "median_return": round(statistics.median(rets), 2) if rets else None,
            "best": round(max(rets), 2) if rets else None,
            "worst": round(min(rets), 2) if rets else None,
            "win_rate": round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1) if rets else None,
        })

    return results


# ---------------------------------------------------------------------------
# Score validation — does scoring predict returns?
# ---------------------------------------------------------------------------


def score_validation(
    alloc: dict,
    scores: dict[str, list[dict]],
    *,
    prices: PricePanel | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[dict]:
    """Test whether composite & component scores predict forward returns.

    For each scoring dimension, compute:
    - Spearman rank correlation vs forward return
    - Top-quartile vs bottom-quartile average return
    - Spread (top − bottom)
    """
    start = datetime.strptime(from_date, "%Y%m%d") if from_date else None
    end = datetime.strptime(to_date, "%Y%m%d") if to_date else None

    # Collect scored tickers with persisted entry prices. The later cohort
    # validation ticket can broaden this universe without changing the
    # historical pricing seam here.
    positions, _authoritative = _allocation_positions(alloc)
    entry_price_map = {
        position.ticker: position.entry_price
        for position in positions
        if position.entry_price is not None
    }
    if prices is None and start is not None and end is not None:
        prices = fetch_price_history(
            _analysis_tickers(alloc, scores, []),
            start,
            end,
        )

    rows: list[dict] = []
    seen: set[str] = set()

    for theme_name, score_list in scores.items():
        for s in score_list:
            ticker = s.get("ticker", "")
            upper = ticker.upper()
            if upper in seen:
                continue
            seen.add(upper)

            ep = entry_price_map.get(upper)
            if not ep or ep <= 0:
                continue
            if prices is None:
                cp = _get_current_price(ticker)
                if not cp or cp <= 0:
                    continue
                ret = round((cp - ep) / ep * 100, 2)
            else:
                assert start is not None and end is not None
                _start_price, cp, ret = _panel_return(
                    upper,
                    prices,
                    start,
                    end,
                    ep,
                )
                if ret is None:
                    continue
            rows.append({
                "ticker": ticker,
                "return_pct": ret,
                "composite_score": s.get("composite_score"),
                "fundamental_score": s.get("fundamental_score"),
                "thesis_alignment_score": s.get("thesis_alignment_score"),
                "pricing_gap_score": s.get("pricing_gap_score"),
            })

    if len(rows) < 4:
        return []

    results: list[dict] = []
    dimensions = [
        ("composite_score", "Composite (overall)"),
        ("fundamental_score", "Fundamental (40%)"),
        ("thesis_alignment_score", "Thesis alignment† (30%)"),
        ("pricing_gap_score", "Pricing gap† (30%)"),
    ]

    for key, label in dimensions:
        # Filter to rows with this score
        valid = [(r["return_pct"], r[key]) for r in rows if r.get(key) is not None]
        if len(valid) < 4:
            continue

        returns = [v[0] for v in valid]
        score_vals = [v[1] for v in valid]

        # Spearman rank correlation
        rho = _spearman(score_vals, returns)

        # Quartile analysis
        sorted_by_score = sorted(valid, key=lambda v: v[1], reverse=True)
        q_size = max(len(sorted_by_score) // 4, 1)
        top_q = [v[0] for v in sorted_by_score[:q_size]]
        bottom_q = [v[0] for v in sorted_by_score[-q_size:]]

        top_avg = statistics.mean(top_q)
        bottom_avg = statistics.mean(bottom_q)

        results.append({
            "dimension": key,
            "label": label,
            "n_companies": len(valid),
            "rank_correlation": round(rho, 3) if rho is not None else None,
            "top_quartile_return": round(top_avg, 2),
            "bottom_quartile_return": round(bottom_avg, 2),
            "spread": round(top_avg - bottom_avg, 2),
        })

    return results


def _spearman(x: list[float], y: list[float]) -> Optional[float]:
    """Compute Spearman rank correlation between two lists."""
    n = len(x)
    if n < 3:
        return None

    def _rank(vals: list[float]) -> list[float]:
        sorted_indices = sorted(range(n), key=lambda i: vals[i])
        ranks = [0.0] * n
        for rank, idx in enumerate(sorted_indices, 1):
            ranks[idx] = float(rank)
        # Handle ties with average rank
        i = 0
        while i < n:
            j = i + 1
            while j < n and vals[sorted_indices[i]] == vals[sorted_indices[j]]:
                j += 1
            if j > i + 1:
                avg_rank = sum(range(i + 1, j + 1)) / (j - i)
                for k in range(i, j):
                    ranks[sorted_indices[k]] = avg_rank
            i = j
        return ranks

    rx = _rank(x)
    ry = _rank(y)

    d_sq = sum((rx[i] - ry[i]) ** 2 for i in range(n))
    return 1 - (6 * d_sq) / (n * (n * n - 1))


# ---------------------------------------------------------------------------
# Confidence analysis — does theme confidence predict theme returns?
# ---------------------------------------------------------------------------


def confidence_analysis(theme_attrs: list[dict]) -> dict:
    """Test whether theme confidence scores predict theme-level returns."""
    valid = [(t["confidence"], t["return_pct"])
             for t in theme_attrs
             if t.get("confidence") and t.get("return_pct") is not None]

    if len(valid) < 3:
        return {"n": len(valid), "sufficient_data": False}

    confs = [v[0] for v in valid]
    rets = [v[1] for v in valid]
    rho = _spearman(confs, rets)

    high = [r for c, r in valid if c >= 8]
    low = [r for c, r in valid if c < 8]

    return {
        "n": len(valid),
        "sufficient_data": True,
        "rank_correlation": round(rho, 3) if rho is not None else None,
        "high_confidence_avg": round(statistics.mean(high), 2) if high else None,
        "low_confidence_avg": round(statistics.mean(low), 2) if low else None,
        "high_count": len(high),
        "low_count": len(low),
    }


# ---------------------------------------------------------------------------
# Risk metrics (time-series based when price history available)
# ---------------------------------------------------------------------------


def compute_risk_metrics(
    alloc: dict,
    from_date: str,
    to_date: str | None = None,
    benchmark: str = "SPY",
    *,
    prices: PricePanel | None = None,
) -> dict:
    """Compute full-portfolio time-series risk metrics.

    Position weights remain percentage points of the whole portfolio. Cash is
    represented as a zero-return sleeve, so missing market data cannot be
    hidden by renormalizing the remaining instruments.
    """
    start = datetime.strptime(from_date, "%Y%m%d")
    end = datetime.strptime(to_date, "%Y%m%d") if to_date else datetime.utcnow()
    days_elapsed = (end - start).days

    positions, authoritative = _allocation_positions(alloc)
    investable_positions = [
        position for position in positions if position.sleeve != "cash"
    ]

    if prices is None:
        all_tickers = list(dict.fromkeys(
            [position.ticker for position in investable_positions] + [benchmark]
        ))
        prices = fetch_price_history(all_tickers, start, end)
    missing_tickers = sorted(
        {
            position.ticker
            for position in investable_positions
            if position.ticker not in prices or len(prices[position.ticker]) < 2
        }
    )

    bm_prices = prices.get(benchmark)
    benchmark_daily_series = (
        bm_prices.pct_change().dropna()
        if bm_prices is not None and len(bm_prices) >= 2
        else pd.Series(dtype=float)
    )
    benchmark_daily = benchmark_daily_series.tolist()

    # Build one aligned daily return panel for every non-cash sleeve. Cash is
    # added as zero return on the same index and therefore retains its weight.
    component_returns: dict[str, tuple[float, pd.Series]] = {}
    for index, position in enumerate(investable_positions):
        if position.ticker in missing_tickers:
            continue
        component_returns[f"{position.ticker}:{index}"] = (
            position.weight_pct / 100,
            prices[position.ticker].pct_change().dropna(),
        )

    unpriced_legacy_sleeves: list[str] = []
    if not authoritative:
        if alloc.get("core_pct", 0) and not benchmark_daily_series.empty:
            component_returns["legacy-core"] = (
                float(alloc.get("core_pct", 0)) / 100,
                benchmark_daily_series,
            )
        elif alloc.get("core_pct", 0):
            unpriced_legacy_sleeves.append("core")
        if alloc.get("defensive_pct", 0):
            # Legacy snapshots did not persist a defensive instrument. Keep
            # that exposure visible as incomplete instead of treating it as 0.
            unpriced_legacy_sleeves.append("defensive")

    if missing_tickers or unpriced_legacy_sleeves:
        daily_returns: list[float] = []
    elif component_returns:
        combined = pd.DataFrame(
            {name: series for name, (_weight, series) in component_returns.items()}
        ).dropna()
        daily_returns = [
            sum(weight * row[name] for name, (weight, _series) in component_returns.items())
            for _, row in combined.iterrows()
        ]
    elif benchmark_daily_series.empty:
        daily_returns = []
    else:
        # An all-cash versioned allocation has no market series of its own;
        # benchmark dates provide the calendar for its explicit zero return.
        daily_returns = [0.0 for _value in benchmark_daily_series]

    total_weight = (
        sum(position.weight_pct for position in positions)
        if authoritative
        else sum(position.weight_pct for position in positions)
        + float(alloc.get("core_pct", 0))
        + float(alloc.get("defensive_pct", 0))
        + float(alloc.get("cash_pct", 0))
    )
    covered_weight = sum(
        position.weight_pct
        for position in positions
        if position.sleeve == "cash"
        or (
            position.ticker not in missing_tickers
            and position.ticker in prices
            and len(prices[position.ticker]) >= 2
        )
    )
    if not authoritative and benchmark_daily_series.size:
        covered_weight += float(alloc.get("core_pct", 0))

    # Compute metrics
    result: dict = {
        "days_elapsed": days_elapsed,
        "trading_days": len(daily_returns),
        "incomplete": bool(missing_tickers or unpriced_legacy_sleeves),
        "missing_tickers": missing_tickers,
        "coverage": {
            "instruments_total": len(positions),
            "instruments_with_data": len(positions) - len(missing_tickers),
            "weight_total_pct": round(total_weight, 2),
            "weight_with_data_pct": round(covered_weight, 2),
            "weight_missing_pct": round(total_weight - covered_weight, 2),
            "unpriced_sleeves": unpriced_legacy_sleeves,
        },
        "cash_return_pct": PERFORMANCE_ASSUMPTIONS["cash_return_pct"],
        "risk_free_rate_pct": PERFORMANCE_ASSUMPTIONS["risk_free_rate_pct"],
        "assumptions": dict(PERFORMANCE_ASSUMPTIONS),
    }

    if daily_returns:
        cum = 1.0
        peak = 1.0
        max_dd = 0.0
        for r in daily_returns:
            cum *= (1 + r)
            peak = max(peak, cum)
            dd = (cum - peak) / peak
            max_dd = min(max_dd, dd)

        total_return = (cum - 1) * 100
        ann_factor = 365.25 / days_elapsed if days_elapsed > 0 else 0
        ann_return = ((cum ** ann_factor) - 1) * 100 if len(daily_returns) > 5 and ann_factor else None
        vol = statistics.stdev(daily_returns) * (252 ** 0.5) * 100 if len(daily_returns) > 5 else None
        sharpe = (ann_return / vol) if ann_return is not None and vol and vol > 0 else None

        result.update({
            "total_return": round(total_return, 2),
            "annualized_return": round(ann_return, 2) if ann_return is not None else None,
            "annualized_volatility": round(vol, 2) if vol is not None else None,
            "sharpe_ratio": round(sharpe, 2) if sharpe is not None else None,
            "max_drawdown": round(max_dd * 100, 2),
        })
    else:
        result.update({
            "total_return": None,
            "annualized_return": None,
            "annualized_volatility": None,
            "sharpe_ratio": None,
            "max_drawdown": None,
        })

    # Benchmark metrics
    if benchmark_daily:
        cum = 1.0
        peak = 1.0
        max_dd = 0.0
        for r in benchmark_daily:
            cum *= (1 + r)
            peak = max(peak, cum)
            dd = (cum - peak) / peak
            max_dd = min(max_dd, dd)

        result["benchmark_total_return"] = round((cum - 1) * 100, 2)
        result["benchmark_max_drawdown"] = round(max_dd * 100, 2)
    else:
        result["benchmark_total_return"] = None
        result["benchmark_max_drawdown"] = None

    return result


# ---------------------------------------------------------------------------
# Full backtest orchestrator
# ---------------------------------------------------------------------------


def full_backtest(
    from_date: str,
    to_date: str | None = None,
    benchmark: str = "SPY",
) -> dict | None:
    """Run comprehensive backtest for a snapshot.

    Returns a dict with all analysis results, or None if snapshot not found.
    """
    snapshot = load_snapshot(from_date)
    if not snapshot:
        return None

    resolved_to_date = to_date or datetime.utcnow().strftime("%Y%m%d")
    alloc = snapshot["allocation"]
    scores = snapshot["scores"]
    themes = snapshot["themes"]

    start = datetime.strptime(from_date, "%Y%m%d")
    end = datetime.strptime(resolved_to_date, "%Y%m%d")
    prices = fetch_price_history(
        _analysis_tickers(alloc, scores, themes, benchmark),
        start,
        end,
    )

    log.info("Running full backtest from %s...", from_date)

    # 1. Basic returns
    returns = compute_returns(
        alloc,
        from_date,
        resolved_to_date,
        benchmark,
        prices=prices,
    )

    # 2. Theme attribution
    theme_attr = theme_attribution(
        alloc,
        scores,
        themes,
        prices=prices,
        from_date=from_date,
        to_date=resolved_to_date,
    )

    # 3. Tier analysis
    tiers = tier_analysis(
        alloc,
        scores,
        themes,
        prices=prices,
        from_date=from_date,
        to_date=resolved_to_date,
    )

    # 4. Score validation
    score_val = score_validation(
        alloc,
        scores,
        prices=prices,
        from_date=from_date,
        to_date=resolved_to_date,
    )

    # 5. Confidence analysis
    conf = confidence_analysis(theme_attr)

    # 6. Risk metrics
    risk = compute_risk_metrics(
        alloc,
        from_date,
        resolved_to_date,
        benchmark,
        prices=prices,
    )

    return {
        "snapshot_date": from_date,
        "to_date": resolved_to_date,
        "returns": returns,
        "theme_attribution": theme_attr,
        "tier_analysis": tiers,
        "score_validation": score_val,
        "confidence_analysis": conf,
        "risk_metrics": risk,
    }
