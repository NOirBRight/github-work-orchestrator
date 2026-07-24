# Runtime configuration

`~/.orch/config.json` is optional, local, and re-read for each command. GitHub
stores only Difficulty Tier; concrete runtimes never become repository truth.

```json
{
  "schema_version": 1,
  "global": {
    "default_tier": "standard",
    "execution_slots": 3,
    "integration_wip_limit": 6,
    "max_attempts": 2,
    "intake": {
      "include_labels": ["ready-for-agent"],
      "human_labels": ["ready-for-human"],
      "clarify_labels": ["needs-info"],
      "candidate_limit": 100,
      "ready_reserve_target": 6
    }
  },
  "tiers": {
    "light": {
      "provider": "kimi-cli",
      "settings": {
        "model": "kimi-code/kimi-for-coding",
        "thinkingOptionId": "on",
        "modeId": "yolo",
        "features": {}
      }
    },
    "standard": {
      "provider": "kimi-cli",
      "settings": {
        "model": "kimi-code/kimi-for-coding",
        "thinkingOptionId": "on",
        "modeId": "yolo",
        "features": {}
      }
    },
    "heavy": {
      "provider": "kimi-cli",
      "settings": {
        "model": "kimi-code/k3",
        "thinkingOptionId": "high",
        "modeId": "yolo",
        "features": {}
      }
    },
    "frontier": {
      "provider": "codex",
      "settings": {
        "model": "gpt-5.6-sol",
        "thinkingOptionId": "xhigh",
        "modeId": "full-access",
        "features": {}
      }
    }
  },
  "role_profiles": {
    "coordinator_auto": {
      "provider": "kimi-cli",
      "settings": {
        "model": "kimi-code/k3",
        "thinkingOptionId": "max",
        "modeId": "yolo",
        "features": {}
      }
    },
    "reviewer_standard": {
      "provider": "codex",
      "settings": {
        "model": "gpt-5.6-sol",
        "thinkingOptionId": "high",
        "modeId": "full-access",
        "features": {}
      }
    },
    "reviewer_strict": {
      "provider": "codex",
      "settings": {
        "model": "gpt-5.6-sol",
        "thinkingOptionId": "max",
        "modeId": "full-access",
        "features": {}
      }
    },
    "reviewer_recovery": {
      "provider": "codex",
      "settings": {
        "model": "gpt-5.6-sol",
        "thinkingOptionId": "max",
        "modeId": "full-access",
        "features": {}
      }
    }
  },
  "reviewer_tiers": {
    "standard": "standard",
    "strict": "heavy"
  },
  "repositories": {
    "owner/repo": {
      "integration_branch": "dev",
      "merge_method": "squash",
      "workspace_id": "optional-paseo-workspace-id",
      "execution_slots": 3,
      "integration_wip_limit": 6,
      "default_tier": "standard",
      "milestone_tiers": {"release-name": "heavy"},
      "tiers": {},
      "role_profiles": {},
      "project_number": null
    }
  }
}
```

## Coordinator context

Every state-changing `frontier admit`, `reconcile`, `integrate`, `retire`, or
`project` command requires `--coordinator-context PATH|-`. The Skill creates
this short-lived JSON from the latest Paseo MCP, collaboration-mode, and Git
readbacks:

```json
{
  "schema_version": 1,
  "actor": {
    "id": "paseo-agent-id",
    "cwd": "current-workspace-cwd",
    "workspace_id": "current-workspace-id",
    "provider": "provider-id",
    "settings": {"model": "model-id", "modeId": "write-mode"}
  },
  "current_workspace": {
    "id": "current-workspace-id",
    "repository": "owner/repo",
    "branch": "dev",
    "relationship": "root",
    "dirty": false,
    "pr_head": false,
    "ephemeral": false,
    "worker": false,
    "agent_cwd_matches": true
  },
  "candidate_workspaces": [],
  "mode": {
    "collaboration_mode": "default",
    "write_capable": true,
    "colorTier": "dangerous"
  },
  "features": {"plan_mode": false},
  "remote_branches": ["dev", "main"],
  "active_root_agents": [],
  "request": "the original request, used only if forwarding is required"
}
```

