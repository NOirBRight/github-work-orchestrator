# GWO V8 Root Canary Ticket Contract

Repository: `NOirBRight/github-work-orchestrator`

Approval: `CREATE-GWO-V8-GA-ROOT-CANARY-TICKETS`

Policy Witness: `.gwo-v8/policy-witness.json`

## alpha: GWO V8 GA Canary A: document Candidate receipt readback

- Path: `docs/canary/gwo-v8-ga-alpha.md`
- Assurance: `standard`
- Batch: `multi`

## Outcome
Create `docs/canary/gwo-v8-ga-alpha.md` as one GA root-Canary marker.

## Acceptance criteria
- [ ] Add the named document only.
- [ ] Repository validation passes.

## Scope exclusions
- Do not edit another path.
- Do not change dependencies, labels, authority, or release state.


## beta: GWO V8 GA Canary B: document permission binding readback

- Path: `docs/canary/gwo-v8-ga-beta.md`
- Assurance: `standard`
- Batch: `multi`

## Outcome
Create `docs/canary/gwo-v8-ga-beta.md` as one GA root-Canary marker.

## Acceptance criteria
- [ ] Add the named document only.
- [ ] Repository validation passes.

## Scope exclusions
- Do not edit another path.
- Do not change dependencies, labels, authority, or release state.


## gamma: GWO V8 GA Canary C: document restart reconstruction

- Path: `docs/canary/gwo-v8-ga-gamma.md`
- Assurance: `standard`
- Batch: `multi`

## Outcome
Create `docs/canary/gwo-v8-ga-gamma.md` as one GA root-Canary marker.

## Acceptance criteria
- [ ] Add the named document only.
- [ ] Repository validation passes.

## Scope exclusions
- Do not edit another path.
- Do not change dependencies, labels, authority, or release state.


## delta: GWO V8 GA Canary D: update the protected GA marker

- Path: `docs/canary/protected/gwo-v8-ga-delta.md`
- Assurance: `strict`
- Batch: `singleton`

## Outcome
Create `docs/canary/protected/gwo-v8-ga-delta.md` as one GA root-Canary marker.

## Acceptance criteria
- [ ] Add the protected marker only.
- [ ] Repository validation passes.

## Scope exclusions
- Do not edit another path.
- Do not change dependencies, labels, authority, or release state.


Readback command: `py -3.13 scripts/provision_v8_root_canary.py --repository NOirBRight/github-work-orchestrator --read-only --output tickets-readback.json`.
