---
status: accepted
---

# Define Runtime Adapters by execution model

The V8 Runtime Adapter seam is capability-oriented rather than fixed to a number
of operations. Paseo is its first real resident-agent adapter; direct Codex CLI
and Claude Code session-process adapters are intended future implementations.
Provider and model selection remains a separate binding within an adapter, so
the word "Codex" alone must not stand for both runtime and model.

The V8.0 Paseo Adapter must be able to idempotently materialize and read back
Agent/workspace/session identity, deliver and confirm an initial Prompt, observe
lifecycle/binding/permission/events, interrupt a current turn while preserving
its session and workspace, resume that session, and execute read-backed
retirement actions already authorized by the Kernel. These are capabilities,
not a mandate to expose a fixed operation count.

An in-memory test fake may exercise the Paseo-shaped contract, but it does not
count as a second production Adapter or freeze a universal cross-runtime
interface. The seam may be redesigned when a real second Adapter exposes
materially different lifecycle behavior.

Every materialized runtime resource must persist and return its GWO
`repository_id`, Plan Revision digest, Node Key, and Admission ID, plus Attempt
ID after the Attempt begins. An Adapter that cannot round-trip these identities
is not eligible for V8 write execution because Store recovery could not
distinguish adoption from duplicate creation.

Coordinator Runtime Requirements include persistent-session resume so Task
Group Goal continuation survives turn boundaries. A Worker may be disposable;
if its runtime cannot resume, replacement begins a new Attempt. Mid-turn steer
is optional. An adapter that lacks it waits for a safe boundary or uses
interrupt followed by resume rather than pretending that a running Agent
accepted another Prompt.

Runtime events accelerate wake-up but never replace authoritative readback by
Agent, session, or action identity. `interrupt` preserves a resumable runtime;
`retire` executes a Kernel cleanup decision after ownership ends. Adapter
support cannot grant cleanup authority. The Adapter also distinguishes an
active turn from a retained idle session and reports observable Runtime
availability when its execution model exposes it; retained identity alone does
not consume a GWO Active Turn Slot.
