# Alpha Holdings

Autonomous thematic investment research CLI tool. Discovers bullish investment themes from global macro signals, maps supply chains (Tier 1/2/3), scores companies on fundamentals + thesis alignment + pricing gap, and produces model portfolio allocations.

**Core philosophy: "During a gold rush, sell shovels."** The system prioritizes Tier 2-3 supply chain companies with unrecognized theme exposure over expensive Tier 1 demand drivers.

> **NOT FINANCIAL ADVICE** — this is an AI-assisted research tool. Verify all data before making investment decisions.

## Quick Start

```bash
# 1. Clone and install
git clone <repo-url> && cd alpha-holdings
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. Configure
cp example.env .env
# Confirm the Codex CLI settings in .env

# 3. Login with the ChatGPT account used by your Plus plan
codex login

# 4. Run discovery
alpha-holdings discover --risk moderate --horizon 3-5yr
```

## Prerequisites

- **Python 3.11+**
- **Codex CLI** — install the CLI and authenticate with `codex login`
- **ChatGPT Plus** — the CLI uses the signed-in ChatGPT account; no API key is required
- **Codex web search** — enabled for discovery, macro research, and ETF lookups

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `CODEX_CLI_COMMAND` | No | Codex executable and optional fixed arguments (default: `codex`) |
| `CODEX_CLI_TIMEOUT` | No | Timeout in seconds for one CLI call (default: `600`) |
| `CODEX_MODEL` | No | Model passed to Codex CLI (default: `gpt-5.6-luna`) |
| `CODEX_REASONING_EFFORT` | No | Reasoning effort passed as `model_reasoning_effort` (default: `xhigh`) |

`CODEX_CLI_TIMEOUT` applies to each individual Codex request. The 600-second
default allows full web-grounded `discover` calls to finish at `xhigh`. Lower it
for short calls when faster failure is more important than completion:

```bash
CODEX_CLI_TIMEOUT=120 alpha-holdings discover --risk moderate --horizon 3-5yr
```

See [example.env](example.env) for the template. `.env.example` contains the same settings for compatibility.

## CLI Commands

### `alpha-holdings discover`

Full pipeline: macro signals → themes → fundamentals → scoring → ETF mapping → allocation.

```bash
alpha-holdings discover --risk moderate --horizon 3-5yr
alpha-holdings discover --risk aggressive --horizon 3-5yr
alpha-holdings discover --risk conservative --horizon 10yr+ --focus "energy infrastructure"
```

**Options:**
- `--risk` — `conservative` / `moderate` / `aggressive`. Controls thematic vs core allocation split, max concentration per theme, and vehicle preference (ETF vs stocks).
- `--horizon` — `3-5yr` / `5-10yr` / `10yr+`. Longer horizons allocate less to thematic bets.
- `--focus` — Optional focus areas to bias theme discovery (repeatable). The system discovers themes autonomously; this biases but doesn't limit.
- `--base-currency` — Your base currency code (default: `USD`). Non-base currency tickers will show ⚠ FX risk warnings. Exotic exchange tickers are flagged with broker accessibility tags.
- `--capital` — Total capital to invest (e.g., `--capital 10000`). When set, the allocation table shows dollar amounts alongside percentages and flags positions below the $200 minimum viable size.

### `alpha-holdings holdings`

Analyze overlap between your existing positions and the latest saved allocation. Run after `discover`.

```bash
alpha-holdings holdings --file holdings.example.json
alpha-holdings holdings --file data/my_portfolio.json
alpha-holdings holdings --file data/allocations/xxxxxxxx_allocation.json  # accepts allocation files directly
alpha-holdings holdings --file data/my_portfolio.json --base-currency EUR
```

Accepts two formats:
- **Holdings JSON**: `[{"ticker": "VT", "shares": 100, "avg_cost": 95.50}, ...]`; repeated lots are aggregated before weighting.
- **Allocation JSON**: the `data/allocations/*_allocation.json` files from `discover`; concrete position weights, currency, and dated entry prices are preserved.

