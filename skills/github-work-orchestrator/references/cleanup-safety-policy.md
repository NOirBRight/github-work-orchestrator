# GWO cleanup safety policy

GWO owns cleanup authorization. It evaluates observed Paseo/Git/worktree/room
evidence and invokes existing Paseo operations. It is not a daemon adapter and
requires no host, sidecar, supervisor service, or source modification.

## Typed targets and resources

`cleanup-plan` v4.3 keeps output `schema_version: 2` and requires explicit:

| Target | `target_kind` | `resource_kind` |
|---|---|---|
| Implementation/Intake/Monitor Worker | `worker` | `issue-worktree` |
| Spec or Quality Reviewer | `worker` | `none` |
| New Campaign | `campaign` | `campaign-control` |
| Legacy v4.2 Campaign | `campaign` | `none` |
| Probe/forward-test | `ephemeral` | `none` |

Do not infer these fields from role or path. Actor, target, execution, and
terminal receipt carry exact matching repository/campaign/dispatch identity.

## Agent phase

An Agent may archive only its direct idle child. It cannot force, archive
itself, a root/sibling/detached/foreign Agent, or a child with uncertain
identity. A Campaign cleans its Workers and Reviewers. The Coordinator cleans a
Campaign only after a repository/campaign-scoped direct-child enumeration is
explicitly read back, all child Agent IDs are absent, and `CAMPAIGN_CLOSED` is
read back. An omitted/default empty list is not proof. Root retirement is
human-only after durable handoff.

Target type also fixes ownership: `worker` requires a Campaign actor and a
Worker/Reviewer role; `campaign` requires the Repository Coordinator and an
Orchestrator target; `ephemeral` permits only normal probe roles such as
Monitor/Review/Implementation/Intake and never an Orchestrator. A lifecycle
label cannot downgrade a Campaign into the ephemeral STOPPED path, and the
Coordinator cannot bypass the Campaign to clean an ordinary Worker.

Stable denial codes remain represented by the GWO blockers derived from:

- `SELF_ARCHIVE_FORBIDDEN`
- `ROOT_ARCHIVE_REQUIRES_SUPERVISOR`
- `ARCHIVE_TARGET_NOT_DIRECT_CHILD`
- `FORCE_REQUIRES_SUPERVISOR`
- `AGENT_NOT_IDLE`

`SUPERVISOR` names the existing human-authorized caller class; GWO does not add
a supervisor component.

## Resource phase

Delegated cleanup is staged. The first eligible plan contains only the child
Agent archive. Execute it, then read back archived Agent state and removal of
all worktree bindings before a second plan may contain resource actions. Return
`WORKTREE_IN_USE` for any remaining Agent binding and
`CONTROL_WORKTREE_PROTECTED` for Coordinator Home, Integration Control, or the
actor's own worktree.

An Issue worktree must be absolute, clean, durable, uniquely owned, and on
`work/issue-*`; `event=merged` also requires `branch_merged: true`.

A new Campaign Control Workspace additionally requires:

- every Worker and both Reviewers already cleaned;
- archived Campaign Agent and no worktree binding;
- local `gwo/campaign/<id>` branch;
- exact read-backed worktree slug `campaign-<id>` bound to the same Campaign;
- clean tracked state, zero unique commits, no push/PR; and
- separate readbacks after Workspace archive and after local branch deletion.

Never emit Campaign worktree archive and branch deletion in the same pass.
Archive the Campaign Agent, read back unbound; archive the worktree, read back
absence; only then delete the exact local control branch and read back absence.

Legacy v4.2 Campaigns explicitly state `campaign_generation=legacy-v4.2`, a
read-backed `campaign_control_expected: false`, and Agent-only cleanup. New
Campaigns translate the read-backed `gwo.version=4.3` label to cleanup evidence
`campaign_generation=v4.3` with control expected true; claiming
`resource_kind=none` or mismatching generation/resource fails closed.

Ephemeral Probe/forward-test cleanup uses a normal runtime role (usually
`monitor`) plus `target_kind=ephemeral`; `ephemeral` is not an Agent role. It
requires a direct idle child, read-backed absence of a worktree, a read-backed
`gwo.lifecycle=ephemeral` label, exact STOPPED receipt, and result-captured
readback.
Provider-native timeline entries are outside GWO ownership and are never
cleaned through this path.

## Execution rule

`cleanup_policy.py` owns the complete typed plan;
`archive_policy.py` is its smaller pure Agent/worktree primitive. An eligible
nonempty plan sets `automatic_execution: true`; execute exactly its ordered
actions and read back every mutation. A protected plan sets false and returns
no actions. Missing, contradictory, active, dirty, shared, or foreign evidence
fails closed without partial actions.

Only `COMPLETED`/`STOPPED` can retire Worker-like targets, and only
`CAMPAIGN_CLOSED` can retire a Campaign. HEARTBEAT, CHECKPOINT, WORKER_DONE,
REVIEW_RESULT, DELIVERY_WAKE, and DELIVERY_ACK are never terminal cleanup
evidence. `CAMPAIGN_CLOSED` never targets the Coordinator.
