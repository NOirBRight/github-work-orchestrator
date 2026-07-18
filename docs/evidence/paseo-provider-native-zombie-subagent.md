# Issue-ready evidence: provider-native terminal subagent remains shown running

Status: prepared for upstream triage; not published.

## Summary

A Provider-native subagent can report a terminal/completed result in its native
timeline while Paseo Desktop continues to show a generic `Sub-agent · running`
row. The row has no Paseo Agent ID and is absent from Paseo Agent list/readback.

## Reproduction

1. Run a Provider task that uses the Provider's native subagent feature inside
   one Paseo Agent.
2. Let the native subagent return a terminal result.
3. Inspect the parent timeline and its subagent panel after completion.

## Observed

- Provider response reports completion.
- The generic native subagent row remains `running`.
- Paseo Agent APIs cannot inspect/archive the row because it is not a Paseo
  Agent.

## Expected

Paseo Desktop should derive a terminal display state from the Provider timeline
or clearly mark the row as Provider-owned/non-Paseo rather than an active Paseo
Agent.

## Impact

Operators mistake the row for a GWO Worker, capacity slot, or cleanup leak.
GWO cannot safely clean it because no Paseo parentage/identity receipt exists.

## Acceptance

- Terminal Provider-native rows stop showing `running`.
- UI distinguishes Provider-native entries from Paseo Agents.
- Paseo Agent lifecycle/counts remain based only on Paseo Agent records.
