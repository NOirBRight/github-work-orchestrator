# GWO V8 live canary

Status: GitHub-boundary smoke and automated three-node Integration Batch
acceptance passed on 2026-07-24.

## Dedicated repository

- Repository: `NOirBRight/gwo-v8-canary` (private)
- Integration branch: `main`
- Durable control branch: `gwo-control`
- Candidate namespace: `gwo/candidates/<candidate-sha>`
- Hosted workflow: `GWO Canary CI`

The fixture contains three independent Python modules. The hosted workflow
parks for 90 seconds before running the exact-SHA suite so CI parking and
readback are externally observable.

## First live smoke

The successful smoke used the production GitHub durable Plan control and
delivery control with the deterministic local Runtime Adapter:

- Activation:
  `activation:8e8972ea6a133384b0a425e8`
- Plan:
  `3ca0988b11d8002e6fb788fd6690c70f2e13e402c80cdc1804af053d0fdad02d`
- Candidate and integrated SHA:
  `a4083c9e09f36dcfa1c893a7cec71e2927a4ebc6`
- Hosted run:
  `https://github.com/NOirBRight/gwo-v8-canary/actions/runs/30058176111`
- Hosted result: passed
- Hosted retry count: zero

The exact Candidate SHA, local `main`, and remote `main` read back identically.
The Candidate was pushed once and its successful hosted run was not rerun.

## Findings fixed by the smoke

1. Candidate publication initially treated GitHub status eventual consistency
   as a permanent publication blocker. Publication readback now has bounded
   transport retries that do not repush, rerun CI, or consume an Attempt.
2. Durable GitHub reads initially allowed one transient network timeout to
   abort reconciliation. Read-only control-plane calls now have three bounded
   retries, and the canary runner keeps the Runtime alive across an exhausted
   read cycle.
3. The initial fixture relied on locally installed `pytest`. The workflow now
   installs its explicit test dependency; an isolated local venv verified the
   same setup before the successful Candidate was created.
4. A success-report field incorrectly assumed `ReconcileOutcome` exposed
   `integrated_sha`. Reporting now compares the Kernel Candidate to exact local
   and remote Git readback. The successful run was recovered from native Store
   state without another Candidate or CI run.

The intentionally retained failed hosted run is:
`https://github.com/NOirBRight/gwo-v8-canary/actions/runs/30057815190`.
It is valid failure evidence for a missing CI dependency and was not rerun.

## Full-canary Batch contract

The full canary admits three independent Work Nodes and completes their local
checks and dual-axis Reviews in parallel. The Kernel waits for that compatible
frontier to drain, composes the three same-base Candidates in deterministic
Node-key order, and records one immutable Integration Batch SHA.

Acceptance requires:

1. three distinct reviewed local Candidate SHAs and one shared base SHA;
2. one Batch SHA containing all three module changes;
3. one remote Candidate-namespace publication for the Batch SHA;
4. one `GWO Canary CI` run on that exact Batch SHA;
5. one remote and local `main` fast-forward to the Batch SHA;
6. Batch Evidence mapping all three Candidate SHAs and Integration Nodes to
   that result, with no `integration_refresh`, repeated Review, or per-member
   hosted CI.

## Full-canary result

The automated Paseo-backed run completed successfully:

- Run: `20260724035522`
- Elapsed time: 889.325 seconds
- Activation:
  `activation:ccdc7a60d2658fde8e123c4f`
- Plan:
  `4fab0ff6f5850534c96ed80898d98e2b8a793dd0964dc29d5ba135ba72bf44c1`
- Candidate SHAs:
  `578eb7512b48b786c13b0a31da6ffd0d9773d737`,
  `843d3b3108c9bdfc59f1fd69e0577b35569e47ec`, and
  `ae28302b193c5b6d5e86ac8db8f7fe256865ee76`
- Integration Batch:
  `9748c4792dbf39976c56d064dd4fd795491ba926ddfb3d4ccb3c7d30d4b3d516`
- Batch and integrated SHA:
  `8a8673cb2bba9d70003eb9c4305b89789122224b`
- Hosted run:
  `https://github.com/NOirBRight/gwo-v8-canary/actions/runs/30066004877`
- Hosted result: passed
- Hosted retry count: zero

All three Work Nodes were admitted concurrently, produced distinct local
Candidates, and received separate Standards and Spec observations. One first
Candidate passed its checks but Spec Review found an extra newline against the
exact content contract. The same semantic Attempt used its one bounded repair
round, produced `ae28302b...`, and passed a fresh dual-axis Review. This
validated that check success does not bypass Spec acceptance.

Only the final Batch SHA was published for CI. The three member Candidates did
not each start hosted CI, no `integration_refresh` occurred, and local and
remote `main` both read back exactly as the Batch SHA.

## Full-canary findings fixed

The automated Paseo run found several protocol and efficiency defects before
the Batch boundary:

1. Windows `.cmd` invocation exceeded the command-line trampoline limit for a
   frozen Prompt. The adapter now resolves the packaged Electron CLI entrypoint
   directly while preserving the public Paseo command surface.
2. Prompt identity was added after Agent creation and could race provider
   startup. `gwo.prompt_digest` is now part of the atomic create labels, and the
   CLI's actual `agentId` response field is accepted.
3. Worker and Review output instructions named markers without defining the
   exact JSON envelope. Both now include compact, action-bound success and
   no-result schemas; unparseable prose remains fail-closed.
4. The Worker could see the full machine output contract, so it repeated the
   repository suite and invoked `code-review` itself. Worker Prompt projection
   now exposes only implementation authority and affected diagnostics. Kernel
   and Runtime retain the full Check and Review contract.
5. A terminal Reviewer without typed output correctly permits one recovery,
   but the underspecified schema caused unnecessary recovery fan-out. The exact
   Review schema removes that false recovery; standard Review remains exactly
   two axes.
6. Store reconstruction previously assumed every integrated SHA equalled its
   member Candidate SHA. Reconstruction now restores Integration Batch
   identity, member mapping, hosted evidence, and the combined integrated SHA.
7. One reconcile repeatedly reread the same GitHub activation receipt.
   Reconciliation now pins one fail-closed durable witness for the pass and
   rereads authority at the next pass.

Paseo 0.1.110 currently advertises Kimi K2.7 `low/high/max`, while its daemon
rejects the tested `high` and `max` values and accepts `on`. The formal Kimi
Runtime Profiles now use `on`, matching the value exercised by this canary.
This changes only the provider setting: Worker tiers, K2.7/K3 model selection,
Yolo mode, and Codex Reviewer thinking levels remain unchanged.