Each candidate Workspace uses the same Workspace fields. Each active root entry
has at least `id` and `workspace_id`. `write_capable` must be a positive fresh
readback; absence or ambiguity is not permission. The CLI cross-checks Actor ID,
cwd, and Workspace against `PASEO_AGENT_ID`, `paseo inspect`, and Git before it
constructs the GitHub adapter. Plan collaboration, `plan_mode=true`, or
`colorTier=planning` blocks all writes.

The request exists only so a feature/PR Workspace can return one
`forward_request` or `create_root_agent` action. Do not put the context in a
GitHub record, log it, or retain its temporary file after executing the action.

An explicit repository `integration_branch` always wins. Without one, `dev` is
accepted only when the remote `dev` ref exists and exactly one stable Workspace
candidate is on it. Otherwise the CLI returns `INTEGRATION_BRANCH_REQUIRED`; it
never guesses `main`.

Park/Resume success observations additionally require the exact durable
`agent_id`, `workspace_id`, and `branch`, plus fresh `agent_state`. Park accepts
only `idle`, `stopped`, `closed`, `finished`, or `completed`; resume accepts
only `running` or `busy`. A bare `status=succeeded` never releases or reacquires
execution/integration capacity and Conflict Claims.

Worker resolution is Issue Tier, Milestone default, repository default, global
default, then `standard`; runtime mapping is repository Tier, global Tier,
then the current Coordinator runtime. The four Worker tiers are `light`,
`standard`, `heavy`, and `frontier`.

Operational roles resolve independently through `role_profiles`: repository
override, then global profile. `coordinator_auto` is only for a future
automatic fallback; V6.1 continues to use the current manually created
Coordinator. `reviewer_standard` and `reviewer_strict` select Reviewer
runtimes without turning Reviewer strength into a Worker tier.
`reviewer_recovery` is reserved for targeted review recovery in V8.

Missing mode/features may inherit only from the same current provider. Every
concrete model, thinking option, mode, and feature is checked against fresh
Paseo capabilities before creation. Ambiguous or unsupported combinations
fail closed for that action.

Paseo 0.1.110 advertises `low/high/max` for Kimi K2.7 while its daemon accepts
`on` and rejects the tested `high/max` values. GWO therefore permits the exact
K2.7 `kimi-cli` value `on` as a narrow discovery compatibility exception. K3
continues to use its native `high/max` levels. This does not weaken capability
validation for another provider, model, or thinking value.

## V8 Paseo Prompt transport and readback

Worker and Review initial Prompts use the same bounded transport contract. An
initial Prompt of at most 8 KiB may be passed to `paseo run`; the limit is
deliberately below the Windows process command-line limit. A larger Prompt
creates the Agent with a short action-keyed bootstrap, waits for the Agent to
become idle, then uses `paseo send <agent> --prompt-file <temporary-path>
--no-wait --json`. Follow-up Worker and Review sends use the same temporary
UTF-8 Prompt-file path. The adapter closes the file before Paseo reads it and
removes it immediately after the command returns.

A successful create response or `status: sent` is transport acknowledgement,
not Prompt acceptance. The adapter reads the Agent activity and requires one
exact `[User] <frozen-prompt>` message boundary. Only then does it publish
`gwo.prompt_digest=<sha256>`, and it reads that label back through a Paseo
label-filtered listing. Every later adoption, including one after the digest
label exists, recounts the complete authoritative activity. More than one
exact activity boundary is `PROMPT_ACCEPTANCE_DUPLICATE` and remains ambiguous
rather than being treated as success.

