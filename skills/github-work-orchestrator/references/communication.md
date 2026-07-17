# Paseo campaign recovery

Use this reference after a missed callback, Agent error, parent restart, daemon
restart, or ambiguous create response.

1. Stop new dispatch for the affected Issue.
2. Replay the bounded campaign room and deduplicate Signal-IDs.
3. List Agents using campaign/dispatch and `paseo.parent-agent-id` labels.
4. Inspect lifecycle, worktree, branch, commit/PR, and GitHub claim.
5. Continue the exact idle Agent, preserve an active one, or record a durable
   blocked/stopped handoff.
6. Create a successor only when the predecessor is proven terminal, no editor
   remains, and useful WIP is pushed/integrated or deliberately preserved.

Finish notification and mention delivery are best effort and may be lost across
restart. A chat message is durable but its claimed author is not authentication.
Never infer terminal state from age, silence, or a missing UI row.
