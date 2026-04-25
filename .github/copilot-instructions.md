# Alpha Holdings — Copilot Instructions

## Project
Autonomous thematic investment research CLI tool. Discovers bullish investment themes from global macro signals, maps supply chains (Tier 1/2/3), scores companies on fundamentals + thesis alignment + pricing gap, and produces model portfolio allocations.

## Constraints
- **Azure & Microsoft Foundry only** — all AI services via Azure OpenAI / Microsoft Foundry. No direct OpenAI API, no local models, no non-Azure services.
- **Entra ID authentication** — use `DefaultAzureCredential` + `get_bearer_token_provider`. No API keys for Azure OpenAI.
- **Responses API** — use `client.responses.create()` with `web_search` tool, not Chat Completions API.
- **Global scope** — US, European, Asian, Australian markets. Not US-only.

## Stack
- Python 3.11+, CLI via click + rich
- Azure OpenAI gpt-5.4 (primary) + gpt-5.4-mini (lightweight tasks)
- yfinance for global market data
- Pydantic for data models, pandas for tabular analysis

## Architecture
- CLI-first (`src/alpha_holdings/cli.py`), notebooks optional for visualization only
- All business logic in importable modules under `src/alpha_holdings/`
- Prompt templates in `src/alpha_holdings/prompts/` — separated from logic
- Data persisted to `data/` (themes, allocations, cache) — gitignored

## Key Principles
- "Sell shovels" — prioritize Tier 2-3 supply chain companies with unrecognized theme exposure
- Thesis horizon always 3-5 years
- Graceful degradation — handle API failures, missing data, hallucinated tickers without crashing
- Every output includes "NOT FINANCIAL ADVICE" disclaimer

## Web Search
- Always search the web for topics that may be more recent than training data: model releases, market data, API changes, current events, pricing.

## Documentation
After any code change that adds, modifies, or removes a feature, flag, or behavior:
update the relevant documentation (README.md, script docstrings, CLI --help text, .env.example).
Never leave code and docs out of sync. The end user relies on docs to discover and use all options.

## Testing
After any code change, verify it works before committing:
- Run a targeted test (mock data or quick CLI command) to confirm the change doesn't break existing functionality.
- For new CLI flags: verify they appear in `--help` and produce the expected output.
- For new modules: verify imports succeed and core functions return expected results with sample data.
- For display changes: visually confirm the output renders correctly in the terminal.
- Never commit untested code. If a full e2e run is too slow, test the specific function in isolation first.
