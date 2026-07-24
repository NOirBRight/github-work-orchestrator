---
status: amended by ADR-0035
---

# Activate one Repository Plan Revision at a time

V8.0 has one active Plan Revision per repository, covering all Work Items that
may receive new admissions. Task Groups remain labels, not independent plan
authorities. A new revision may supersede unstarted nodes, but a running or
result-bearing Attempt remains permanently bound to the revision under which
it began. Activating a revision does not stop that Attempt; cancellation still
requires authority from its existing contract or a durable Decision Gate.
New work discovered within an existing Goal, Effect Contract, and external
effect boundary may be added through another Plan Revision. Expanding the Goal,
authority, or external effects requires a Decision Gate before activation.

When new input changes a Task Group Goal, acceptance condition, or executable
plan, the current Coordinator is woken and new admissions for that affected
Goal enter a Replan Hold until the replacement revision is active. Existing
Attempts continue under their original contracts. An existing Admission also
remains pinned to its original Plan Revision and is never silently retargeted.
If its Node Key and contract are unchanged it may continue Materialization. If
the node changed or disappeared, cancellation must be explicit and any possibly
created runtime must be read back before claims are released. Cancellation
before an Attempt begins is not an Attempt terminal reason.

A replacement node for the same work cannot be admitted alongside its
non-terminal predecessor or unreconciled Admission; it waits for terminal
readback or an explicitly authorized cancellation and claim release. Deliberate
parallel exploration is represented as distinct Plan Nodes rather than hidden
replacement concurrency.

When an authorized replan explicitly ends an obsolete Attempt, its terminal
classification is superseded rather than failed or rejected. Revision
activation by itself still does not imply that cancellation.
