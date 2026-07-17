# Paseo Worker execution core

Load this reference only for one assigned Paseo implementation Agent. The Issue
owns acceptance, the repository owns verification, and the Orchestrator owns
claim, review, integration, and cleanup.

## Contract gate

Require one valid v3 contract with exact Issue/repository, `dev` base SHA,
`work/issue-...` branch, worktree, campaign/dispatch IDs, room, hotset,
permissions, verification, and `done_when`. Stop before edits when any field,
ownership, permission, or architecture decision is unresolved.

Also require `relationship: subagent`, exact `parent_agent_id`,
`notify_on_finish: true`, and the dynamically resolved `runtime_mode_id`. The
permission profile uses `approval: never` and
`unexpected_request_fallback: parent`. Before `AGENT_READY`, inspect the live
Agent and fail closed unless parentage and mode match exactly.

## Room preflight and activation

Run the packaged repository preflight and:

```text
python <skill>/scripts/paseo_room.py preflight \
  --room <gwo-campaign-id> --require-agent-identity
```

Post `AGENT_READY`, then wait. Begin only after a valid room `START` event for
the same dispatch. The initial prompt may contain the contract, but the room is
the campaign communication record.

## Execution

Stay in the assigned worktree and hotset. At safe phase boundaries replay the
room. Do not use mentions for routine updates or send prompts to busy Agents.
Post `DISCUSSION_REQUIRED` before durable architecture, compatibility,
security/privacy, migration, or cross-Issue decisions.

Implement the smallest accepted vertical change. Run targeted checks, then the
required suite/manual evidence exactly once per locally green candidate. The
Worker does not spawn another work item or perform formal review.

## Publication

Commit and push the assigned branch, open/update the PR against `dev`, and post
`PR_OPENED` followed by `READY_FOR_REVIEW` or `COMPLETED` with exact evidence.
The Orchestrator verifies room, Agent, Git, GitHub, and test state before merge.

The high-autonomy mode prevents routine prompts. If a Provider still requests
permission, pause without retrying: Paseo notifies the parent. The parent may
approve only non-destructive work already covered by the v3 permissions and
hotset; otherwise it denies and records `BLOCKED` for human direction.

On host or permission failure, stop editing, preserve useful WIP durably when
possible, post `BLOCKED` or `STOPPED`, and keep the worktree intact. Never reset,
force-clean, or activate a successor yourself.