Share-based portfolios are valued at dated adjusted prices and converted into
`--base-currency` (default `USD`) with FX observations from the same date before
normalization. Missing quotes or FX rates reduce the reported coverage; the
command never silently substitutes an equal-weight portfolio.

### `alpha-holdings explain`

Show the LLM's reasoning behind each company's thesis alignment, pricing gap, and revenue exposure scores. Loads saved data — no new API calls.

```bash
alpha-holdings explain                           # All themes, all tiers
alpha-holdings explain --theme "AI Power"        # Filter to one theme
alpha-holdings explain --tier 3                  # Only Tier 3 picks & shovels
alpha-holdings explain --theme "Nuclear" --tier 2  # Combine filters
```

**Options:**
- `--theme` — Filter to themes matching this name (partial match).
- `--tier` — `1` (demand drivers) / `2` (direct enablers) / `3` (picks & shovels).

### Holdings File Format

Create a JSON file with your existing positions:

```json
[
  {"ticker": "VT", "shares": 100, "avg_cost": 95.50},
  {"ticker": "NVDA", "shares": 10, "avg_cost": 150.00}
]
```

Both existing and proposed ETFs are decomposed through named provider weight
fields. If live composition is unavailable, the command identifies any
disclosed built-in approximation and reports reduced coverage. Unreported fund
weight remains visible as `<ETF>:UNKNOWN/OTHER`; it is never discarded or
presented as known constituent exposure. See
[holdings.example.json](holdings.example.json) for a sample.

### `alpha-holdings monitor`

Re-evaluate saved themes against fresh macro signals. Generates rebalancing signals, scans for dip opportunities, and optionally tracks returns for sell discipline.

```bash
alpha-holdings monitor
alpha-holdings monitor --theme "AI Power Stack"
alpha-holdings monitor --since 20260425    # Track returns from a specific allocation date
```

**Options:**
- `--theme` — Re-evaluate a specific theme only.
- `--since` — Date of a saved allocation (YYYYMMDD) to compute returns from. Shows per-ticker entry price vs current price, return %, and sell discipline signals:
  - **Up >50%** — "Review whether to take profits"
  - **Up >30% but declining from peak** — "Consider trimming"
  - **Down >20%** — "Review thesis validity"

### `alpha-holdings opportunities`

Quick scan across all funded themes for buy-the-dip opportunities. Shows only actionable signals (ON SALE, STABILIZED, RECOVERING).

```bash
alpha-holdings opportunities              # Uses cached prices (fast)
alpha-holdings opportunities --fresh      # Fetches live prices (bypasses 24h cache)
```

### `alpha-holdings watchlist`

High-scoring companies that aren't discounted yet — your buy-the-dip watchlist. Shows tickers with strong composite scores but no current opportunity signal. No new API calls; reads saved scores and fetches current prices.

```bash
alpha-holdings watchlist                         # All companies scoring ≥60
alpha-holdings watchlist --theme "AI"            # Filter to AI theme
alpha-holdings watchlist --tier 3                # Only T3 "picks & shovels"
alpha-holdings watchlist --min-score 80          # Only top scorers
alpha-holdings watchlist --theme "AI" --tier 2 --min-score 70
```

**Options:**
- `--theme` — Filter to a specific theme (word-boundary match).
- `--tier` — Filter to supply chain tier (`1`, `2`, or `3`).
- `--min-score` — Minimum composite score threshold (default: `60`).

### `alpha-holdings backtest`

Track record and model validation. Compares historical allocations vs a benchmark, attributes P&L by theme and supply chain tier, and optionally validates whether scores predict forward returns.

```bash
alpha-holdings backtest                          # From earliest allocation to today
alpha-holdings backtest --from 20260425          # From a specific date
alpha-holdings backtest --from 20260425 --to 20260525 --benchmark VT
alpha-holdings backtest --validate               # Include score validation analysis
```

