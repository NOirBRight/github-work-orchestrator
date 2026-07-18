# Entry routing and Workspace model

Run entry routing before reading GitHub work. Parentage and Workspace answer
different questions: parentage controls supervision/notification/cleanup;
Workspace controls the sidebar entry and file context.

## Bootstrap matrix

| Observed state | Route |
|---|---|
| No Coordinator; current root in stable repository Workspace | Promote current Agent in place |
| No Coordinator; current Agent in Issue/Campaign/Dispatch worktree | Create in one uniquely read-backed stable `dev` Workspace |
| One different Coordinator | Become an Operator Relay |
| Current Agent is the one Coordinator | Replay Repository Room and continue |
| Duplicate Coordinator or ambiguous stable Workspace | Fail closed and preserve all Agents |

Coordinator Home may be dirty or not on `dev`; it is conversation state, not an
integration precondition. The Integration Control Worktree is selected by
explicit readback and passed to every Git command. Never move the conversation
or delete its Workspace to obtain a clean `dev`.

## Operator Relay fast path

Rename the ordinary Task `Relay · <repo> → Coordinator`. In at most five
external actions:

1. validate one unique Coordinator;
2. post one sanitized Repository Room `OPERATOR_REQUEST` containing a bounded
   summary and SHA-256 of the original message;
3. read Coordinator status exactly once;
4. if idle, send a prompt containing only the Signal-ID; if running or
   initializing, do not prompt; and
5. leave the room message UUID/status receipt and idle.

The Coordinator record must be an exact read-backed root Agent with null parent
and matching repository/`repository-coordinator` labels; a foreign or
mislabeled Agent is never a Relay target. The Relay does not inspect GitHub,
worktrees, Campaign rooms, Workers, or PRs.
This bounded path prevents repository reconciliation from delaying the first
durable forward. Duplicate request Signal-ID + identical payload is a retry;
conflicting payload blocks the sender.

## Repository Room

Use `repository_room.py` and its deterministic `gwo-repo-<slug>-<digest>` room.
The digest prevents repositories whose slugs collide from sharing a mailbox.
Its schema remains v1 and accepts only `OPERATOR_REQUEST`, `REQUEST_ACCEPTED`, and
`REQUEST_REJECTED`. Verify sender Agent, repository/role labels, author, strict
sequence, and response correlation on replay.

A conflicting request Signal-ID poisons the request, not only its Relay sender.
Responses correlated before or after that conflict are filtered and cannot be
acted on.

The room is a persistent mailbox, not business truth. Payloads contain no
credential, private prompt, absolute path, or full original message. The
Coordinator replays it at startup, immediately before waiting, and immediately
before ending a turn so the post/status race cannot lose work.

## Campaign admission transaction

`campaign_workspace.py create-plan` emits one Paseo create operation with
`workspace=create/worktree`, `relationship=subagent`, exact `dev` base,
`gwo/campaign/<id>`, local-only branch, and Campaign Provider Binding. Follow it
with readback and Workspace rename. Admission is complete only after
`validate-readback` succeeds.

Validation derives the expected parent, names, branch, head, Provider Binding,
labels, and worktree slug from read-backed Coordinator/base/provider evidence.
A caller-provided `expected` object is never an authority source.

If create or readback is partial, preserve every returned Agent/worktree and
reconcile by identity; never retry blind or roll back a resource whose ownership
is uncertain. The control branch has no PR and no feature work. Tracked changes,
unique commits, or publication stop new Dispatch until a human-safe recovery.