`paseo inspect` may omit labels. Fresh Coordinator processes therefore rebuild
Worker and Review identity from authoritative `paseo ls --label ...` queries
using Admission or Review action identity, runtime profile, Candidate, parent,
and Prompt digest. Process-local label caches are not part of correctness.
After an acknowledgement loss, reconciliation queries the exact digest first:
an accepted Prompt is adopted without another send. Before the one large
Prompt-file send, the adapter requires authoritative bootstrap-idle readback
and publishes an action-keyed `gwo.prompt_delivery` receipt on the same Agent.
Once the send may have been invoked, acknowledgement, elapsed time, and later
idle observations cannot prove it was absent from Paseo's asynchronous queue.
Without an exact acceptance boundary, the same Agent and action therefore
remain ambiguous indefinitely; reconciliation never replays from timing or
idle heuristics. A retry is permitted only after an explicit pre-acceptance
transport rejection proves that no enqueue occurred. The Admission or Review
action never creates a replacement Agent while that identity exists.

Process-start failures distinguish `PASEO_COMMAND_LINE_OVERFLOW` from
`PASEO_EXECUTABLE_UNAVAILABLE`. The former identifies a host command-line
limit and must not be reported as a missing installation.

Review still receives the canonical Spec authority, but large declared file
contents are projected as `{path, content_digest, content_bytes}` references.
Each axis reads the exact Candidate workspace and diff for the materialized
content instead of receiving the same source payload again.

The installed-daemon Windows E2E is intentionally opt-in because it creates
one live Worker and two live Review Agents:

```text
python scripts/run_paseo_transport_e2e.py --live
```

Provider, model, thinking, mode, executable, and timeout are configurable
flags. `GWO_RUN_PASEO_TRANSPORT_E2E=1` is the automation equivalent of
`--live`. The runner uses a Worker Prompt above 300 KiB, two Review Prompts
above 170 KiB, replaces the Coordinator client twice, proves one exact Prompt
boundary per Agent, and archives every Agent it created. It does not run
hosted checks.

Legacy migration is an explicit configuration operation:

```text
python <skill>/scripts/orch_config.py migrate
```

The command reads `~/.orch/providers.json`, validates the complete translated
schema before publication, copies the source to `providers.v5.backup.json`,
and atomically installs `config.json`. It refuses to replace an existing config
or a conflicting backup, so unrelated global and repository overrides cannot
be normalized away by a Runtime operation. It ignores ambiguous V5 `roles`
bindings and initializes empty profile maps. Without this explicit command,
an absent `config.json` is resolved in memory and neither it nor the legacy
file is written. Once the no-clobber backup is published, migration retains it
even if config publication loses a race. Migration never unlinks the backup,
so a concurrent replacement cannot be removed by rollback.

The `frontier` tier never inherits the current Coordinator runtime. A missing
mapping fails as `RUNTIME_FRONTIER_PROFILE_MISSING`; an incomplete mapping
fails as `RUNTIME_FRONTIER_PROFILE_INVALID`.

Valid execution slots are 1–5. Integration WIP must be at least execution
capacity and at most 20. Candidate and Ready Reserve limits are at most 100;
valid attempts are 1–5; merge methods are `merge`, `squash`, or `rebase`.
Legacy `worker_slots` remains accepted as an execution-capacity alias when the
new keys are absent; its integration limit defaults to at least six and twice
execution capacity without rewriting the file. Invalid configuration blocks
new mutations but does not alter already-running Workers.

## V6.1 writer fence

V8 cutover uses one durable V6.1 stop fence at
`gwo-control:.gwo-v8/legacy-writer-fence.json`. Stop and restore append stable
action-keyed events through GitHub compare-and-swap; they do not erase writer
history. A missing branch or record means V6.1 has not been stopped. Invalid,
contradictory, or unavailable fence readback blocks mutation instead of
guessing.

Every state-changing V6.1 command checks the fence while holding the same
repository coordination mutex and before its first GitHub, Git, or Paseo
write. `frontier scan` and `reconcile --read-only` remain available for
diagnosis after stop.

Production cutover readback holds that mutex, reads non-terminal Dispatches
from GitHub, and reads unarchived repository Worker identities from Paseo.
Only the combination of the exact durable stop fence, no non-terminal
Dispatch, no in-flight Integration command, and no unarchived Worker can
report zero V6.1 execution authority. Installing this Skill never creates the
control branch, publishes the stop fence, or performs cutover.
