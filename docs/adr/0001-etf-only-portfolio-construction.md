# ETF-only is an explicit portfolio construction mode

Status: accepted

ETF-only is a persisted, fresh-allocation mode: every generated non-cash position must be a validated ETF, while company research remains audit evidence rather than an actionable stock recommendation. The existing automatic mode remains unchanged for compatibility. When a theme lacks a validated ETF, its allocation moves explicitly to the validated core ETF or cash; when no ETF is eligible, the result is a prominently marked 100% cash coverage failure. Risk and horizon policy continue to determine allocation targets, duplicate ETF positions are consolidated while retaining theme attribution, and existing user stock holdings remain analyzable.

## Considered options

- Use ETFs as a preference and fall back to stocks.
- Fail the entire run when ETF coverage is insufficient.
- Treat ETF-only as a hard construction constraint with explicit core/cash fallback.

The hard constraint was selected so an ETF-only request cannot silently produce stock exposure, while the explicit fallback preserves a coherent and auditable allocation.
