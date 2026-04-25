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
cp .env.example .env
# Edit .env with your Azure OpenAI endpoint (Entra ID auth — no API key needed)

# 3. Login to Azure (for Entra ID authentication)
az login

# 4. Run discovery
alpha-holdings discover --risk moderate --horizon 3-5yr
```

## Prerequisites

- **Python 3.11+**
- **Azure CLI** — `az login` for Entra ID authentication
- **Azure OpenAI** — gpt-5.4 and gpt-5.4-mini deployed on Microsoft Foundry
  - Uses the Responses API with `web_search` tool for real-time grounding
  - No API key needed — authenticates via `DefaultAzureCredential`

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `AZURE_OPENAI_BASE_URL` | Yes | Foundry endpoint URL (e.g., `https://<your-resource>.services.ai.azure.com/api/projects/<your-project>/openai/v1/`) |
| `AZURE_OPENAI_MODEL` | Yes | Primary model deployment name (e.g., `gpt-5.4`) |
| `AZURE_OPENAI_MODEL_MINI` | Yes | Lightweight model deployment name (e.g., `gpt-5.4-mini`) |

See [.env.example](.env.example) for the template.

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
alpha-holdings holdings holdings.example.json
alpha-holdings holdings data/my_portfolio.json
```

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

Index funds and ETFs are automatically decomposed into their constituent holdings for overlap analysis using a 3-tier fallback:

1. **yfinance** — live ETF holdings data (most accurate, but not always available)
2. **LLM + web search** — asks the AI model to look up current holdings (works for any ETF)
3. **Built-in data** — hardcoded approximate weights for 15 common ETFs (VT, VOO, SPY, QQQ, SMH, SOXX, XLK, URA, HACK, XME, XLE, ITA, IWDA.AS, VWCE.DE)

This means any ETF you hold — including thematic ones like SMH, URA, or ARKK — will be decomposed for overlap detection. See [holdings.example.json](holdings.example.json) for a sample.

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

Compare historical theme allocations vs a benchmark (default: SPY). Computes per-ticker returns, weighted portfolio return, and alpha.

```bash
alpha-holdings backtest                          # From earliest allocation to today
alpha-holdings backtest --from 20260425          # From a specific date
alpha-holdings backtest --from 20260425 --to 20260525 --benchmark VT
```

**Options:**
- `--from` — Start date YYYYMMDD (default: earliest saved allocation).
- `--to` — End date YYYYMMDD (default: today).
- `--benchmark` — Benchmark ticker to compare against (default: `SPY`).

**Limitations:** Backtesting only works from when themes were first saved. Cannot simulate past runs retroactively. Past performance does not predict future results.

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
  → Signal Collector (Responses API + web_search tool)
    → Macro regime assessment (bull/neutral/bear)
  → Theme Discovery (gpt-5.4 with web search, tiered supply chain mapping)
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
| `llm.py` | Azure OpenAI client, Responses API, Entra ID auth |
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
- `data/themes/` — discovered themes (JSON, dated)
- `data/allocations/` — allocation snapshots for drift tracking
- `data/cache/` — fundamentals cache (24h TTL)

## Scoring

Each company is scored on three dimensions:

| Dimension | Weight | What it measures |
|---|---|---|
| **Fundamental** | 40% | Revenue growth, margins, FCF, valuation, balance sheet |
| **Thesis alignment** | 30% | How well positioned for the theme over 5 years |
| **Pricing gap** | 30% | How much the market has NOT priced in the theme exposure |

This weighting naturally surfaces Tier 2-3 "picks & shovels" companies — they have decent fundamentals, strong theme alignment, AND unrecognized pricing.

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

## Risk Profiles

Two axes: appetite × time horizon.

|  | 3-5yr | 5-10yr | 10yr+ |
|---|---|---|---|
| Conservative | 30% thematic | 20% thematic | 15% thematic |
| Moderate | 50% thematic | 40% thematic | 30% thematic |
| Aggressive | 75% thematic | 55% thematic | 40% thematic |

Thesis horizon is always 3-5 years regardless of time horizon setting. Longer horizons simply allocate less to thematic bets and rely on repeated course correction.

## Macro Regime

The system assesses the overall market environment (bull/neutral/bear) from real-time web signals:

| Regime | Effect |
|---|---|
| **Bull** | Full thematic allocation. Fund all themes above baseline. |
| **Neutral** | Slightly increase core. Only fund themes with confidence ≥7/10. |
| **Bear** | Reduce thematic to minimum. Only confidence ≥8/10. Suggest defensive vehicles (BND, TLT, GLD). |

## Supply Chain Tiers

Every theme maps companies into tiers:

- **Tier 1 — Demand drivers**: headline companies everyone knows. Typically fully priced.
- **Tier 2 — Direct enablers**: companies that directly supply Tier 1. Partially priced.
- **Tier 3 — Picks & shovels**: infrastructure that enables the enablers. Often still valued at sector multiples, not yet priced for theme exposure. **This is where the alpha is.**

## Quality Filters

Companies must pass minimum thresholds before scoring:

| Filter | Threshold | Rationale |
|---|---|---|
| Market cap | ≥$500M | Excludes uninvestable micro-caps |
| Avg daily $ volume | ≥$1M | Ensures liquidity for real positions |
| Debt-to-equity | ≤300 | Rejects over-leveraged companies |
| Operating margin | ≥-20% | Allows cyclical dips but filters deep losses |
| Revenue history | Must exist | Filters out pre-revenue explorers/SPACs |
| **2-year price return** | **≥-30%** | **Rejects persistent decliners — structural issues** |

Additionally, two technical warning flags are shown (but don't reject):
- **200-DMA position** — flagged if >20% below the 200-day moving average (stock in a downtrend)
- **Earnings revision** — flagged if forward P/E > 1.3× trailing P/E (analysts cutting estimates)
- **Historical P/E** — flagged if current forward P/E is <70% (cheap) or >130% (expensive) of the stock's own 5-year average P/E

Companies that fail these filters are removed before scoring and logged in the output.

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

It also scans for **dip opportunities** — companies that dropped in price but retain strong thesis + fundamentals.

## Disclaimers & Limitations

- **Not financial advice.** This is an AI-assisted research tool. All output should be verified independently before making investment decisions.
- **LLM-generated scores.** Thesis alignment and pricing gap scores (marked with †) are estimated by the AI model, not sourced from analyst consensus or verified research. They can be confidently wrong.
- **Unknown unknowns.** The tool discovers themes from public news and LLM reasoning. It cannot detect insider information, unpublished regulatory actions, or black swan events. Your broad market core allocation is your protection against what this tool cannot see.
- **Tax implications.** Rebalancing and selling positions may trigger taxable capital gains events. Consult a tax advisor for your jurisdiction.
- **FX risk.** International tickers carry currency risk and higher spread costs that are not reflected in the scoring.
- **No backtesting yet.** The tool has no track record. Past themes and scores have not been validated against historical returns.
