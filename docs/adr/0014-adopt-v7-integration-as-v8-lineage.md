---
status: superseded by ADR-0034
---

# Adopt the V7 integration line as the V8 lineage

The `v7-integration` line is the sole product lineage for V8. The divergent
V6.1 `dev` line becomes legacy and is not merged wholesale; proven hardening
may be ported selectively. After its transition audit, V7.1 is merged to
`main` as a frozen baseline and may receive a prerelease tag, but is not
presented as a complete standalone product release. The transition audit is
the re-scoped Issue 27 freeze audit; the original Issue 26 five-operation
Runtime Port is explicitly not a prerequisite and moves to a new V8
capability-oriented boundary.
