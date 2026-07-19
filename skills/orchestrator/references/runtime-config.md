# Runtime configuration

`~/.orch/config.json` is optional, local, and re-read for each command. GitHub
stores only Difficulty Tier; concrete runtimes never become repository truth.

```json
{
  "schema_version": 1,
  "global": {
    "default_tier": "standard",
    "worker_slots": 3,
    "max_attempts": 2
  },
  "tiers": {
    "light": {
      "provider": "provider-id",
      "settings": {
        "model": "fast-model",
        "thinkingOptionId": "low",
        "modeId": "unattended-mode",
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
      "worker_slots": 3,
      "default_tier": "standard",
      "milestone_tiers": {"release-name": "heavy"},
      "tiers": {},
      "project_number": null
    }
  }
}
```

Resolution is Issue Tier, Milestone default, repository default, global
default, then `standard`; runtime mapping is repository Tier, global Tier,
then the current Coordinator runtime. Missing mode/features may inherit only
from the same current provider. Every concrete model, thinking option, mode,
and feature is checked against fresh Paseo capabilities before creation.
Ambiguous or unsupported combinations fail closed for that action.

The Coordinator always uses its current session runtime. There is deliberately
no `roles.coordinator` binding.

If V6 finds `~/.orch/providers.json` but no new config, it copies the source to
`providers.v5.backup.json`, translates model and `thinking` to the new settings
shape, and atomically installs `config.json`. It ignores V5 role bindings and
never reads the old file at runtime afterwards.

Valid Worker slots are 1–5; valid attempts are 1–5; merge methods are `merge`,
`squash`, or `rebase`. Invalid configuration blocks new mutations but does not
alter already-running Workers.
