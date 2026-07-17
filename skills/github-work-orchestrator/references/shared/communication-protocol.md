# Paseo campaign communication protocol

Paseo room messages are the primary coordination surface. GitHub Issues, PRs,
commits, checks, and repository decisions remain the durable business truth.
Agent finish notifications and mentions are wake-up accelerators, never proof.

## Campaign room

Create exactly one room named `gwo-<campaign-id>` before dispatch. Every
participating Paseo-managed Agent must pass a read/post preflight through the
packaged `paseo_room.py` helper and must have `PASEO_AGENT_ID` available.

Each material event is a v1 JSON envelope containing:

```text
schema_version, signal_id, campaign_id, dispatch_id, sequence, event_type,
issue, sender_agent_id, recipient_agent_id, evidence, next_action
```

The chat message UUID is a publish receipt. It proves that the daemon stored a
message, not that the claimed author or evidence is true. Re-read Agent state,
GitHub, Git, the worktree, and verification evidence before acting.

## Delivery and wake-up

1. Publish the complete event to the campaign room.
2. Consumers replay the bounded room and deduplicate by `signal_id`.
3. Use `chat wait` only as a wake-up accelerator; after every wake or timeout,
   replay the room so the CLI read/wait race cannot lose an event.
4. Do not mention or send a prompt to a busy Agent. A prompt may replace its
   active run. Record the work in the room and let the Agent read it at its next
   safe checkpoint.
5. When the target is verified idle, `send_agent_prompt` may point it to the
   exact room message UUID. The room remains the authoritative communication.

Retries reuse the same `signal_id`. A conflicting duplicate is invalid and
blocks the affected dispatch until reconciled.

## Event states

Use only material states: `CAMPAIGN_OPENED`, `AGENT_READY`, `START`, `PROGRESS`,
`DISCUSSION_REQUIRED`, `BLOCKED`, `PR_OPENED`, `READY_FOR_REVIEW`,
`REVIEW_RESULT`, `COMPLETED`, `STOPPED`, `CHECKPOINT`, and `CAMPAIGN_CLOSED`.

Worker terminal evidence includes branch, commit/PR, verification class,
commands and outcomes, phase timings, changed paths, scope delta, blockers, and
next action. Formal review remains Orchestrator-owned.

## Recovery

Finish callbacks are process-local convenience signals. After a daemon or
parent restart, replay the campaign room, list Agents by campaign/dispatch and
`paseo.parent-agent-id` labels, inspect their lifecycle, then reconcile GitHub
and worktrees. Also list pending Paseo permissions: a `permission_requested`
event notifies the parent when the Worker is a Paseo `subagent`, but that
notification can be lost across restart. The parent may approve only a
non-destructive request already authorized by the v3 permission profile and
hotset; deny and block every ambiguous or expanded request. Never create a
successor until the predecessor is proven terminal and ownership is
unambiguous.

## Completion

Write the durable result to GitHub before posting `CAMPAIGN_CLOSED`. Delete a
completed room only after that readback succeeds. Preserve rooms for blocked or
handed-off campaigns. Never place credentials, provider tokens, local paths, or
private prompts in room or GitHub messages.
