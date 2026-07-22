# Paseo Worker execution core

Load this reference only for one assigned Paseo implementation Agent. The Issue
owns acceptance, the repository owns verification, and the Orchestrator owns
claim, review, integration, and cleanup.

## Contract gate

Require one valid v3 contract with exact Issue/repository, `dev` base SHA,
`work/issue-...` branch, worktree, Task Group/dispatch IDs, hotset,
permissions, verification, and `done_when`. Stop before edits when any field,
ownership, permission, or architecture decision is unresolved.

The Worker is one Paseo Agent. It must not invoke Provider-native Agent, Task,
Swarm, or subagent features. An explicit request for native orchestration is a
separate non-GWO top-level Task, never a child of this Dispatch.

Also require `relationship: subagent`, exact `parent_agent_id`,
`notify_on_finish: true`, and the dynamically resolved `runtime_mode_id`. The
permission profile uses `approval: never` and
`unexpected_request_fallback: parent`. Before `status`, inspect the live
Agent and fail closed unless parentage and mode match exactly.

## Store preflight and activation

Run the packaged repository preflight and verify `PASEO_AGENT_ID` and
`GWO_AGENT_ID` are available. The Worker identity comes from `GWO_AGENT_ID`;
the CLI refuses any event whose identity/role pair is not entitled.

Post `status`, then wait. Begin only after a valid `START` event for the same
dispatch. The initial prompt may contain the contract, but the store mailbox is
the coordination record.

Replay and wait with `gwo inbox --agent-id <self> --dispatch-id <exact-dispatch-id>`.
A Worker never unscoped-replays Task Group lifecycle or sibling history, and its
consumer view ignores Coordinator-owned `review_result` events. Missing identity
makes in-scope events non-actionable. Do not hand-author identity columns or
construct evidence from mailbox claims.

## Execution

Stay in the assigned worktree and hotset. At safe phase boundaries replay your
inbox. Do not use mentions for routine updates or send prompts to busy Agents.
Post one blocking `ask` before durable architecture, compatibility,
security/privacy, migration, or cross-Issue decisions. Resume only after a
correlated `reply` backed by durable GitHub decision readback.

Treat any direct user instruction as an `ask` before acting. An in-contract
clarification may continue after Coordinator `reply`. Scope, Hotset,
architecture, compatibility, security, or integration expansion requires the
GitHub decision gate; never broaden authority from a direct prompt alone.

Implement the smallest accepted vertical change. Run targeted checks, then the
required suite/manual evidence exactly once per locally green candidate. The
Worker does not spawn another work item or perform formal review.

Post `heartbeat` after investigation, after each independent implementation
phase, before and after long verification, and at the next safe boundary when
five minutes passed without a runtime signal. The five-minute target is not an
SLA: do not interrupt a running command merely to post. Use `status` instead
when evidence materially changed. `heartbeat` never advances lifecycle and stops
after `worker_done`, `BLOCKED`, or `STOPPED`.

## Publication

Commit and push the assigned branch, open/update the PR against `dev`, and post
`worker_done` with exact evidence. Publish it through the gwo kernel:

```text
python <skill>/scripts/gwo.py done --task-id <task> --dispatch-id <dispatch> --status done --evidence <json>
```

Then run the packaged `material_delivery.py delivery-plan`. Wake only an idle
Coordinator using the exact returned signal-only action, post `DELIVERY_WAKE`
after an accepted send, and remain in bounded inbox waits until the Coordinator
identity-verifies the source and posts `DELIVERY_ACK`. A busy Coordinator receives
no prompt. ACK proves receipt only; the Coordinator still verifies Agent, Git,
GitHub, and test state before marking complete and before any merge.

The high-autonomy mode prevents routine prompts. If a Provider still requests
permission, pause without retrying: Paseo notifies the parent. The parent may
approve only non-destructive work already covered by the v3 permissions and
hotset; otherwise it denies and records `BLOCKED` for human direction.

On host or permission failure, stop editing, preserve useful WIP durably when
possible, post `BLOCKED` or `STOPPED`, and keep the worktree intact. Never reset,
force-clean, or activate a successor yourself.