**Output includes:**
- Per-ticker returns (frozen entry price → requested historical end date, using adjusted prices)
- Portfolio summary: thematic, core, defensive, and cash returns; blended return, alpha, Sharpe ratio, max drawdown, win rate, and data coverage by instrument count and portfolio weight
- Theme attribution: which themes contributed most to P&L, ranked by contribution
- Tier analysis: avg/median returns by Tier 1 / 2 / 3 — tests the "sell shovels" thesis
- Score validation (`--validate`): Spearman rank correlation + top/bottom quartile spread for each scoring dimension (composite, fundamental, thesis alignment, pricing gap)
- Confidence analysis (`--validate`): whether high-confidence themes outperform low-confidence

**Options:**
- `--from` — Start date YYYYMMDD (default: earliest saved allocation).
- `--to` — End date YYYYMMDD (default: today).
- `--benchmark` — Benchmark ticker to compare against (default: `SPY`).
- `--validate` — Run score validation analysis (rank correlation, quartile spreads).

**Building a track record:** Run `discover` periodically (weekly/monthly) to accumulate snapshots. Each snapshot records entry prices at discovery time. The backtest compares those frozen entry prices against current market prices. More snapshots = more statistical power for validating the model.

Backtests consume one adjusted historical price panel bounded by `--from` and
`--to` for every position, benchmark, attribution, tier, score-validation, and
risk calculation. They include explicit zero-return cash, preserve each
position's persisted whole-portfolio weight, and state dividend, fee,
transaction-cost, FX, cash-return, and risk-free-rate assumptions. If any
funded instrument lacks historical data, the analysis names the missing ticker
and reports coverage by instrument count and portfolio weight instead of
silently reweighting the remaining positions.

**Limitations:** Backtesting only works from when themes were first saved. Cannot simulate past runs retroactively. Statistical significance requires 3+ months and multiple snapshots. Past performance does not predict future results.

### `alpha-holdings show`

Display saved data from previous runs.

```bash
alpha-holdings show themes
alpha-holdings show allocation
```

### Global Options

```bash
alpha-holdings -v discover ...   # Verbose/debug logging
```

## Architecture

```
CLI (click + rich)
  → Signal Collector (Codex CLI + web search)
    → Macro regime assessment (bull/neutral/bear)
  → Theme Discovery (configured Codex model with web search, tiered supply chain mapping)
    → Theme dependency mapping (cross-theme causal chains)
  → Fundamentals Fetcher (yfinance, global exchanges)
  → Scoring Engine (40% fundamental + 30% thesis alignment + 30% pricing gap)
  → ETF Mapping (thematic ETF identification + overlap analysis)
  → Allocation Engine (conviction-weighted, regime-adjusted, overlap-aware)
  → Rich terminal output (supply chain trees, allocation tables, dip alerts)
```

### Modules

| Module | Purpose |
|---|---|
| `cli.py` | Click CLI + rich terminal output |
| `llm.py` | Codex CLI client, model/effort configuration, retry handling |
| `models.py` | All Pydantic data models |
| `config.py` | Risk matrix, scoring weights, regime gates |
| `signals.py` | Macro signal collection via agentic web search |
| `themes.py` | Autonomous theme discovery + ticker validation |
| `fundamentals.py` | yfinance global market data + file cache |
| `scoring.py` | Composite scoring + dip opportunity detection |
| `etfs.py` | Thematic ETF identification + overlap analysis |
| `allocation.py` | Conviction-weighted portfolio allocation |
| `monitor.py` | Course correction + rebalancing signals |
| `prompts/` | Versioned prompt templates (separated from logic) |

### Data

All data persisted to `data/` (gitignored):
- `data/runs/` — atomic, versioned run snapshots with scores, prices, configuration, and provenance
- `data/themes/` — discovered themes (JSON, dated)
- `data/allocations/` — allocation snapshots for drift tracking
- `data/cache/` — fundamentals cache (24h TTL)

### Design Decisions

**Multi-pass discovery.** Theme discovery uses two LLM passes: Pass 1 discovers themes with full supply chain mapping, Pass 2 (gap-check) reviews pass 1 output and fills missing layers/companies. This was the single biggest quality improvement — coverage went from 4/10 to 10/10 on a reference benchmark.

**Supply chain layer checklist > reasoning depth.** The discovery prompt requires explicit coverage of 8 supply chain layers (core tech, equipment, components, energy, physical infra, networking, services, raw materials). A/B testing showed this checklist drives completeness more than GPT-5 thinking levels. Medium reasoning with a good prompt beats high reasoning with a vague one.

