# Issue-ready evidence: rejected native subagent spawn remains shown running

Status: prepared for upstream triage; not published.

## Summary

A Provider-native `spawn_agent` call rejected before Agent identity readback can
leave a generic `Sub-agent · running` row in the parent UI. The row has no
Agent ID and is absent from both native collaboration readback and Paseo Agent
readback.

## Reproduction

1. Inside one Paseo-managed Codex Agent, call native `spawn_agent` with an
   invalid combination such as `fork_turns: all` plus an explicit
   `agent_type`.
2. Observe that the call is rejected before an Agent ID or `started` event is
   returned.
3. Retry with valid settings and let all real subagents complete.
4. Inspect the parent subagent panel.

## Observed

- Native collaboration readback reports only the successfully started,
  terminal named subagents.
- The generic native subagent row remains `running`.
- The generic row has no Agent ID, path, or lifecycle event and therefore
  cannot be inspected, interrupted, or archived.
- In the captured parent session, 15 `spawn_agent` calls produced 12 `started`
  events; three pre-ID rejections used the same invalid full-history/explicit
  role combination. The UI showed 12 real named subagents plus one generic
  running row.
- At least 13 local session logs contained more spawn calls than started
  subagents; most unmatched calls were pre-ID validation rejections.

## Expected

The client should settle or remove its provisional row whenever spawn returns
an error before identity creation. Replay should derive visible subagents from
identified lifecycle events, not unmatched tool-call starts.

## Impact

Operators mistake the row for a GWO Worker, capacity slot, or cleanup leak.
GWO cannot safely clean it because no Agent exists. Repeated invalid spawn
calls make the symptom recur across conversations.

## Caller-side avoidance

- With a full-history fork, omit `agent_type`, `model`, and
  `reasoning_effort`; those settings inherit from the parent.
- To override model or role, use no history or a bounded history fork and send
  a complete fixed task packet.
- Validate the combination before dispatch.
- Use a stable child action key and create no running child record until Agent
  identity readback.

## Acceptance

- Pre-ID spawn rejection removes or terminally settles the provisional row.
- UI distinguishes Provider-native entries from Paseo Agents.
- Paseo Agent lifecycle/counts remain based only on Paseo Agent records.
- Valid retry creates exactly one identified visible child.
