# Paseo Worker dispatch contract

Create one Paseo-managed implementation Agent only after the Issue claim,
`dev` base SHA, campaign room, role preference, permissions, hotset,
verification, and `done_when` are complete.

The private v3 contract contains campaign/dispatch IDs, Issue/repository,
Agent role/category, exact base/worktree/branch/PR target, permission profile,
hotset, acceptance, verification, room, and return evidence. It contains no
fixed Provider, model, native Task ID, or callback thread.

Resolve the Provider at dispatch time from the explicit override or Paseo
orchestration preferences, then validate availability. Resolve the highest
unattended execution mode from advertised mode metadata; never use a
Provider-name lookup table. Create with relationship `subagent`,
`notifyOnFinish: true`, that mode, an isolated worktree from `dev`, and
campaign/dispatch/Issue labels. Read back the parent Agent ID and mode before
publishing `START`.

If a Provider still requests permission, Paseo notifies the parent. The parent
may allow only non-destructive work already covered by the permission profile
and hotset. Deny and block on ambiguity or scope expansion. After restart,
reconcile the pending-permission list because notification is not durable state.

The Agent must post `AGENT_READY` after room and repository preflight. Publish
`START` to the room only after the claim and one-editor readback remain valid.
Room events are replayable coordination, while GitHub/Git/Paseo state supplies
authorization evidence.

Do not send a follow-up to a busy Agent. When idle, wake the same Agent with a
pointer to the exact room message UUID. Create a successor only after terminal
proof, durable WIP, and released ownership.
