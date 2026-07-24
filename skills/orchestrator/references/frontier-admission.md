# Frontier admission

Frontier admission keeps backlog triage separate from execution. A scan is
read-only; only an explicit, decision-complete admission creates durable
Orchestrator state.

## Configure intake

Global `intake` values may be overridden per repository:

```json
{
  "include_labels": ["ready-for-agent"],
  "human_labels": ["ready-for-human"],
  "clarify_labels": ["needs-info"],
  "candidate_limit": 100,
  "ready_reserve_target": 6
}
```

`candidate_limit` is 1-100. A paginated pool fails closed; narrow it with the
repository's intake labels. Label policy is only a prefilter. The Coordinator
still reads the Issue, resolves product decisions, and sanitizes the contract.
The three `orch:*` states are included automatically for status and idempotent
Admission readback. Configure at most 12 distinct labels; full comments are
fetched only for proposed targets.

## Scan

```text
python <skill>/scripts/orch.py frontier scan --repo owner/repo
```

The result includes raw Candidate facts for Coordinator review, one assessment
per Issue (`design`, `human`, `clarify`, `defer`, or `managed`), Ready Reserve,
reserve gap, current wave selection, both free capacities, Parallel Width, and
a starvation flag. `WAVE_SEARCH_BOUNDED` means a worst-case conflict graph used
the best safe subset found within its deterministic budget. Raw bodies remain
untrusted and must not be passed to a Worker.

## Admit

Prepare one local, short-lived plan:

```json
{
  "schema_version": 1,
  "repository": "owner/repo",
  "admissions": [
    {
      "issue": 23,
      "contract": {
        "design": ["decision-complete sanitized steps"],
        "acceptance": ["observable outcome"],
        "change_claims": {
          "paths": ["src/api"],
          "resources": ["schema:settings"]
        },
        "done_when": ["python -m pytest tests/api -q"],
        "dependencies": {
          "dispatch_after": [],
          "merge_after": [19]
        },
        "priority": "P1",
        "difficulty": "standard",
        "risk": "standard",
        "unresolved_decisions": [],
        "sha256": "canonical contract hash"
      }
    }
  ]
}
```

Compute `sha256` with `orch_core.contract_hash`, build fresh Coordinator
context, then run:

```text
python <skill>/scripts/orch.py frontier admit --repo owner/repo --plan admission.json --coordinator-context context.json
```

The CLI validates every V2 hash, unique target, self-reference, cross-frontier
dependency cycle, referenced Issue, existing managed identity, Coordinator
authority, and repository before the first write. It then writes one
`orchestrator:issue:v2` comment and `orch:ready`. An interrupted retry with the
same contract is idempotent; a different contract or active Dispatch fails
closed. Delete the local plan and Coordinator context after use.

Contract V1 remains runnable. Its Hotset becomes path claims and its dependency
list acts as both `dispatch_after` and `merge_after`; V1 records are not rewritten
until a human-approved design change creates a new V2 contract.
