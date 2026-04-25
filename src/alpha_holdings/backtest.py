"""Backtesting: validate theme allocations, scores, and tiers vs actual returns."""

from __future__ import annotations

import json
import logging
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)

ALLOC_DIR = Path("data/allocations")
THEMES_DIR = Path("data/themes")
SCORES_DIR = Path("data/scores")


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
) -> dict[str, pd.Series]:
    """Fetch daily close prices for a list of tickers.

    Returns dict mapping ticker → Series of daily close prices.
    """
    prices: dict[str, pd.Series] = {}
    start_str = start.strftime("%Y-%m-%d")
    end_str = (end + timedelta(days=1)).strftime("%Y-%m-%d")

    for ticker in tickers:
        try:
            hist = yf.Ticker(ticker).history(start=start_str, end=end_str)
            if hist is not None and not hist.empty and "Close" in hist.columns:
                prices[ticker] = hist["Close"]
        except Exception as exc:
            log.warning("Price fetch failed for %s: %s", ticker, exc)

    return prices


def _get_current_price(ticker: str) -> Optional[float]:
    """Get current price for a ticker."""
    try:
        info = yf.Ticker(ticker).info or {}
        return info.get("regularMarketPrice") or info.get("currentPrice")
    except Exception:
        return None


def _get_period_return(ticker: str, start: datetime, end: datetime) -> Optional[float]:
    """Get return % for a ticker between two dates."""
    try:
        hist = yf.Ticker(ticker).history(
            start=start.strftime("%Y-%m-%d"),
            end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
        )
        if hist is None or hist.empty or len(hist) < 2:
            return None
        start_price = hist["Close"].iloc[0]
        end_price = hist["Close"].iloc[-1]
        if start_price and start_price > 0:
            return round((end_price - start_price) / start_price * 100, 2)
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# Core return computation
# ---------------------------------------------------------------------------


def compute_returns(
    alloc: dict,
    from_date: str,
    to_date: str | None = None,
    benchmark: str = "SPY",
) -> dict:
    """Compute portfolio and benchmark returns between two dates.

    Returns dict with per-ticker returns, portfolio return, benchmark return, alpha.
    """
    start = datetime.strptime(from_date, "%Y%m%d")
    end = datetime.strptime(to_date, "%Y%m%d") if to_date else datetime.utcnow()

    ticker_returns: list[dict] = []
    total_thematic_pct = 0.0

    for entry in alloc.get("entries", []):
        entry_prices = entry.get("entry_prices", {})
        theme = entry.get("theme", "?")
        pct = entry.get("pct_allocation", 0)
        tickers_in_vehicle = [t.strip() for t in entry.get("vehicle", "").split(",")]
        n_tickers = max(len(tickers_in_vehicle), 1)

        for ticker in tickers_in_vehicle:
            ticker = ticker.strip()
            if not ticker:
                continue

            ep = entry_prices.get(ticker)
            cp = _get_current_price(ticker)

            ret_pct = None
            if ep and ep > 0 and cp and cp > 0:
                ret_pct = round((cp - ep) / ep * 100, 2)

            ticker_weight = pct / n_tickers
            total_thematic_pct += ticker_weight

            ticker_returns.append({
                "ticker": ticker,
                "theme": theme,
                "entry_price": ep,
                "current_price": cp,
                "return_pct": ret_pct,
                "weight_pct": round(ticker_weight, 2),
            })

    # Portfolio weighted return (thematic portion only)
    weighted_return = 0.0
    valid_weight = 0.0
    for tr in ticker_returns:
        if tr["return_pct"] is not None:
            weighted_return += tr["return_pct"] * tr["weight_pct"]
            valid_weight += tr["weight_pct"]

    portfolio_return = round(weighted_return / max(valid_weight, 1), 2) if valid_weight > 0 else 0.0

    core_pct = alloc.get("core_pct", 60)
    core_return = _get_period_return(benchmark, start, end)

    thematic_share = total_thematic_pct / 100.0
    core_share = core_pct / 100.0
    blended_return = round(
        portfolio_return * thematic_share + (core_return or 0) * core_share,
        2,
    )

    benchmark_return = _get_period_return(benchmark, start, end)
    alpha = round(blended_return - (benchmark_return or 0), 2) if benchmark_return is not None else None

    max_drawdown = 0.0
    for tr in ticker_returns:
        if tr["return_pct"] is not None and tr["return_pct"] < max_drawdown:
            max_drawdown = tr["return_pct"]

    return {
        "from_date": from_date,
        "to_date": to_date or datetime.utcnow().strftime("%Y%m%d"),
        "ticker_returns": ticker_returns,
        "thematic_return": portfolio_return,
        "core_return": core_return,
        "blended_return": blended_return,
        "benchmark_ticker": benchmark,
        "benchmark_return": benchmark_return,
        "alpha": alpha,
        "max_drawdown": round(max_drawdown, 2),
    }


