# Alpha Holdings

Alpha Holdings produces auditable, research-backed proposed investment allocations from market data, macro themes, and investor preferences.

## Portfolio construction

**Portfolio construction mode**:
The persisted policy that determines which instrument types may receive weight in a generated allocation.
_Avoid_: investment strategy, allocation preference

**Automatic mode**:
The existing portfolio construction mode, in which validated ETFs or individual stocks may receive generated allocation.
_Avoid_: default ETF mode

**ETF-only mode**:
A portfolio construction mode in which only validated ETFs and cash may receive generated allocation; company research is supporting evidence, never an investable stock position.
_Avoid_: ETF preference, ETF tilt

**Validated ETF**:
An ETF with sufficient provider evidence for its identity, price, liquidity, assets, fee, and holdings coverage to receive allocation.
_Avoid_: suggested ETF, best-effort ETF

**Unfunded theme**:
A discovered theme that receives no thematic allocation because no validated ETF satisfies the construction mode or allocation constraints.
_Avoid_: rejected theme, missing theme

**Core fallback**:
The validated broad-market ETF, or cash when it is unavailable, that receives thematic capital that cannot be funded under the selected construction mode.
_Avoid_: residual, unallocated capital

**Existing holding**:
A user-owned position supplied for analysis; it is not constrained by the construction mode used for a generated allocation.
_Avoid_: proposed position
