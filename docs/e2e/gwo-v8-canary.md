# GWO V8 live canary

Status: GitHub-boundary smoke passed; full three-node acceptance is pending an
Integration refresh decision.

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

## Full-canary blocker

The first full canary must admit three independent Work Nodes in parallel and
integrate them serially. Today all three Candidates start from the same target
head, while `GitHubCliDeliveryControl` only permits exact-SHA fast-forward
Integration. Therefore:

1. the first Candidate can integrate;
2. the other two deterministically enter `integration_refresh`;
3. each refreshed Candidate gets a new SHA and repeats Review and hosted CI;
4. the last Candidate can require another refresh after the second integrates.

Starting that run now would knowingly repeat Agent work, Review, and CI rather
than test an uncertain condition. Full acceptance remains closed until the
Integration policy chooses and specifies one of these options:

- retain exact-SHA fast-forward and authorize bounded automatic refresh;
- define compositional Integration for disjoint Effect Contracts, including
  which Evidence may be reused and which combined-tree check is required;
- serialize Candidate production, which gives up the required parallel
  Admission behavior and is therefore not recommended.