**Generic gap-check prompt.** The gap-check contains no hardcoded theme hints (nuclear, defense, etc.) — just a generic instruction to check for missing layers and themes. Tested: produces the same coverage as a steered version while staying unbiased and future-proof.

**Reasoning effort (GPT-5 thinking levels).** Tested minimal/low/medium/high:
- **Medium (default)**: wider net, 10/10 benchmark coverage, ~5 min per LLM call
- **High**: better ticker accuracy but too selective — missed AMD, Broadcom, SK Hynix. Gap-check timed out.
- Decision: keep medium for both passes. The `reasoning` parameter is available in the LLM client for future experiments.

**Ticker validation as self-correction.** The LLM frequently gets non-US exchange suffixes wrong. yfinance validation drops invalid tickers after pass 1, then the gap-check often re-suggests the same company with the correct ticker (e.g., Schneider Electric: SE.PA dropped → SU.PA found in gap-check).

### Recommended Workflow

| Command | Frequency | Cost | Purpose |
|---|---|---|---|
| `discover` | Weekly | ~$2-4 | Refresh themes, companies, scores, allocation |
| `opportunities --fresh` | Daily | Free | Catch dip entry points with live prices |
| `watchlist` | On-demand | Free | Browse high-scorers not yet discounted |
| `monitor` | Weekly | ~$1-2 | Full course correction + sell discipline |
| `backtest` | On-demand | Free | Track performance vs benchmark |

## Scoring

Each company is scored on three dimensions:

| Dimension | Weight | What it measures |
|---|---|---|
| **Fundamental** | 40% | Revenue growth, margins, FCF, valuation, balance sheet |
| **Thesis alignment** | 30% | How well positioned for the theme over 5 years |
| **Pricing gap** | 30% | How much the market has NOT priced in the theme exposure |

This weighting naturally surfaces Tier 2-3 "picks & shovels" companies — they have decent fundamentals, strong theme alignment, AND unrecognized pricing.

The fundamental dimension has fixed metric weights: revenue growth 15%, ROE 10%, gross margin 10%, operating margin 15%, FCF yield 15%, forward P/E 15%, PEG 10%, and debt-to-equity 10%. A missing optional metric contributes a neutral 50 rather than causing the remaining metrics to be reweighted.

AI scoring output must identify the requested ticker, keep every score within 0–100, provide all three reasonings, and include at least one HTTP(S) evidence source. Invalid output is retried once and then the candidate is rejected. Successful scores persist their model, provider, timestamp, evidence sources, and dated entry price in the versioned run snapshot, including candidates that are not selected for allocation.

### Reading the Score Display

In the supply chain tree, each scored company shows:

```
NVDA (USD) (22x fwd P/E) [64/F:55/T:72†/P:68†] ⚡ DCA
```

- `64` — composite score (bold)
- `F:55` — fundamental score (data-derived)
- `T:72†` — thesis alignment (AI-estimated, marked with †)
- `P:68†` — pricing gap (AI-estimated, marked with †)
- `⚡ DCA` / `🟢 lump sum` / `🔴 wait` — entry timing recommendation
- `(USD)` — trading currency. `⚠ FX` appears for non-base currencies.
- `[exotic]` / `[check broker]` — broker accessibility warning for non-standard exchanges.

In the `explain` command output, additional AI-estimated reasonings are shown:
- `T†` — thesis alignment reasoning
- `P†` — pricing gap reasoning
- `R†` — revenue exposure reasoning (0-100 scale: 100 = pure-play theme revenue)

## Risk Profiles

Two axes: appetite × time horizon.

|  | 3-5yr | 5-10yr | 10yr+ |
|---|---|---|---|
| Conservative | 30% thematic | 20% thematic | 15% thematic |
| Moderate | 50% thematic | 40% thematic | 30% thematic |
| Aggressive | 75% thematic | 55% thematic | 40% thematic |

Risk appetite controls the minimum number of funded themes, theme and aggregate
company concentration caps, and how many individual stocks can be funded per
theme:

