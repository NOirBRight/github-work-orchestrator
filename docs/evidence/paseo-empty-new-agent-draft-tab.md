# Issue-ready evidence: atomic Paseo Agent create leaves an empty draft tab

Status: prepared for upstream triage; not published.

## Summary

Creating one Paseo Agent with one atomic `workspace=create/worktree` operation
can leave two tabs in the resulting Workspace: an empty `New Agent` draft tab
and the real Agent tab. Paseo Agent/CLI readback reports only the real Agent.

## Reproduction

1. From a parent Paseo Agent, create one child using the public Paseo Agent API.
2. Set relationship to `subagent`, choose `workspace=create/worktree`, and
   supply provider/model/mode plus the initial prompt in that one operation.
3. Open the created Workspace in Paseo Desktop.

## Observed

- Sidebar contains the new Workspace.
- Its tab strip contains an empty `New Agent` draft first and the created Agent
  second.
- Agent list/readback contains one child Agent, not two.
- No separate GWO create operation was issued for the draft.

## Expected

An atomic Agent create should open/select the created Agent tab without leaving
an unrelated blank draft, or the UI should clearly mark the draft as local UI
state that consumes no Agent slot.

## Impact

Operators infer an extra/zombie task and must inspect the second tab to find the
real Worker. GWO cannot remove this UI draft through Paseo Agent APIs because it
has no Agent identity.

## Acceptance

- Atomic create produces one visible Agent tab, or closes/suppresses the empty
  draft automatically.
- Agent/workspace counts remain unchanged.
- Existing manually opened drafts continue to work.
