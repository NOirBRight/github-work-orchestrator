---
status: amended by ADR-0059
---

# Define Runtime adapters by execution model

RuntimeGateway owns one private, capability-oriented adapter seam rather than
a provider-shaped public interface or a fixed operation count. Paseo is its
first resident-Agent adapter; direct Codex CLI and Claude Code session-process
adapters are compatible future implementations. A provider name, model name,
and execution model remain separate facts.

An adapter must idempotently materialize and read back Agent, workspace,
session, Runtime Binding, and stable action identity; accept Artifact-backed
Prompt input; observe lifecycle, permission, and event state; park or interrupt
without losing required identity; resume; fence terminal bindings; and execute
read-backed retirement already authorized by ExecutionKernel.

An in-memory test fake may exercise the Paseo-shaped contract, but it does not
count as a second production adapter or freeze a universal cross-Runtime
interface. The private seam may be redesigned when another adapter exposes
materially different lifecycle behavior.

Every materialized resource round-trips repository, Campaign, Plan Revision,
Work Run, Runtime action, Agent, session, and workspace identity. Events
accelerate wake-up but never replace authoritative readback. Adapter
capabilities cannot grant authority: exact permissions remain covered by the
Work Run's Authority Grants and Policy Witness.
