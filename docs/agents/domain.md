# Domain Docs

This is a single-context repository.

## Before exploring

- Read `CONTEXT.md` at the repository root.
- Read the ADRs under `docs/adr/` that govern the area being changed.
- For V8 design or implementation, read
  `docs/design/gwo-v8-lean-architecture.md`. The older V8 architecture and
  roadmap are historical migration records.
- Proceed silently if a referenced domain document does not yet exist.

## Use the glossary vocabulary

Use terms exactly as defined in `CONTEXT.md` in Issue titles, implementation
plans, and user-facing review findings. Implementation-only records may use
private names inside their owning deep module, but must not expand the
ubiquitous language or leak into other module interfaces.

If a needed concept is absent, first decide whether the proposed term is
unnecessary. Record a genuine domain gap through the domain-modeling workflow
instead of silently inventing competing vocabulary.

## ADR conflicts

Surface any conflict with an accepted ADR explicitly. Do not silently override
or reinterpret an accepted decision.
