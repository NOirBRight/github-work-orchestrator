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

For a new addressed material event, use `post-material` plus
`material_delivery.py`. A pending source, accepted `DELIVERY_WAKE`, and exact
recipient `DELIVERY_ACK` are separately replayable. Recovery resumes that state
machine: pending+idle wakes once, pending+running waits, wake-sent+running waits
for ACK, and wake-sent+idle is protected/escalated. Do not reinterpret Wake or
ACK as completion evidence.

HEARTBEAT is a Worker room event, not Coordinator polling. A normal 60-second
`chat wait` timeout causes room replay only. At 15 minutes without a valid
runtime signal, use `coordinator_loop.py` for one state/timeline inspection; do
not inspect again for 15 minutes without new evidence. Leave a running Agent
alone, prompt an idle non-terminal Agent once, preserve error/closed WIP, and
escalate ambiguous identity. Silence never authorizes a successor.
