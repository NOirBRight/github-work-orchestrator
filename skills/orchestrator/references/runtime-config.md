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
        "thinkingOptionId": "high",
        "modeId": "yolo",
        "features": {}
      }
    },
    "standard": {
      "provider": "kimi-cli",
      "settings": {
        "model": "kimi-code/kimi-for-coding",
        "thinkingOptionId": "max",
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

If V6.1 finds `~/.orch/providers.json` but no new config, it copies the source to
`providers.v5.backup.json`, translates model and `thinking` to the new settings
shape, and atomically installs `config.json`. It ignores ambiguous V5
`roles` bindings, initializes an empty `role_profiles` map, and never reads the
old file at runtime afterwards.

Valid execution slots are 1–5. Integration WIP must be at least execution
capacity and at most 20. Candidate and Ready Reserve limits are at most 100;
valid attempts are 1–5; merge methods are `merge`, `squash`, or `rebase`.
Legacy `worker_slots` remains accepted as an execution-capacity alias when the
new keys are absent; its integration limit defaults to at least six and twice
execution capacity without rewriting the file. Invalid configuration blocks
new mutations but does not alter already-running Workers.
