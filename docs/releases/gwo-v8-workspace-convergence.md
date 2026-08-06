# GWO V8 Workspace Convergence Gate

This receipt proves local workspace convergence only. Local historical test logs
are forensic evidence, not Candidate/CI authority. No Git ref was deleted.
Passing this gate does not publish Beta1 or transfer writer authority.

```json
{
  "schema": "gwo-workspace-convergence.v1",
  "source_sha": "e58c596998df90e65349bdb4b5f25d3d9dc1f7e2",
  "protected_remote_ref": "refs/heads/codex/gwo-v8-ga-plan",
  "protected_remote_sha": "2cd6c46e1484ca140c3a197bbdeb171191d70c20",
  "kept_worktrees": [
    "canonical-main",
    "active-ga"
  ],
  "removed_worktree_count": 36,
  "removed_test_root_count": 48,
  "retained_green_runs": [
    "gwo-109-r14-full-run1",
    "gwo-109-r13-full-run3",
    "gwo-109-round7-full-final-race",
    "gwo-109-r12-full-synced"
  ],
  "refs_deleted": false,
  "archive_manifest_sha256": "e6939fbd27eedca2198b87f17de0d14bd3e367a65a37fc51542aa87ade889409",
  "pre_clean_bundle_sha256": "5eb64cffaed0ac2fd2748a575cb9cd041b2f7463d4d46d7dbfabf9dbdc0e8530",
  "post_clean_bundle_sha256": "9c91a126003e867a3c5736a4e4a69f5c3c079ce1adf5667c1108351181ac4f40",
  "evidence": {
    "manifest": {
      "path": "convergence-manifest.json",
      "sha256": "e6939fbd27eedca2198b87f17de0d14bd3e367a65a37fc51542aa87ade889409"
    },
    "pre_clean_bundle": {
      "path": "pre-clean.bundle",
      "sha256": "5eb64cffaed0ac2fd2748a575cb9cd041b2f7463d4d46d7dbfabf9dbdc0e8530"
    },
    "post_clean_bundle": {
      "path": "post-clean.bundle",
      "sha256": "9c91a126003e867a3c5736a4e4a69f5c3c079ce1adf5667c1108351181ac4f40"
    },
    "remote_ga_readback": {
      "path": "inventory/remote-ga-ref-after.txt",
      "ref": "refs/heads/codex/gwo-v8-ga-plan",
      "sha256": "2cd6c46e1484ca140c3a197bbdeb171191d70c20"
    }
  },
  "completed_at": "2026-08-04T20:54:09.2104764Z"
}
```