|  | Minimum themes | Max per theme | Max per company | Max stocks per theme |
|---|---:|---:|---:|---:|
| Conservative | 2 | 10% | 5% | 3 |
| Moderate | 3 | 15% | 8% | 5 |
| Aggressive | 3 | 25% | 10% | 7 |

Only candidates with validated scores, provider identity, instrument type, and a
positive dated price can receive weight. The allocator prefers a validated ETF
when one is available; otherwise it conviction-weights the highest-scoring
stocks up to the profile maximum. A theme name is never treated as a ticker.

ETF suggestions are treated as untrusted candidates, not selections. Every
suggested symbol is checked against provider identity and ETF type, then must
have at least $50 million in assets, 50,000 average daily volume, an expense
ratio no higher than 1%, holdings data, and a dated adjusted close with a named
source. All candidates are evaluated before ranking by theme-company coverage,
assets, and expense ratio. Provider failures and incomplete evidence fail
closed.

Holdings weights are read only from recognized named fields. Fractional
`Holding Percent` values are converted to percentage points, while
`% Of Net Assets` and `pctNetAssets` are already percentages. Reported coverage
is quantified and any residual is retained as `UNKNOWN/OTHER`. The complete
candidate audit and selected evidence are persisted in the versioned run.

Allocations are rounded to 0.1 percentage points and must total 100% within a
0.1-point validation tolerance. Caps and cross-theme overlap penalties change
relative weights, then remaining eligible instruments are reweighted. Capacity
that still cannot be invested is explained and moved to the configured `VT`
core position, or to explicit cash if the core price is unavailable. Duplicate
listings and share classes connected by canonical issuer aliases share one
aggregate company cap across themes.

Core (`VT`), defensive (`BND`), thematic, and cash sleeves are all persisted as
the same concrete position model with ticker, percentage-point weight, capital
amount when `--capital` is supplied, currency, entry price, and price timestamp.
Every funded ETF uses its validated adjusted close, timestamp, and source.
The older grouped allocation entries remain a validated compatibility view.

Thesis horizon is always 3-5 years regardless of time horizon setting. Longer horizons simply allocate less to thematic bets and rely on repeated course correction.

## Macro Regime

The system assesses the overall market environment (bull/neutral/bear) from real-time web signals:

| Regime | Effect |
|---|---|
| **Bull** | Deterministic 1.0× thematic modifier. Fund themes with confidence ≥5/10. |
| **Neutral** | Deterministic 0.8× thematic modifier. Only fund themes with confidence ≥7/10. |
| **Bear** | Deterministic 0.5× thematic modifier. Only confidence ≥8/10; place 20% of the policy's non-thematic budget in validated `BND`. |

The AI classifies the regime and explains its drivers; it does not choose the
allocation modifier. The effective deterministic modifier is stored with every
allocation and versioned run.

## Supply Chain Tiers

Every theme maps companies into tiers:

- **Tier 1 — Demand drivers**: headline companies everyone knows. Typically fully priced.
- **Tier 2 — Direct enablers**: companies that directly supply Tier 1. Partially priced.
- **Tier 3 — Picks & shovels**: infrastructure that enables the enablers. Often still valued at sector multiples, not yet priced for theme exposure. **This is where the alpha is.**

## Quality Filters

Companies must pass minimum thresholds before scoring:

- `market_cap`, `current_price`, and `avg_daily_volume` are mandatory and must be positive finite values.
- At least 4 of the 8 fixed-weight scoring metrics must be usable. Negative P/E and PEG values are economically undefined and do not count toward coverage.
- Provider symbol, equity quote type, and company name must match the proposed candidate.

| Filter | Threshold | Rationale |
|---|---|---|
| Market cap | ≥$500M | Excludes uninvestable micro-caps |
| Avg daily $ volume | ≥$1M | Ensures liquidity for real positions |
| Debt-to-equity | ≤300 (unless margin >15%) | Rejects over-leveraged companies; exempts profitable buyback-heavy firms |
| Operating margin | ≥-20% | Allows cyclical dips but filters deep losses |
| Scoring coverage | ≥4 of 8 metrics | Prevents sparse records from passing through neutral defaults |
| **2-year price return** | **≥-30%** | **Rejects persistent decliners — structural issues** |