# ---------------------------------------------------------------------------
# Theme attribution
# ---------------------------------------------------------------------------


def theme_attribution(
    alloc: dict,
    scores: dict[str, list[dict]],
    themes: list[dict],
) -> list[dict]:
    """P&L attribution by theme: return, weight, contribution, confidence."""
    # Build confidence lookup from themes
    confidence_map: dict[str, int] = {}
    for t in themes:
        confidence_map[t.get("name", "")] = t.get("confidence_score", 0)

    results: list[dict] = []

    for entry in alloc.get("entries", []):
        theme_name = entry.get("theme", "?")
        pct = entry.get("pct_allocation", 0)
        entry_prices = entry.get("entry_prices", {})
        tickers = [t.strip() for t in entry.get("vehicle", "").split(",") if t.strip()]
        n = max(len(tickers), 1)

        ticker_rets: list[float] = []
        for ticker in tickers:
            ep = entry_prices.get(ticker)
            cp = _get_current_price(ticker)
            if ep and ep > 0 and cp and cp > 0:
                ticker_rets.append((cp - ep) / ep * 100)

        avg_ret = statistics.mean(ticker_rets) if ticker_rets else None

        # Get average composite score for this theme
        theme_scores = scores.get(theme_name, [])
        allocated_tickers = {t.upper() for t in tickers}
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
            "n_tickers": len(tickers),
            "tickers_with_data": len(ticker_rets),
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
) -> list[dict]:
    """Compare returns by supply chain tier (Tier 1 / 2 / 3).

    Uses all scored companies, not just allocated ones, for broader signal.
    """
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

    # Collect entry prices from allocation
    entry_price_map: dict[str, float] = {}
    for entry in alloc.get("entries", []):
        for ticker, price in entry.get("entry_prices", {}).items():
            entry_price_map[ticker.upper()] = price

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
            if not ep:
                # Try to get historical price from allocation date (approximate)
                continue

            cp = _get_current_price(ticker)
            ret = None
            if ep and ep > 0 and cp and cp > 0:
                ret = round((cp - ep) / ep * 100, 2)

            tier_data[tier].append({
                "ticker": ticker,
                "return_pct": ret,
                "composite_score": s.get("composite_score"),
            })

    # Also include allocated tickers that may not be in scores
    for entry in alloc.get("entries", []):
        for ticker in [t.strip() for t in entry.get("vehicle", "").split(",") if t.strip()]:
            upper = ticker.upper()
            if upper in seen_tickers:
                continue
            seen_tickers.add(upper)
            tier = ticker_tier.get(upper, "")
            if tier not in tier_data:
                continue
            ep = entry_price_map.get(upper)
            cp = _get_current_price(ticker)
            ret = None
            if ep and ep > 0 and cp and cp > 0:
                ret = round((cp - ep) / ep * 100, 2)
            tier_data[tier].append({"ticker": ticker, "return_pct": ret, "composite_score": None})

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
) -> list[dict]:
    """Test whether composite & component scores predict forward returns.

    For each scoring dimension, compute:
    - Spearman rank correlation vs forward return
    - Top-quartile vs bottom-quartile average return
    - Spread (top − bottom)
    """
    # Collect all scored tickers with entry prices
    entry_price_map: dict[str, float] = {}
    for entry in alloc.get("entries", []):
        for ticker, price in entry.get("entry_prices", {}).items():
            entry_price_map[ticker.upper()] = price

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

            cp = _get_current_price(ticker)
            if not cp or cp <= 0:
                continue

            ret = round((cp - ep) / ep * 100, 2)
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
) -> dict:
    """Compute time-series risk metrics: Sharpe, max drawdown, volatility."""
    start = datetime.strptime(from_date, "%Y%m%d")
    end = datetime.strptime(to_date, "%Y%m%d") if to_date else datetime.utcnow()
    days_elapsed = (end - start).days

    # Collect all tickers with weights
    positions: list[tuple[str, float, float]] = []  # (ticker, weight_pct, entry_price)
    for entry in alloc.get("entries", []):
        entry_prices = entry.get("entry_prices", {})
        pct = entry.get("pct_allocation", 0)
        tickers = [t.strip() for t in entry.get("vehicle", "").split(",") if t.strip()]
        n = max(len(tickers), 1)
        for ticker in tickers:
            ep = entry_prices.get(ticker, 0)
            positions.append((ticker, pct / n, ep))

    all_tickers = [p[0] for p in positions] + [benchmark]
    prices = fetch_price_history(list(set(all_tickers)), start, end)

    # Build daily portfolio return series
    daily_returns: list[float] = []
    benchmark_daily: list[float] = []

    bm_prices = prices.get(benchmark)
    if bm_prices is not None and len(bm_prices) >= 2:
        bm_rets = bm_prices.pct_change().dropna()
        benchmark_daily = bm_rets.tolist()

    # Weighted portfolio daily returns
    if positions:
        total_weight = sum(p[1] for p in positions)
        if total_weight > 0:
            # Get common dates across all position tickers
            pos_series: list[tuple[float, pd.Series]] = []
            for ticker, weight, ep in positions:
                if ticker in prices and len(prices[ticker]) >= 2:
                    pos_series.append((weight / total_weight, prices[ticker].pct_change().dropna()))

            if pos_series:
                # Align on common dates
                combined = pd.DataFrame(
                    {f"w{i}": s for i, (w, s) in enumerate(pos_series)}
                ).dropna()
                if not combined.empty:
                    weights = [w for w, s in pos_series]
                    for _, row in combined.iterrows():
                        daily_ret = sum(w * row.iloc[i] for i, (w, _) in enumerate(pos_series))
                        daily_returns.append(daily_ret)

    # Compute metrics
    result: dict = {
        "days_elapsed": days_elapsed,
        "trading_days": len(daily_returns),
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
        ann_factor = 252 / max(len(daily_returns), 1)
        ann_return = ((cum ** ann_factor) - 1) * 100 if len(daily_returns) > 5 else None
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

    alloc = snapshot["allocation"]
    scores = snapshot["scores"]
    themes = snapshot["themes"]

    log.info("Running full backtest from %s...", from_date)

    # 1. Basic returns
    returns = compute_returns(alloc, from_date, to_date, benchmark)

    # 2. Theme attribution
    theme_attr = theme_attribution(alloc, scores, themes)

    # 3. Tier analysis
    tiers = tier_analysis(alloc, scores, themes)

    # 4. Score validation
    score_val = score_validation(alloc, scores)

    # 5. Confidence analysis
    conf = confidence_analysis(theme_attr)

    # 6. Risk metrics
    risk = compute_risk_metrics(alloc, from_date, to_date, benchmark)

    return {
        "snapshot_date": from_date,
        "to_date": to_date or datetime.utcnow().strftime("%Y%m%d"),
        "returns": returns,
        "theme_attribution": theme_attr,
        "tier_analysis": tiers,
        "score_validation": score_val,
        "confidence_analysis": conf,
        "risk_metrics": risk,
    }
