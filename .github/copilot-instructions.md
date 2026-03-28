# Copilot Instructions for alpha-holdings

## Orientation

- Read `PLAN.md` first for architecture, decisions, phase scope, and step checklists.
- Read `STATUS.md` second for current execution state, upcoming work, blockers, and known limitations.
- These two files together give full project context. Do not rely on repo memory files or session memory for project state.

## File maintenance conventions

### STATUS.md (execution snapshot — keep lean)

- **Trim, don't tick.** When a task is completed, remove it from the checklist rather than marking it `[x]`. The file should only contain open work.
- Sections: `Now` (≤5 lines of current state), `Upcoming Work` (phase-grouped `- [ ]` checklists for next 1–2 phases), `Blocked`, `Known Limitations`, `Test Commands`.
- No "Done" section. Git history and PLAN.md progress notes are the record of completed work.

### PLAN.md (long-lived roadmap — keep stable)

- **Phase Completion Snapshot** uses `[x]`/`[ ]` checkboxes per phase.
- **Steps** use `[x]`/`[ ]` checkboxes per step.
- **Progress notes** exist only for the currently active phase(s). When a phase completes: tick its `[x]` checkbox, delete its progress notes block, and remove its checklist from STATUS.md.
- The Decisions, Verification, and Further Considerations sections are append-only reference — do not trim them.

### General rules

- Do not accumulate historical completion logs in either file.
- When starting a new session, update `STATUS.md → Now` to reflect the current state before beginning work.
- When finishing a session, ensure STATUS.md reflects what's next and any new blockers.

## Code conventions

- Python 3.12, managed with `uv`.
- Domain models use Pydantic v2.
- CLI uses Typer.
- Tests: unit/function tests in `tests/test_*.py`, BDD scenarios in `tests/bdd/`.
- Run all tests: `uv run pytest -q`.
- Lint/format: `uv run ruff check . && uv run ruff format --check .`

## Development principles

### TDD (Red → Green → Refactor)

- **Write a failing test first.** No production code without a corresponding red test.
- Get the test green with the simplest possible implementation — do not gold-plate on the first pass.
- Refactor only after green. Each refactor must keep the suite green.
- Run the full test suite (`uv run pytest -q`) after every change; never leave it red.

### YAGNI (You Aren't Gonna Need It)

- Only build what the current task requires — no speculative features, abstractions, or "just in case" code.
- Do not add parameters, config knobs, or extension points for hypothetical future needs.
- If a need isn't proven by a test or a current requirement, it doesn't belong in the codebase yet.

### DRY (Don't Repeat Yourself)

- Extract shared logic into a helper only when the same code appears in two or more places — not before.
- Prefer small, focused functions over monoliths, but do not create abstractions for one-time operations.
- Test helpers and fixtures in `conftest.py` count — deduplicate test setup the same way you deduplicate production code.