Additionally, technical warning flags are shown (but don't reject):
- **200-DMA position** — flagged if >20% below the 200-day moving average (stock in a downtrend)
- **Forward/trailing P/E ratio** — explicitly shown as a multiple comparison, not an earnings-revision series
- **Price/current-EPS proxy** — explicitly shown as a rough proxy, not the stock's historical P/E

Companies that fail these filters are removed before scoring and logged in the output.

Revenue growth is annualized from the actual dates of the oldest and newest available revenue observations. The snapshot records the elapsed period; it is not labelled as a three-year CAGR unless the observations actually span three years.

## Revenue Exposure

The scoring prompt asks the LLM to estimate what percentage of each company’s revenue is tied to the theme. Companies with <20% revenue exposure get their thesis alignment score halved — a conglomerate with 3% relevant revenue shouldn’t score like a pure-play.

## Entry Timing

Each company gets an entry method recommendation based on valuation + thesis confidence:

| Valuation | Thesis confidence | Entry |
|---|---|---|
| Cheap (low P/E, PEG < 1) | ≥7/10 | **Lump Sum** — price is attractive relative to growth |
| Fair | Any | **DCA** — fundamentals justify gradual entry |
| Expensive | ≥8/10 | **DCA** — strong thesis but priced in, go slow |
| Expensive | <8/10 | **Wait** — thesis not strong enough to justify premium |

## Theme Dependencies

After discovering themes, the system maps causal chains between them. For example:
- A compute-heavy theme **drives demand for** energy/power themes
- A defense spending theme **amplifies** cybersecurity themes
- A reshoring theme **shares infrastructure** with grid buildout themes

This surfaces cross-theme opportunities (companies that benefit from multiple theme tailwinds) and flags correlated allocation risk.

## Rebalancing (Course Correction)

The `monitor` command re-evaluates saved themes and generates three levels of rebalancing signals:

| Level | Trigger | Action |
|---|---|---|
| **Theme-level** | Thesis weakens or strengthens | Reduce/increase allocation, redeploy to stronger themes or core |
| **Holding-level** | Company fundamentals deteriorate within a strong theme | Swap to better-positioned company or rotate to theme ETF |
| **Concentration drift** | A position grew above target weight via price appreciation | Trim to target if thesis softening; accept risk if conviction high |

It also scans funded instruments for **dip opportunities**. Course correction applies
the new thesis confidence before scanning: invalidated themes produce `AVOID` only,
and weakened themes produce `CAUTION` rather than a buy recommendation. Specific
stabilization and recovery checks take precedence over a generic drawdown. Every
instrument is classified as actionable, caution/avoid, no-signal, or unavailable;
provider failures are displayed rather than silently omitted.

Each `monitor` run against a versioned discovery snapshot writes an append-only
event under `data/monitoring-events/`, linked to the source run ID. Suggested
company additions remain pending until a future discovery run performs the normal
identity, market-data, and scoring validation; they are never activated directly
from a course-correction response. The `opportunities` command requires a
versioned saved run and uses its allocation's theme/instrument scope.

## Disclaimers & Limitations

- **Not financial advice.** This is an AI-assisted research tool. All output should be verified independently before making investment decisions.
- **LLM-generated scores.** Thesis alignment and pricing gap scores (marked with †) are estimated by the AI model, not sourced from analyst consensus or verified research. They can be confidently wrong.
- **Unknown unknowns.** The tool discovers themes from public news and LLM reasoning. It cannot detect insider information, unpublished regulatory actions, or black swan events. Your broad market core allocation is your protection against what this tool cannot see.
- **Tax implications.** Rebalancing and selling positions may trigger taxable capital gains events. Consult a tax advisor for your jurisdiction.
- **FX risk.** International tickers carry currency risk and higher spread costs that are not reflected in the scoring.
- **Early track record.** The backtesting system validates scores against actual returns, but statistical significance requires 3+ months of history across multiple discovery snapshots. Early results should be treated as directional, not conclusive.
