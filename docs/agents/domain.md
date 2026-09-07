# Domain Docs

This repository uses a single-context domain-documentation layout.

## Before exploring, read these

- `CONTEXT.md` at the repository root, when present.
- Relevant ADRs under `docs/adr/`, when present.

Missing domain files should not block work or trigger suggestions to create them preemptively. Domain-modeling workflows create them when terminology or architectural decisions need documenting.

## File structure

```
/
├── CONTEXT.md
├── docs/
│   └── adr/
└── src/
```

## Use the glossary’s vocabulary

Use domain terms as defined in `CONTEXT.md`. Avoid synonyms the glossary explicitly rejects.

If a required concept is absent, reconsider whether new terminology is necessary or note the gap for domain modeling.

## Flag ADR conflicts

Explicitly surface proposals that contradict an existing ADR rather than silently overriding it.
