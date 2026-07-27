# Domain Docs

This is a single-context repository.

## Normative hierarchy

Resolve current V8 documentation in this order:

1. `CONTEXT.md` owns ubiquitous language and contains no mechanics.
2. Accepted ADRs own individual decisions. Their `amends`, `supersedes`, and
   reciprocal status metadata determine which decision is current.
3. `docs/design/gwo-v8-lean-architecture.md` is the sole integrated current V8
   mechanics contract and must compose the accepted ADRs without redefining
   glossary terms.
4. `docs/design/gwo-v8-lean-stabilization-spec.md` is the dated requirement,
   successor-Ticket source, and acceptance record for Issues #108–#119. It is
   subordinate to and links to the architecture for mechanics.
5. `docs/design/gwo-v8-lean-roadmap.md` owns delivery sequencing and exit
   criteria only.

The older V7 and V8 architectures and roadmaps are unadopted or superseded
historical migration records and must not generate new implementation work.
If current documents disagree, apply the order above and repair the
lower-precedence document.

## Before exploring

- Read `CONTEXT.md`.
- Read the accepted ADRs governing the area being changed.
- For V8 mechanics, read `docs/design/gwo-v8-lean-architecture.md`.
- Proceed silently if a referenced domain document does not yet exist.

## Use the glossary vocabulary

Use terms exactly as defined in `CONTEXT.md` in Issue titles, implementation
plans, and user-facing Review Findings. Implementation-private records may use
private names inside their owning deep module, but must not expand the
ubiquitous language or leak into another module's interface.

If a needed concept is absent, first decide whether the proposed term is
unnecessary. Record a genuine domain gap through the domain-modeling workflow
instead of silently inventing competing vocabulary.

## ADR conflicts

Surface any conflict with an accepted ADR explicitly. Do not silently override
or reinterpret an accepted decision.
