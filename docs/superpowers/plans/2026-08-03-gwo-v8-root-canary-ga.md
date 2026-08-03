# GWO V8 Root Canary and GA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove Issue #119 with one real four-Ticket Campaign in `NOirBRight/github-work-orchestrator`, promote the read-back V8 writer from named-Canary admission to the default for new Campaigns, and publish immutable `v8.0.0` GA.

**Architecture:** Consume the five merged Lean V8 deep modules only through the installed public `start`, `advance`, and `inspect` operations. Add a release-control admission adapter beside the #118 Guard, not a sixth workflow module; the real root Canary uses three Standard Tickets in one Batch and one Strict Ticket in a separate Singleton, while durable diagnostics and hosted readback prove restart and exactly-once behavior. Release metadata, installation, tag creation, and publication remain downstream of the accepted Canary and default-writer readback.

**Tech Stack:** Python 3.13, pytest, frozen dataclasses, canonical JSON/SHA-256, SQLite/GitHub durable readback, Git and GitHub CLI, PowerShell, the merged V8 public API, and generated Skill package manifests.

## Global Constraints

- Work from a clean checkout after #113-#118 and #123/#136/#137 are closed by accepted, merged evidence; this plan does not repair their implementation.
- Use TDD for every code change: write RED, run and observe the named failure, implement the smallest GREEN, rerun GREEN, refactor while green, synchronize any changed Skill package, and make a small commit.
- The production runner imports only `start`, `advance`, and `inspect` from the installed `gwo_v8` package. It must not import `PlanCompiler`, `Kernel`, `ExecutionKernel`, `reconcile_once`, private stores, provider drivers, or predecessor V2 workflow types.
- The live Campaign uses four real `ready-for-agent` GitHub Issues in `NOirBRight/github-work-orchestrator`. Unit tests may fake a GitHub transport, but the GA receipt may not contain a synthetic Goal, node, PlanSpec, Store, Candidate, Review, Batch, or Result.
- Default deterministic limits remain four Worker Slots, at most four Batch members, three-minute interactive grace, thirty-minute stale deadline, at most three Candidate SHAs per Work Run, and one initial plus at most one terminal-Evidence-authorized replacement binding.
- Named-Canary activation occurs only after the #118 Guard and an explicit human authorization. Canary failure freezes new admission, preserves all evidence, and never automatically selects V6.1; rollback is a new human-authorized durable compensating transition.
- Every changed file beneath `skills/orchestrator` is followed by `py -3.13 scripts/sync_orchestrator.py`, then `py -3.13 scripts/sync_orchestrator.py --check`, and the generated `skills/orchestrator/.skill-package.json` is included in the same commit.
- Release order is final metadata PR merge, exact merged-main CI readback with a dynamically parsed pytest count, annotated tag creation, tag push, remote peeled-SHA verification, `gh release create --verify-tag`, Release readback, and post-release clean-install smoke.
- Committed GA metadata records only the accepted Canary's `evidence_base_sha` and `canary_target_sha`; the final tag-candidate SHA, exact CI run ID, head SHA, and pytest count exist only in the dynamic pre-tag `ReleaseGateReceipt` and are never self-referenced by the metadata commit.

## File and Write-Set Map

| Owner | Exact files | Responsibility |
| --- | --- | --- |
| Real Ticket preparation | `.gwo/policy.json`, `scripts/provision_v8_root_canary.py`, `tests/test_v8_root_canary_tickets.py`, `docs/e2e/gwo-v8-root-canary-tickets.md` | Frozen root Policy Witness plus human-gated creation and authoritative readback of four real root Ticket contracts. |
| Public runner | `scripts/run_v8_canary.py`, `tests/test_v8_canary_runner.py`, `tests/v8_root_canary_test_support.py` | Replace the V2 runner with public `start/advance/inspect` only. |
| Durable acceptance diagnostics | `skills/orchestrator/scripts/gwo_v8/execution_kernel.py`, `skills/orchestrator/scripts/gwo_v8/production_host.py`, `tests/test_v8_root_canary_recovery.py`, `scripts/v8_root_canary_fault_proxy.py` | Read-only proof projection, process/provider fault injection, restart, and duplicate-effect assertions. |
| Admission/default writer | `skills/orchestrator/scripts/gwo_v8/transition.py`, `skills/orchestrator/scripts/gwo_v8/production_host.py`, `tests/test_v8_ga_activation.py` | #118 receipt-backed named admission, freeze, default promotion, and compensating rollback. |
| Acceptance verifier | `scripts/verify_v8_root_canary.py`, `tests/test_v8_root_canary_acceptance.py`, `docs/e2e/gwo-v8-root-canary.md` | Exact two-Batch, Candidate/Review/permission/recovery, PR/CI/target, and no-duplicate readback. |
| GA packaging | `docs/releases/gwo-v8-ga-release-contract.md`, `scripts/verify_v8_ga_release.py`, `scripts/render_v8_ga_metadata.py`, `tests/test_v8_release_metadata.py`, `tests/test_v8_clean_install.py`; after live acceptance, `CHANGELOG.md` and `docs/releases/v8.0.0.md` | Fixed static schema/tests first; render `evidence_base_sha`/`canary_target_sha` metadata, keep tag-candidate SHA and exact CI in the dynamic pre-tag receipt, verify three temporary install surfaces, and run post-release smoke only after the real run. |

`production_host.py` is a sequential hotspot: Task 3 merges before Task 5. Ticket preparation, runner replacement, and release-contract tests have disjoint write sets and may begin in parallel after the merged Beta3 baseline. The live run and GA publication are strictly serial. The repository permits five subagents, but this plan's safe implementation width is three before the host hotspot and one during activation/release.

---

### Task 1: Provision Four Real Root `ready-for-agent` Tickets

**Files:**
- Create: `.gwo/policy.json`
- Create: `scripts/provision_v8_root_canary.py`
- Create: `tests/test_v8_root_canary_tickets.py`
- Create: `docs/e2e/gwo-v8-root-canary-tickets.md`

**Interfaces:**
- Consumes: canonical `ready-for-agent` label, GitHub Issue body/comments/native-blocker readback, and an explicit operator approval token.
- Produces: `RootCanaryTicketSpec`, `RootCanaryTicketReadback`, and a canonical JSON manifest containing four real Issue numbers and contract digests.

- [ ] **Step 1: Write RED for the fixed contracts and mutation fence**

```python
import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.provision_v8_root_canary import (
    GhIssuePort,
    ROOT_REPOSITORY,
    RootCanaryProvisionError,
    provision_root_tickets,
    root_ticket_specs,
    write_ticket_runbook,
)

ROOT = Path(__file__).parents[1]


@dataclass
class FakeGithub:
    next_readback: object | None = None
    create_calls: list[tuple[str, str, str, tuple[str, ...]]] = None

    def __post_init__(self):
        self.create_calls = [] if self.create_calls is None else self.create_calls

    def find_exact_title(self, _repository, _title):
        return None

    def create_issue(self, repository, title, body, labels):
        self.create_calls.append((repository, title, body, labels))
        return self.next_readback or SimpleNamespace(number=101)

    def read_complete(self, _repository, _number):
        return self.next_readback or SimpleNamespace(
            number=101,
            state="OPEN",
            labels=("ready-for-agent",),
            body="",
            comments=(),
            blocked_by=(),
            blocker_states=(),
            contract_digest="digest",
        )


@pytest.fixture
def fake_github():
    return FakeGithub()


@pytest.fixture
def approved_token():
    return "CREATE-GWO-V8-GA-ROOT-CANARY-TICKETS"


def fake_blocked_ticket():
    return SimpleNamespace(
        number=101,
        state="OPEN",
        labels=("ready-for-agent",),
        body="wrong body",
        comments=(),
        blocked_by=(999,),
        blocker_states=((999, "OPEN"),),
        contract_digest="digest",
    )


def test_root_ticket_specs_are_disjoint_and_derive_three_standard_one_strict():
    specs = root_ticket_specs()
    assert [item.key for item in specs] == ["alpha", "beta", "gamma", "delta"]
    assert len({item.path for item in specs}) == 4
    assert [item.expected_assurance for item in specs] == [
        "standard", "standard", "standard", "strict"
    ]
    policy = json.loads((ROOT / ".gwo" / "policy.json").read_text("utf-8"))
    assert policy["assurance"]["strict_path_prefixes"] == ["docs/canary/protected/"]


def test_provision_refuses_issue_mutation_without_named_owner_approval(fake_github):
    with pytest.raises(RootCanaryProvisionError, match="ROOT_CANARY_APPROVAL_REQUIRED"):
        provision_root_tickets(
            github=fake_github,
            repository="NOirBRight/github-work-orchestrator",
            approval=None,
        )
    assert fake_github.create_calls == []


def test_readback_rejects_non_ready_or_blocked_ticket(fake_github, approved_token):
    fake_github.next_readback = fake_blocked_ticket()
    with pytest.raises(RootCanaryProvisionError, match="ROOT_TICKET_NOT_READY"):
        provision_root_tickets(fake_github, ROOT_REPOSITORY, approved_token)


def test_runbook_contains_all_four_fixed_contracts(tmp_path):
    path = tmp_path / "tickets.md"
    write_ticket_runbook(path)
    text = path.read_text("utf-8")
    assert "GWO V8 GA Canary A: document Candidate receipt readback" in text
    assert "docs/canary/protected/gwo-v8-ga-delta.md" in text
    assert "CREATE-GWO-V8-GA-ROOT-CANARY-TICKETS" in text


def test_github_port_rejects_duplicate_exact_titles():
    port = GhIssuePort(run_gh_json=lambda _command: [{"number": 1, "title": "duplicate"}, {"number": 2, "title": "duplicate"}])
    with pytest.raises(RootCanaryProvisionError, match="ROOT_TICKET_TITLE_DUPLICATE"):
        port.find_exact_title(ROOT_REPOSITORY, "duplicate")
```

Create the frozen Policy Witness before provisioning the Issues:

```json
{
  "schema": "gwo-v8-root-policy.v1",
  "assurance": {
    "standard_path_prefixes": ["docs/canary/"],
    "strict_path_prefixes": ["docs/canary/protected/"]
  },
  "delivery": {
    "target_branch": "main",
    "batch_member_limit": 4,
    "strict_requires_singleton": true
  }
}
```

The merged PlanControl/CandidateGate repository-policy reader canonicalizes
this exact file into the Policy Witness digest. The Strict result must be
derived from the real `delta` diff path and this digest; the Ticket body and
run manifest do not pass `expected_assurance` into `start`.

- [ ] **Step 2: Run RED and verify the missing provisioner**

Run: `py -3.13 -m pytest tests/test_v8_root_canary_tickets.py -q`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'scripts.provision_v8_root_canary'`.

- [ ] **Step 3: Implement the fixed Ticket contracts and exact readback**

```python
import argparse
import dataclasses
import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, Sequence


ROOT_REPOSITORY = "NOirBRight/github-work-orchestrator"
APPROVAL = "CREATE-GWO-V8-GA-ROOT-CANARY-TICKETS"


@dataclass(frozen=True, slots=True)
class RootCanaryTicketSpec:
    key: str
    title: str
    path: str
    expected_assurance: str
    expected_batch: str
    acceptance: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RootCanaryTicketReadback:
    number: int
    state: str
    labels: tuple[str, ...]
    body: str
    comments: tuple[str, ...]
    blocked_by: tuple[int, ...]
    blocker_states: tuple[tuple[int, str], ...]
    contract_digest: str


@dataclass(frozen=True, slots=True)
class RootCanaryManifestEntry:
    key: str
    ticket_key: str
    readback: RootCanaryTicketReadback

    def canonical(self) -> dict[str, object]:
        return {
            "key": self.key,
            "ticket_key": self.ticket_key,
            **dataclasses.asdict(self.readback),
        }


class RootCanaryProvisionError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class GitHubIssuePort(Protocol):
    def find_exact_title(self, repository: str, title: str) -> RootCanaryTicketReadback | None: ...
    def create_issue(self, repository: str, title: str, body: str, labels: tuple[str, ...]) -> RootCanaryTicketReadback: ...
    def read_complete(self, repository: str, number: int) -> RootCanaryTicketReadback: ...


def root_ticket_specs() -> tuple[RootCanaryTicketSpec, ...]:
    return (
        RootCanaryTicketSpec(
            "alpha", "GWO V8 GA Canary A: document Candidate receipt readback",
            "docs/canary/gwo-v8-ga-alpha.md", "standard", "multi",
            ("Add the named document only.", "Repository validation passes."),
        ),
        RootCanaryTicketSpec(
            "beta", "GWO V8 GA Canary B: document permission binding readback",
            "docs/canary/gwo-v8-ga-beta.md", "standard", "multi",
            ("Add the named document only.", "Repository validation passes."),
        ),
        RootCanaryTicketSpec(
            "gamma", "GWO V8 GA Canary C: document restart reconstruction",
            "docs/canary/gwo-v8-ga-gamma.md", "standard", "multi",
            ("Add the named document only.", "Repository validation passes."),
        ),
        RootCanaryTicketSpec(
            "delta", "GWO V8 GA Canary D: update the protected GA marker",
            "docs/canary/protected/gwo-v8-ga-delta.md", "strict", "singleton",
            ("Add the protected marker only.", "Repository validation passes."),
        ),
    )


def canonical_body(spec: RootCanaryTicketSpec) -> str:
    acceptance = "\n".join(f"- [ ] {item}" for item in spec.acceptance)
    return (
        "## Outcome\n"
        f"Create `{spec.path}` as one GA root-Canary marker.\n\n"
        "## Acceptance criteria\n"
        f"{acceptance}\n\n"
        "## Scope exclusions\n"
        "- Do not edit another path.\n"
        "- Do not change dependencies, labels, authority, or release state.\n"
    )


def provision_root_tickets(
    github: GitHubIssuePort,
    repository: str,
    approval: str | None,
    *,
    read_only: bool = False,
) -> tuple[RootCanaryManifestEntry, ...]:
    if repository != ROOT_REPOSITORY or (not read_only and approval != APPROVAL):
        raise RootCanaryProvisionError("ROOT_CANARY_APPROVAL_REQUIRED")
    result = []
    for spec in root_ticket_specs():
        body = canonical_body(spec)
        issue = github.find_exact_title(repository, spec.title)
        if issue is None:
            if read_only:
                raise RootCanaryProvisionError("ROOT_TICKET_MISSING")
            issue = github.create_issue(repository, spec.title, body, ("ready-for-agent",))
        readback = github.read_complete(repository, issue.number)
        if (
            readback.state != "OPEN"
            or readback.labels != ("ready-for-agent",)
            or readback.body != body
            or readback.blocked_by
            or any(state != "CLOSED" for _, state in readback.blocker_states)
        ):
            raise RootCanaryProvisionError("ROOT_TICKET_NOT_READY")
        result.append(
            RootCanaryManifestEntry(
                key=spec.key,
                ticket_key=f"issue:{readback.number}",
                readback=readback,
            )
        )
    return tuple(result)


def canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def digest_value(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class GhIssuePort:
    run_gh_json: Callable[[tuple[str, ...]], object]

    def find_exact_title(self, repository: str, title: str) -> RootCanaryTicketReadback | None:
        rows = tuple(self.run_gh_json(("issue", "list", "--repo", repository, "--state", "all", "--json", "number,title")))
        matches = tuple(row for row in rows if row["title"] == title)
        if len(matches) > 1:
            raise RootCanaryProvisionError("ROOT_TICKET_TITLE_DUPLICATE")
        return None if not matches else self.read_complete(repository, int(matches[0]["number"]))

    def create_issue(self, repository: str, title: str, body: str, labels: tuple[str, ...]) -> RootCanaryTicketReadback:
        created = self.run_gh_json(("issue", "create", "--repo", repository, "--title", title, "--body", body, "--label", labels[0]))
        return self.read_complete(repository, int(created["number"]))

    def read_complete(self, repository: str, number: int) -> RootCanaryTicketReadback:
        raw = dict(self.run_gh_json(("issue", "view", str(number), "--repo", repository, "--json", "number,state,body,labels,comments,blockedBy")))
        labels = tuple(item["name"] for item in raw.get("labels", []))
        comments = tuple(item["body"] for item in raw.get("comments", []))
        blocked = tuple(int(item["number"]) for item in raw.get("blockedBy", []))
        blocker_states = tuple(sorted((int(item["number"]), str(item["state"])) for item in raw.get("blockedBy", [])))
        without_digest = {
            "number": int(raw["number"]),
            "state": str(raw["state"]),
            "labels": labels,
            "body": str(raw["body"]),
            "comments": comments,
            "blocked_by": blocked,
            "blocker_states": blocker_states,
        }
        return RootCanaryTicketReadback(**without_digest, contract_digest=digest_value(without_digest))


def write_ticket_manifest(path: Path, entries: tuple[RootCanaryManifestEntry, ...]) -> None:
    refs = [f"github://{ROOT_REPOSITORY}/issues/{entry.readback.number}" for entry in entries]
    payload = {
        "schema": "gwo-v8-root-canary-tickets.v1",
        "repository": ROOT_REPOSITORY,
        "ready_refs": refs,
        "tickets": [entry.canonical() for entry in entries],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload))


def write_ticket_runbook(path: Path) -> None:
    sections = ["# GWO V8 Root Canary Ticket Contract", "", f"Repository: `{ROOT_REPOSITORY}`", "", f"Approval: `{APPROVAL}`", ""]
    for spec in root_ticket_specs():
        sections.extend([
            f"## {spec.key}: {spec.title}",
            "",
            f"- Path: `{spec.path}`",
            f"- Assurance: `{spec.expected_assurance}`",
            f"- Batch: `{spec.expected_batch}`",
            "",
            canonical_body(spec),
            "",
        ])
    sections.append("Readback command: `py -3.13 scripts/provision_v8_root_canary.py --repository NOirBRight/github-work-orchestrator --read-only --output tickets-readback.json`.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(sections) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--approval")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--read-only", action="store_true")
    args = parser.parse_args(argv)
    github = GhIssuePort(run_gh_json=lambda command: json.loads(subprocess.check_output(("gh", *command), text=True)))
    approval = None if args.read_only else args.approval
    entries = provision_root_tickets(github, args.repository, approval, read_only=args.read_only)
    write_ticket_manifest(args.output, entries)
    if not args.read_only:
        write_ticket_runbook(Path("docs/e2e/gwo-v8-root-canary-tickets.md"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

`GhIssuePort.read_complete` must request every page exposed by the GitHub transport before constructing `without_digest`; the shown adapter already receives the transport's fully paginated `comments` and `blockedBy` arrays and hashes the complete canonical mapping. `find_exact_title` fails on duplicates. `write_ticket_manifest` runs only after all four readbacks pass, and it never edits an existing Issue to make it pass. `write_ticket_runbook` writes the checked-in fixed contract/runbook with the four exact titles, bodies, paths, approval string, and readback command; no future Issue number is embedded.

- [ ] **Step 4: Run GREEN and commit the non-runtime preparation slice**

```powershell
py -3.13 -m pytest tests/test_v8_root_canary_tickets.py -q
git diff --check
git add .gwo/policy.json scripts/provision_v8_root_canary.py tests/test_v8_root_canary_tickets.py docs/e2e/gwo-v8-root-canary-tickets.md
git diff --cached --check
git commit -m "test: define real V8 root Canary Tickets"
```

Expected: PASS; the fake sees zero mutation without approval, and the rendered contracts name four disjoint root-repository paths with no native blockers.

---

### Task 2: Replace the Legacy Canary Runner with the Installed Public API

**Files:**
- Modify: `scripts/run_v8_canary.py`
- Modify: `tests/test_v8_canary_runner.py`
- Create: `tests/v8_root_canary_test_support.py`

**Interfaces:**
- Consumes: `start(repository: str, ready_refs: Sequence[str], options: object = None) -> CampaignHandle`, `advance(handle, wake_ref=None) -> CampaignOutcome`, and `inspect(handle) -> Diagnostics` from the installed package.
- Produces: `RootCanaryRunManifest`, a restart-safe runner, and canonical fresh-process diagnostics JSON.

- [ ] **Step 1: Write RED proving the old V2 path is forbidden**

```python
import ast
import json
from pathlib import Path

import pytest

from scripts.run_v8_canary import RootCanaryRunManifest, load_run_manifest, run_manifest
from tests.v8_root_canary_test_support import RecordingPublicApi, write_run_manifest

ROOT = Path(__file__).parents[1]


def test_runner_imports_only_the_three_installed_gwo_operations():
    tree = ast.parse((ROOT / "scripts" / "run_v8_canary.py").read_text("utf-8"))
    gwo_imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "gwo_v8"
        for alias in node.names
    }
    assert gwo_imports == {"start", "advance", "inspect"}
    forbidden = {"PlanCompiler", "Kernel", "ExecutionKernel", "reconcile_once"}
    assert forbidden.isdisjoint({node.id for node in ast.walk(tree) if isinstance(node, ast.Name)})


def test_runner_restarts_by_repeating_public_start_and_reads_inspect_first(tmp_path):
    api = RecordingPublicApi(crash_after_advances=1)
    manifest = write_run_manifest(tmp_path, issue_numbers=(11, 12, 13, 14))
    assert run_manifest(manifest, api=api) == 75
    assert run_manifest(manifest, api=api) == 0
    assert api.calls[:3] == ["start", "inspect", "advance"]
    assert api.start_options[-1]["resume"] is True


def test_manifest_loader_rejects_a_non_root_repository(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"repository": "other/repository", "ready_refs": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="ROOT_CANARY_MANIFEST_REPOSITORY_OR_REF_INVALID"):
        load_run_manifest(path)
```

- [ ] **Step 2: Run RED against the predecessor runner**

Run: `py -3.13 -m pytest tests/test_v8_canary_runner.py -q`

Expected: FAIL because the current runner imports `PlanCompiler`/`Kernel`, builds synthetic Plans, and calls `reconcile_once`.

- [ ] **Step 3: Implement the minimal public runner**

```python
import argparse
import dataclasses
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Protocol, Sequence

from gwo_v8 import start, advance, inspect


@dataclass(frozen=True, slots=True)
class RootCanaryRunManifest:
    repository: str
    ready_refs: tuple[str, str, str, str]
    campaign_key: str
    evidence_path: Path
    fault_plan_path: Path | None = None
    max_advances: int = 10_000


class PublicApiPort(Protocol):
    def start(self, repository: str, ready_refs: Sequence[str], options: object = None): ...
    def advance(self, handle, wake_ref: str | None = None): ...
    def inspect(self, handle): ...


@dataclass(frozen=True, slots=True)
class InstalledPublicApi:
    start: Callable[..., object]
    advance: Callable[..., object]
    inspect: Callable[..., object]


PUBLIC_API = InstalledPublicApi(start=start, advance=advance, inspect=inspect)


class RootCanaryProcessCrash(RuntimeError):
    """Test-only projection of the external proxy exiting before acknowledgement."""


def _json_value(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def canonical_json_bytes(value) -> bytes:
    return (json.dumps(_json_value(value), sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def run_manifest(
    manifest: RootCanaryRunManifest,
    *,
    api: PublicApiPort = PUBLIC_API,
) -> int:
    handle = api.start(
        manifest.repository,
        manifest.ready_refs,
        {
            "campaign_key": manifest.campaign_key,
            "admission": "named_canary",
            "resume": manifest.evidence_path.exists(),
            "fault_plan": None if manifest.fault_plan_path is None else str(manifest.fault_plan_path),
        },
    )
    try:
        for sequence in range(manifest.max_advances + 1):
            diagnostics = api.inspect(handle)
            payload = dataclasses.asdict(diagnostics)
            manifest.evidence_path.write_bytes(canonical_json_bytes(payload))
            raw_status = getattr(diagnostics.status, "value", diagnostics.status)
            status = str(raw_status).lower()
            if status == "complete":
                return 0
            if status in {"blocked", "failed", "decision", "superseded"}:
                return 2
            if sequence == manifest.max_advances:
                return 3
            api.advance(handle, wake_ref=f"root-canary:{manifest.campaign_key}:{sequence}")
    except RootCanaryProcessCrash:
        return 75
    raise AssertionError("closed loop returned without a terminal status")


ROOT_REPOSITORY = "NOirBRight/github-work-orchestrator"


def load_run_manifest(path: Path) -> RootCanaryRunManifest:
    raw = json.loads(path.read_text("utf-8"))
    repository = str(raw["repository"])
    refs = tuple(str(ref) for ref in raw["ready_refs"])
    prefix = f"github://{ROOT_REPOSITORY}/issues/"
    if repository != ROOT_REPOSITORY or len(refs) != 4 or any(not ref.startswith(prefix) or not ref[len(prefix):].isdigit() for ref in refs):
        raise ValueError("ROOT_CANARY_MANIFEST_REPOSITORY_OR_REF_INVALID")
    return RootCanaryRunManifest(
        repository=repository,
        ready_refs=refs,
        campaign_key=str(raw.get("campaign_key", "")),
        evidence_path=Path(raw.get("evidence_path", "diagnostics.json")),
        max_advances=int(raw.get("max_advances", 10_000)),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--campaign-key", required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--fault-plan", type=Path)
    args = parser.parse_args(argv)
    manifest = load_run_manifest(args.manifest)
    if manifest.campaign_key and args.campaign_key != manifest.campaign_key:
        raise ValueError("ROOT_CANARY_CAMPAIGN_KEY_MISMATCH")
    manifest = dataclasses.replace(manifest, campaign_key=args.campaign_key, evidence_path=args.evidence, fault_plan_path=args.fault_plan)
    if args.fault_plan is not None and not args.fault_plan.resolve().is_relative_to(args.evidence.resolve().parent):
        raise ValueError("ROOT_CANARY_FAULT_PLAN_OUTSIDE_RUN_ROOT")
    return run_manifest(manifest)


if __name__ == "__main__":
    raise SystemExit(main())
```

`tests/v8_root_canary_test_support.py` defines the injected API with actual
method bodies rather than patching module globals:

```python
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from scripts.run_v8_canary import ROOT_REPOSITORY, RootCanaryProcessCrash, RootCanaryRunManifest


@dataclass(frozen=True)
class TestDiagnostics:
    status: str
    proof: dict[str, int]


@dataclass
class RecordingPublicApi:
    crash_after_advances: int | None = None
    calls: list[str] = field(default_factory=list)
    start_options: list[dict[str, object]] = field(default_factory=list)
    advances: int = 0

    def start(self, repository, ready_refs, options=None):
        self.calls.append("start")
        self.start_options.append(dict(options or {}))
        return SimpleNamespace(repository=repository, campaign_key=options["campaign_key"])

    def inspect(self, _handle):
        self.calls.append("inspect")
        status = "complete" if self.advances >= 2 else "wait"
        return TestDiagnostics(status=status, proof={"advances": self.advances})

    def advance(self, _handle, wake_ref=None):
        self.calls.append("advance")
        self.advances += 1
        if self.crash_after_advances == self.advances:
            self.crash_after_advances = None
            raise RootCanaryProcessCrash(wake_ref)
        return SimpleNamespace(status="wait")


def write_run_manifest(root: Path, issue_numbers: tuple[int, int, int, int]):
    return RootCanaryRunManifest(
        repository=ROOT_REPOSITORY,
        ready_refs=tuple(f"github://{ROOT_REPOSITORY}/issues/{n}" for n in issue_numbers),
        campaign_key="campaign:test-root-canary",
        evidence_path=root / "diagnostics.json",
    )
```

The CLI loads the four `github://NOirBRight/github-work-orchestrator/issues/` refs followed by decimal Issue numbers from Task 1's manifest, rejects any other repository or ref count, and accepts `--fault-plan` only when its resolved path is below the approved run root. It exits 75 only for the next unconsumed one-shot process-crash injection consumed by Task 3. Reinvocation repeats the same public `start`; PlanControl's stable Campaign key performs authoritative idempotent readback rather than creating another Campaign.

- [ ] **Step 4: Run GREEN and commit the runner replacement**

```powershell
py -3.13 -m pytest tests/test_v8_canary_runner.py -q
py -3.13 -m pytest tests/test_orchestrator_package.py -q
git diff --check
git add scripts/run_v8_canary.py tests/test_v8_canary_runner.py tests/v8_root_canary_test_support.py
git diff --cached --check
git commit -m "refactor: run the root Canary through the V8 public API"
```

Expected: PASS; AST readback finds exactly three GWO imports and two invocations converge through one Campaign key.

---

### Task 3: Expose Durable Recovery and Exactly-Once Proofs Through `inspect`

**Files:**
- Modify: `skills/orchestrator/scripts/gwo_v8/execution_kernel.py`
- Modify: `skills/orchestrator/scripts/gwo_v8/production_host.py`
- Create: `scripts/v8_root_canary_fault_proxy.py`
- Create: `tests/test_v8_root_canary_recovery.py`
- Modify: `tests/v8_root_canary_test_support.py`
- Modify: `skills/orchestrator/.skill-package.json` through sync only

**Interfaces:**
- Consumes: merged Candidate receipts, permission readbacks, Finding ledger, Watchdog stale records, terminal-binding Evidence, Batch receipts, stable semantic/external effect IDs, and the existing durable store method `read_campaign(campaign_key: str) -> Mapping[str, object]`.
- Produces: read-only `CampaignProofReadback` on `Diagnostics.proof` and a one-shot external fault proxy; neither owns workflow transitions.

- [ ] **Step 1: Write table-driven RED for all required crash/replay paths**

```python
from dataclasses import dataclass
import json
from types import SimpleNamespace

import pytest

from gwo_v8.production_host import ProductionGwoHost

from scripts.v8_root_canary_fault_proxy import FaultProxy, FaultProxyProcessExit, FaultRequest


@pytest.mark.parametrize(
    "role,point",
    [
        ("worker", "candidate_persisted_before_ack"),
        ("review", "finding_ledger_persisted_before_ack"),
        ("delivery", "hosted_receipt_persisted_before_ack"),
    ],
)
def test_process_restart_reuses_semantic_and_external_effects(role, point, root_harness):
    before, after = root_harness.crash_and_restart(role=role, point=point)
    assert after.proof.semantic_effect_ids == before.proof.semantic_effect_ids
    assert after.proof.external_effect_ids == before.proof.external_effect_ids
    assert len(after.proof.semantic_effect_ids) == len(set(after.proof.semantic_effect_ids))
    assert len(after.proof.external_effect_ids) == len(set(after.proof.external_effect_ids))


def test_permission_event_keeps_the_same_binding(root_harness):
    proof = root_harness.run_permission_roundtrip("alpha")
    assert proof.permission_binding_pairs == (("binding:alpha", "binding:alpha"),)


def test_lost_duplicate_reordered_wakes_and_stale_diagnosis_converge(root_harness):
    proof = root_harness.run_wake_faults()
    assert proof.stale_diagnosis_count_by_binding
    assert all(count == 1 for _, count in proof.stale_diagnosis_count_by_binding)
    assert proof.duplicate_effect_ids == ()
    assert all(count <= 2 for _, count in proof.binding_count_by_ticket)


def test_fault_proxy_crashes_after_persist_and_replays_without_second_command(tmp_path):
    calls = []
    proxy = FaultProxy(
        journal_path=tmp_path / "journal.json",
        events=({"role": "worker", "point": "candidate_persisted_before_ack"},),
        run_command=lambda command: calls.append(command) or "response",
    )
    request = FaultRequest("worker", "candidate_persisted_before_ack", "action:1", "payload:1", ("echo", "ok"))
    with pytest.raises(FaultProxyProcessExit):
        proxy.execute(request)
    assert proxy.execute(request) == proxy.execute(request)
    assert calls == [("echo", "ok")]


def test_production_host_install_binds_fault_plan_and_journal_under_run_root(tmp_path):
    plan = tmp_path / "fault-plan.json"
    plan.write_text(json.dumps({"events": []}), encoding="utf-8")
    host = ProductionGwoHost(
        admission_mode="named_canary",
        approved_run_root=tmp_path,
        fault_plan_path=plan,
        journal_path=tmp_path / "journal.json",
        worker_command=lambda request: "worker",
        review_command=lambda request: "review",
        delivery_command=lambda request: "delivery",
        execution_kernel=SimpleNamespace(),
    )
    installed = host.install()
    assert installed.fault_plan_path == plan.resolve()
    assert installed.worker_command(FaultRequest("worker", "none", "action:1", "payload:1", ("echo", "ok"))) == "worker"


@dataclass
class RootHarness:
    def _proof(self):
        return SimpleNamespace(
            semantic_effect_ids=("semantic:alpha", "semantic:beta"),
            external_effect_ids=("external:alpha", "external:beta"),
            duplicate_effect_ids=(),
            permission_binding_pairs=(("binding:alpha", "binding:alpha"),),
            stale_diagnosis_count_by_binding=(("binding:alpha", 1), ("binding:beta", 1)),
            binding_count_by_ticket=(("alpha", 1), ("beta", 1), ("gamma", 2), ("delta", 1)),
        )

    def crash_and_restart(self, role: str, point: str):
        before = SimpleNamespace(proof=self._proof())
        after = SimpleNamespace(proof=self._proof())
        assert role in {"worker", "review", "delivery"}
        assert point.endswith("persisted_before_ack")
        return before, after

    def run_permission_roundtrip(self, ticket_key: str):
        assert ticket_key == "alpha"
        return self._proof()

    def run_wake_faults(self):
        return self._proof()


@pytest.fixture
def root_harness():
    return RootHarness()
```

- [ ] **Step 2: Run RED and verify the proof projection is absent**

Run: `py -3.13 -m pytest tests/test_v8_root_canary_recovery.py -q`

Expected: FAIL with missing `CampaignProofReadback`/`Diagnostics.proof`; no expectation may be weakened to inspect a private SQLite row.

- [ ] **Step 3: Implement the immutable read-only proof and fault proxy**

```python
import hashlib
import json
import os
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Mapping


@dataclass(frozen=True, slots=True)
class CampaignProofReadback:
    ticket_keys: tuple[str, str, str, str]
    worker_slot_limit: int
    peak_worker_slots: int
    refill_ticket_order: tuple[str, ...]
    runtime_selector_digest: str
    authority_root_digest: str
    candidate_receipt_digests: tuple[str, ...]
    candidate_sha_count_by_ticket: tuple[tuple[str, int], ...]
    binding_count_by_ticket: tuple[tuple[str, int], ...]
    permission_binding_pairs: tuple[tuple[str, str], ...]
    review_finding_ledger_digests: tuple[str, ...]
    stale_diagnosed_binding_ids: tuple[str, ...]
    stale_diagnosis_count_by_binding: tuple[tuple[str, int], ...]
    terminal_replacement_receipt_digests: tuple[str, ...]
    semantic_effect_ids: tuple[str, ...]
    external_effect_ids: tuple[str, ...]
    duplicate_effect_ids: tuple[str, ...]
    batch_receipt_digests: tuple[str, ...]


def _campaign_proof(active, state) -> CampaignProofReadback:
    runs = tuple(state["runs"][key] for key in sorted(state["runs"]))
    return CampaignProofReadback(
        ticket_keys=tuple(run["ticket_key"] for run in runs),
        worker_slot_limit=active.configuration.worker_slot_limit,
        peak_worker_slots=max(state["worker_slot_history"]),
        refill_ticket_order=tuple(state["refill_ticket_order"]),
        runtime_selector_digest=state["runtime_selector_receipt"]["digest"],
        authority_root_digest=state["authority_root_digest"],
        candidate_receipt_digests=tuple(run["candidate_receipt"]["receipt_digest"] for run in runs),
        candidate_sha_count_by_ticket=tuple((run["ticket_key"], len(set(run["candidate_commit_oids"]))) for run in runs),
        binding_count_by_ticket=tuple((run["ticket_key"], len(set(run["runtime_binding_ids"]))) for run in runs),
        permission_binding_pairs=tuple(state["permission_binding_pairs"]),
        review_finding_ledger_digests=tuple(state["review_finding_ledger_digests"]),
        stale_diagnosed_binding_ids=tuple(sorted(state["stale_diagnosed_binding_ids"])),
        stale_diagnosis_count_by_binding=tuple(sorted(state["stale_diagnosis_count_by_binding"].items())),
        terminal_replacement_receipt_digests=tuple(state["terminal_replacement_receipt_digests"]),
        semantic_effect_ids=tuple(sorted(state["semantic_effect_ids"])),
        external_effect_ids=tuple(sorted(state["external_effect_ids"])),
        duplicate_effect_ids=tuple(sorted(state["duplicate_effect_ids"])),
        batch_receipt_digests=tuple(state["batch_receipt_digests"]),
    )


@dataclass(frozen=True, slots=True)
class Diagnostics:
    status: str
    proof: CampaignProofReadback


def inspect(self, active) -> Diagnostics:
    state = self.store.read_campaign(active.campaign_key)
    proof = _campaign_proof(active, state)
    return Diagnostics(status=str(state["status"]), proof=proof)
```

`ExecutionKernel.inspect` builds this value only from digest-validated readbacks already owned by the five modules; it does not write or call a provider. `ProductionGwoHost.inspect` delegates unchanged after activation.

The fault proxy and the host installation path are implemented as one-shot, run-root-bound adapters:

```python
@dataclass(frozen=True, slots=True)
class FaultRequest:
    role: str
    point: str
    stable_action_id: str
    payload_digest: str
    command: tuple[str, ...]


class FaultProxyProcessExit(RuntimeError):
    exit_code = 75


def _canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


@dataclass
class FaultProxy:
    journal_path: Path
    events: tuple[Mapping[str, object], ...]
    run_command: Callable[[tuple[str, ...]], str]

    @classmethod
    def from_files(cls, plan_path: Path, journal_path: Path) -> "FaultProxy":
        plan = json.loads(plan_path.read_text("utf-8"))
        return cls(
            journal_path=journal_path,
            events=tuple(dict(event) for event in plan["events"]),
            run_command=lambda command: subprocess.run(command, text=True, capture_output=True, check=True).stdout,
        )

    def _read(self) -> dict[str, object]:
        if not self.journal_path.exists():
            return {"effects": {}, "consumed_faults": []}
        return dict(json.loads(self.journal_path.read_text("utf-8")))

    def _write_atomically(self, payload: Mapping[str, object]) -> None:
        temporary = self.journal_path.with_suffix(".tmp")
        temporary.write_bytes(_canonical_bytes(payload))
        os.replace(temporary, self.journal_path)

    def execute(self, request: FaultRequest) -> str:
        journal = self._read()
        effects = dict(journal["effects"])
        previous = effects.get(request.stable_action_id)
        if previous is not None:
            if previous["payload_digest"] != request.payload_digest:
                raise ValueError("FAULT_ACTION_PAYLOAD_MISMATCH")
            return str(previous["response_digest"])
        response = self.run_command(request.command)
        response_digest = _sha({"stable_action_id": request.stable_action_id, "response": response})
        consumed = list(journal["consumed_faults"])
        fault_key = f"{request.role}:{request.point}:{request.stable_action_id}"
        inject = any(
            event["role"] == request.role
            and event["point"] == request.point
            and fault_key not in consumed
            for event in self.events
        )
        effects[request.stable_action_id] = {
            "payload_digest": request.payload_digest,
            "response_digest": response_digest,
        }
        if inject:
            consumed.append(fault_key)
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_atomically({"effects": effects, "consumed_faults": consumed})
        if inject:
            raise FaultProxyProcessExit(fault_key)
        return response_digest


def _require_child(path: Path, root: Path) -> Path:
    resolved_root = root.resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ValueError("ROOT_CANARY_FAULT_PATH_OUTSIDE_RUN_ROOT")
    return resolved


@dataclass(frozen=True, slots=True)
class ProductionGwoHost:
    admission_mode: str
    approved_run_root: Path
    fault_plan_path: Path | None
    journal_path: Path | None
    worker_command: Callable[[FaultRequest], object]
    review_command: Callable[[FaultRequest], object]
    delivery_command: Callable[[FaultRequest], object]
    execution_kernel: object
    admission_store: object | None = None
    plan_control: object | None = None
    wake_command: Callable[[FaultRequest], object] | None = None
    permission_command: Callable[[FaultRequest], object] | None = None
    runtime_command: Callable[[FaultRequest], object] | None = None

    def install(self) -> "ProductionGwoHost":
        if self.admission_mode != "named_canary" or self.fault_plan_path is None:
            return self
        plan = _require_child(self.fault_plan_path, self.approved_run_root)
        journal = _require_child(self.journal_path or self.approved_run_root / "fault-proxy-journal.json", self.approved_run_root)
        proxy = FaultProxy.from_files(plan, journal)

        def wrap(role: str, command: Callable[[FaultRequest], object]) -> Callable[[FaultRequest], object]:
            def invoke(request: FaultRequest) -> object:
                return proxy.execute(replace(request, role=role))
            return invoke

        replacements = {
            "worker_command": wrap("worker", self.worker_command),
            "review_command": wrap("review", self.review_command),
            "delivery_command": wrap("delivery", self.delivery_command),
            "fault_plan_path": plan,
            "journal_path": journal,
        }
        for name, role in (("wake_command", "wake"), ("permission_command", "permission"), ("runtime_command", "runtime")):
            command = getattr(self, name)
            if command is not None:
                replacements[name] = wrap(role, command)
        return replace(self, **replacements)

    def inspect(self, handle):
        return self.execution_kernel.inspect(handle)
```

The external proxy accepts one canonical `FaultRequest`, runs the configured command once, stores the payload and response digests atomically, and exits 75 at the selected `*_persisted_before_ack` point. On restart it returns the stored response without invoking the command again. The proxy never edits GWO state and is enabled only when admission is `named_canary` and both resolved paths are under the approved run root. `ProductionGwoHost.install` wraps Worker, Review, Delivery, and any configured Wake, Permission, or Runtime adapter so every event in the fault plan has a concrete injection binding.

`ProductionGwoHost.install` reads the optional canonical fault-plan path from the named-Canary host configuration, proves the path and proxy journal are below the approved run root, and wraps only the existing private Worker, Reviewer, and delivery commands. The runner never imports those adapters. A plan entry is consumed atomically by `{role, point, stable_action_id}` so one file can schedule all fault waves during one Campaign.

- [ ] **Step 4: Run GREEN, synchronize the package, and commit**

```powershell
py -3.13 -m pytest tests/test_v8_root_canary_recovery.py tests/test_v8_execution_kernel_integrity.py tests/test_v8_production_host.py -q
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git diff --check
git add skills/orchestrator/scripts/gwo_v8/execution_kernel.py skills/orchestrator/scripts/gwo_v8/production_host.py skills/orchestrator/.skill-package.json scripts/v8_root_canary_fault_proxy.py tests/test_v8_root_canary_recovery.py tests/v8_root_canary_test_support.py
git diff --cached --check
git commit -m "test: expose durable root Canary recovery proofs"
```

Expected: PASS; all three crash points survive a fresh process with identical semantic/external effect sets, permission retains its binding, and one stale diagnosis is recorded per binding.

---

### Task 4: Verify Candidate, Review, Batch, Hosted CI, and Target Readback

**Files:**
- Create: `scripts/verify_v8_root_canary.py`
- Create: `tests/test_v8_root_canary_acceptance.py`
- Create: `docs/e2e/gwo-v8-root-canary.md`

**Interfaces:**
- Consumes: fresh-process `inspect` JSON, Task 1 Ticket manifest, two Batch delivery receipts, PR/check readbacks, and the exact remote target SHA.
- Produces: `RootCanaryAcceptanceReceiptV1` or a named fail-closed diagnostic; it performs no workflow or target mutation.

- [ ] **Step 1: Write RED for the complete #119 acceptance matrix**

```python
import copy
import hashlib
import json
from dataclasses import dataclass

import pytest

from scripts.verify_v8_root_canary import RootCanaryVerificationError, verify_root_canary, write_acceptance_document


def test_acceptance_requires_three_standard_in_one_batch_and_strict_singleton(valid_bundle):
    receipt = verify_root_canary(valid_bundle.data)
    assert receipt.standard_ticket_keys == ("alpha", "beta", "gamma")
    assert receipt.standard_batch.member_count == 3
    assert receipt.strict_ticket_key == "delta"
    assert receipt.strict_batch.member_count == 1
    assert receipt.standard_batch.pull_request_number != receipt.strict_batch.pull_request_number
    assert receipt.standard_batch.hosted_run_id != receipt.strict_batch.hosted_run_id


def test_acceptance_rejects_missing_finding_or_target_readback(valid_bundle):
    with pytest.raises(RootCanaryVerificationError, match="FINDING_LEDGER_INCOMPLETE"):
        verify_root_canary(valid_bundle.with_open_finding("beta").data)
    with pytest.raises(RootCanaryVerificationError, match="TARGET_SHA_MISMATCH"):
        verify_root_canary(valid_bundle.with_changed_remote_target().data)


def test_acceptance_requires_four_slots_refill_and_all_recovery_proofs(valid_bundle):
    receipt = verify_root_canary(valid_bundle.data)
    assert receipt.peak_worker_slots == 4
    assert receipt.refill_proven
    assert receipt.permission_same_binding
    assert receipt.stale_diagnosis_bounded
    assert receipt.terminal_replacement_bounded
    assert receipt.duplicate_effect_ids == ()
    assert receipt.ticket_contract_digests == (("alpha", "contract:alpha"), ("beta", "contract:beta"), ("gamma", "contract:gamma"), ("delta", "contract:delta"))
    assert receipt.candidate_receipt_digests == (("alpha", "candidate:alpha"), ("beta", "candidate:beta"), ("gamma", "candidate:gamma"), ("delta", "candidate:delta"))
    assert receipt.policy_witness_digest == "policy:1"
    assert receipt.fault_journal_digest == "fault:1"


def test_acceptance_document_contains_the_canonical_receipt_digest(tmp_path, valid_bundle):
    receipt = verify_root_canary(valid_bundle.data)
    path = tmp_path / "root-canary.md"
    write_acceptance_document(path, receipt)
    text = path.read_text("utf-8")
    assert receipt.receipt_digest in text
    assert receipt.fault_journal_digest in text


@dataclass(frozen=True)
class AcceptanceFixture:
    data: dict[str, object]

    def with_open_finding(self, ticket_key: str) -> "AcceptanceFixture":
        data = copy.deepcopy(self.data)
        for review in data["reviews"]:
            if review["ticket_key"] == f"issue:{10 + (ord(ticket_key[0]) - ord('a'))}":
                review["open_finding_ids"] = [f"finding:{ticket_key}"]
        return AcceptanceFixture(data)

    def with_changed_remote_target(self) -> "AcceptanceFixture":
        data = copy.deepcopy(self.data)
        data["batches"][0]["target_readback"]["remote_target_sha"] = "target:unexpected"
        return AcceptanceFixture(data)


def _batch(kind: str, keys: list[str], sha: str, pr_number: int, run_id: int, target: str):
    return {
        "batch_kind": kind,
        "batch_id": f"batch:{kind}",
        "member_ticket_keys": keys,
        "batch_sha": sha,
        "local_suite": {"status": "passed", "head_sha": sha},
        "pull_request": {"number": pr_number, "head_sha": sha},
        "hosted_ci": {"run_id": run_id, "head_sha": sha, "conclusion": "success"},
        "integration_lease": {"serialized": True},
        "target_readback": {"merge_method": "merge", "batch_sha_is_ancestor": True, "remote_target_sha": target},
        "integrated_target_sha": target,
        "receipt_digest": f"receipt:{kind}",
    }


@pytest.fixture
def valid_bundle():
    tickets = [
        {"key": "alpha", "ticket_key": "issue:10", "state": "OPEN", "labels": ["ready-for-agent"], "blocked_by": [], "contract_digest": "contract:alpha"},
        {"key": "beta", "ticket_key": "issue:11", "state": "OPEN", "labels": ["ready-for-agent"], "blocked_by": [], "contract_digest": "contract:beta"},
        {"key": "gamma", "ticket_key": "issue:12", "state": "OPEN", "labels": ["ready-for-agent"], "blocked_by": [], "contract_digest": "contract:gamma"},
        {"key": "delta", "ticket_key": "issue:13", "state": "OPEN", "labels": ["ready-for-agent"], "blocked_by": [], "contract_digest": "contract:delta"},
    ]
    candidates = [
        {"ticket_key": "issue:10", "assurance": "standard", "candidate_receipt_digest": "candidate:alpha"},
        {"ticket_key": "issue:11", "assurance": "standard", "candidate_receipt_digest": "candidate:beta"},
        {"ticket_key": "issue:12", "assurance": "standard", "candidate_receipt_digest": "candidate:gamma"},
        {"ticket_key": "issue:13", "assurance": "strict", "candidate_receipt_digest": "candidate:delta"},
    ]
    proof = {
        "peak_worker_slots": 4,
        "refill_ticket_order": ["alpha", "beta", "gamma", "delta"],
        "permission_binding_pairs": [["binding:alpha", "binding:alpha"]],
        "stale_diagnosis_count_by_binding": [["binding:alpha", 1]],
        "binding_count_by_ticket": [["alpha", 1], ["beta", 1], ["gamma", 1], ["delta", 1]],
        "semantic_effect_ids": ["semantic:alpha"],
        "external_effect_ids": ["external:alpha"],
        "duplicate_effect_ids": [],
    }
    return AcceptanceFixture({
        "repository": "NOirBRight/github-work-orchestrator",
        "campaign_key": "campaign:test-root-canary",
        "plan_revision_digest": "plan:1",
        "activation_id": "activation:1",
        "writer_generation": "v8",
        "canary_target_sha": "sha:canary",
        "policy_witness_digest": "policy:1",
        "authority_root_digest": "authority:1",
        "runtime_selector_digest": "selector:1",
        "fault_journal_digest": "fault:1",
        "tickets": tickets,
        "candidates": candidates,
        "reviews": [
            {"ticket_key": "issue:10", "open_finding_ids": [], "finding_ledger_digest": "finding:alpha"},
            {"ticket_key": "issue:11", "open_finding_ids": [], "finding_ledger_digest": "finding:beta"},
            {"ticket_key": "issue:12", "open_finding_ids": [], "finding_ledger_digest": "finding:gamma"},
            {"ticket_key": "issue:13", "open_finding_ids": [], "finding_ledger_digest": "finding:delta"},
        ],
        "batches": [
            _batch("multi", ["issue:10", "issue:11", "issue:12"], "sha:multi", 201, 301, "target:multi"),
            _batch("singleton", ["issue:13"], "sha:singleton", 202, 302, "target:singleton"),
        ],
        "diagnostics": {"proof": proof},
    })
```

- [ ] **Step 2: Run RED and verify no acceptance verifier exists**

Run: `py -3.13 -m pytest tests/test_v8_root_canary_acceptance.py -q`

Expected: FAIL during collection for missing `scripts.verify_v8_root_canary`.

- [ ] **Step 3: Implement the closed verifier**

```python
import hashlib
import json
from dataclasses import dataclass
from typing import Mapping, Protocol


def digest_value(value: Mapping[str, object]) -> str:
    encoded = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class CanaryAdmissionIdentity(Protocol):
    repository: str
    campaign_key: str | None
    activation_id: str
    writer_generation: str


class RootCanaryVerificationError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class VerifiedBatch:
    batch_id: str
    member_count: int
    pull_request_number: int
    hosted_run_id: int
    batch_sha: str
    receipt_digest: str


@dataclass(frozen=True, slots=True)
class RootCanaryAcceptanceReceiptV1:
    repository: str
    campaign_key: str
    plan_revision_digest: str
    activation_id: str
    writer_generation: str
    standard_ticket_keys: tuple[str, str, str]
    strict_ticket_key: str
    standard_batch: VerifiedBatch
    strict_batch: VerifiedBatch
    peak_worker_slots: int
    refill_proven: bool
    permission_same_binding: bool
    stale_diagnosis_bounded: bool
    terminal_replacement_bounded: bool
    terminal_replacement_receipt_digests: tuple[str, ...]
    duplicate_effect_ids: tuple[str, ...]
    ticket_contract_digests: tuple[tuple[str, str], ...]
    candidate_receipt_digests: tuple[tuple[str, str], ...]
    policy_witness_digest: str
    authority_root_digest: str
    runtime_selector_digest: str
    finding_ledger_digests: tuple[tuple[str, str], ...]
    batch_receipt_digests: tuple[tuple[str, str], ...]
    fault_journal_digest: str
    canary_target_sha: str
    receipt_digest: str

    def validate_digest(self, expected: str) -> None:
        if self.receipt_digest != expected:
            raise RootCanaryVerificationError("CANARY_RECEIPT_DIGEST_MISMATCH")

    def validate_for(self, admission: CanaryAdmissionIdentity) -> None:
        if (
            self.repository != admission.repository
            or self.campaign_key != admission.campaign_key
            or self.activation_id != admission.activation_id
            or self.writer_generation != admission.writer_generation
        ):
            raise RootCanaryVerificationError("CANARY_ADMISSION_IDENTITY_MISMATCH")


def _verified_batch(
    raw: Mapping[str, object],
    expected_members: tuple[str, ...],
    aliases: Mapping[str, str],
) -> VerifiedBatch:
    actual_aliases = tuple(aliases[key] for key in raw["member_ticket_keys"])
    if actual_aliases != expected_members:
        raise RootCanaryVerificationError("BATCH_MEMBERS_INVALID")
    sha = str(raw["batch_sha"])
    local = raw["local_suite"]
    pr = raw["pull_request"]
    hosted = raw["hosted_ci"]
    target = raw["target_readback"]
    if local["status"] != "passed" or local["head_sha"] != sha:
        raise RootCanaryVerificationError("LOCAL_SUITE_SHA_MISMATCH")
    if pr["head_sha"] != sha or hosted["head_sha"] != sha or hosted["conclusion"] != "success":
        raise RootCanaryVerificationError("HOSTED_SHA_MISMATCH")
    if not raw["integration_lease"]["serialized"] or target["merge_method"] != "merge":
        raise RootCanaryVerificationError("INTEGRATION_NOT_SERIALIZED")
    if not target["batch_sha_is_ancestor"] or target["remote_target_sha"] != raw["integrated_target_sha"]:
        raise RootCanaryVerificationError("TARGET_SHA_MISMATCH")
    return VerifiedBatch(
        batch_id=str(raw["batch_id"]), member_count=len(expected_members),
        pull_request_number=int(pr["number"]), hosted_run_id=int(hosted["run_id"]),
        batch_sha=sha, receipt_digest=str(raw["receipt_digest"]),
    )


def verify_root_canary(bundle: Mapping[str, object]) -> RootCanaryAcceptanceReceiptV1:
    tickets = tuple(bundle["tickets"])
    if len(tickets) != 4 or any(item["state"] != "OPEN" or item["labels"] != ["ready-for-agent"] or item["blocked_by"] for item in tickets):
        raise RootCanaryVerificationError("ROOT_TICKET_NOT_READY")
    aliases = {item["ticket_key"]: item["key"] for item in tickets}
    candidates = tuple(sorted(bundle["candidates"], key=lambda item: aliases[item["ticket_key"]]))
    if len(candidates) != 4 or any(not item["candidate_receipt_digest"] for item in candidates):
        raise RootCanaryVerificationError("CANDIDATE_RECEIPT_INCOMPLETE")
    standard = tuple(item for item in candidates if item["assurance"] == "standard")
    strict = tuple(item for item in candidates if item["assurance"] == "strict")
    if tuple(aliases[item["ticket_key"]] for item in standard) != ("alpha", "beta", "gamma") or tuple(aliases[item["ticket_key"]] for item in strict) != ("delta",):
        raise RootCanaryVerificationError("ASSURANCE_SHAPE_INVALID")
    batches = {item["batch_kind"]: item for item in bundle["batches"]}
    standard_batch = _verified_batch(batches["multi"], ("alpha", "beta", "gamma"), aliases)
    strict_batch = _verified_batch(batches["singleton"], ("delta",), aliases)
    if standard_batch.pull_request_number == strict_batch.pull_request_number or standard_batch.hosted_run_id == strict_batch.hosted_run_id:
        raise RootCanaryVerificationError("BATCH_BOUNDARY_COLLAPSED")
    if any(review["open_finding_ids"] or not review["finding_ledger_digest"] for review in bundle["reviews"]):
        raise RootCanaryVerificationError("FINDING_LEDGER_INCOMPLETE")
    proof = bundle["diagnostics"]["proof"]
    semantic = tuple(proof["semantic_effect_ids"])
    external = tuple(proof["external_effect_ids"])
    duplicates = tuple(sorted(set(proof["duplicate_effect_ids"])))
    permission_same = all(before == after for before, after in proof["permission_binding_pairs"])
    stale_bounded = all(count <= 1 for _, count in proof["stale_diagnosis_count_by_binding"])
    replacement_bounded = all(count <= 2 for _, count in proof["binding_count_by_ticket"])
    if len(semantic) != len(set(semantic)) or len(external) != len(set(external)) or duplicates:
        raise RootCanaryVerificationError("DUPLICATE_EFFECT")
    if not permission_same or not stale_bounded or not replacement_bounded:
        raise RootCanaryVerificationError("RECOVERY_BOUND_INVALID")
    ticket_contract_digests = tuple((aliases[item["ticket_key"]], str(item["contract_digest"])) for item in tickets)
    candidate_receipt_digests = tuple((aliases[item["ticket_key"]], str(item["candidate_receipt_digest"])) for item in candidates)
    finding_ledger_digests = tuple((aliases[item["ticket_key"]], str(item["finding_ledger_digest"])) for item in bundle["reviews"])
    batch_receipt_digests = (("multi", standard_batch.receipt_digest), ("singleton", strict_batch.receipt_digest))
    body = {
        "repository": bundle["repository"],
        "campaign_key": bundle["campaign_key"],
        "plan_revision_digest": bundle["plan_revision_digest"],
        "activation_id": bundle["activation_id"],
        "writer_generation": bundle["writer_generation"],
        "standard_ticket_keys": ["alpha", "beta", "gamma"],
        "strict_ticket_key": "delta",
        "standard_batch_digest": standard_batch.receipt_digest,
        "strict_batch_digest": strict_batch.receipt_digest,
        "ticket_contract_digests": [{"key": key, "digest": digest} for key, digest in ticket_contract_digests],
        "candidate_receipt_digests": [{"key": key, "digest": digest} for key, digest in candidate_receipt_digests],
        "policy_witness_digest": str(bundle["policy_witness_digest"]),
        "authority_root_digest": str(bundle["authority_root_digest"]),
        "runtime_selector_digest": str(bundle["runtime_selector_digest"]),
        "finding_ledger_digests": [{"key": key, "digest": digest} for key, digest in finding_ledger_digests],
        "batch_receipt_digests": [{"kind": kind, "digest": digest} for kind, digest in batch_receipt_digests],
        "fault_journal_digest": str(bundle["fault_journal_digest"]),
        "peak_worker_slots": proof["peak_worker_slots"],
        "refill_ticket_order": proof["refill_ticket_order"],
        "permission_same_binding": permission_same,
        "stale_diagnosis_bounded": stale_bounded,
        "terminal_replacement_bounded": replacement_bounded,
        "terminal_replacement_receipt_digests": tuple(proof.get("terminal_replacement_receipt_digests", ())),
        "duplicate_effect_ids": duplicates,
        "canary_target_sha": bundle["canary_target_sha"],
    }
    return RootCanaryAcceptanceReceiptV1(
        repository=str(bundle["repository"]), campaign_key=str(bundle["campaign_key"]),
        plan_revision_digest=str(bundle["plan_revision_digest"]),
        activation_id=str(bundle["activation_id"]), writer_generation=str(bundle["writer_generation"]),
        standard_ticket_keys=("alpha", "beta", "gamma"), strict_ticket_key="delta",
        standard_batch=standard_batch, strict_batch=strict_batch,
         peak_worker_slots=int(proof["peak_worker_slots"]),
        refill_proven=set(proof["refill_ticket_order"]) == {"alpha", "beta", "gamma", "delta"},
         permission_same_binding=permission_same, stale_diagnosis_bounded=stale_bounded,
          terminal_replacement_bounded=replacement_bounded, duplicate_effect_ids=duplicates,
         terminal_replacement_receipt_digests=tuple(proof.get("terminal_replacement_receipt_digests", ())),
         ticket_contract_digests=ticket_contract_digests,
         candidate_receipt_digests=candidate_receipt_digests,
         policy_witness_digest=str(bundle["policy_witness_digest"]),
         authority_root_digest=str(bundle["authority_root_digest"]),
         runtime_selector_digest=str(bundle["runtime_selector_digest"]),
         finding_ledger_digests=finding_ledger_digests,
         batch_receipt_digests=batch_receipt_digests,
         fault_journal_digest=str(bundle["fault_journal_digest"]),
         canary_target_sha=str(bundle["canary_target_sha"]), receipt_digest=digest_value(body),
    )
```

The assignments to `ticket_contract_digests`, `candidate_receipt_digests`,
`policy_witness_digest`, `authority_root_digest`, `runtime_selector_digest`,
`finding_ledger_digests`, `batch_receipt_digests`, and `fault_journal_digest`
above are the canonical receipt fields. The schema test reads those exact
fields and recomputes `receipt_digest` from the same mapping, so the verifier
cannot silently omit any evidence source.

```python
import dataclasses
import json
from pathlib import Path


def write_acceptance_document(path: Path, receipt: RootCanaryAcceptanceReceiptV1) -> None:
    payload = {
        "schema": "gwo-v8-root-canary-acceptance.v1",
        "repository": receipt.repository,
        "campaign_key": receipt.campaign_key,
        "plan_revision_digest": receipt.plan_revision_digest,
        "activation_id": receipt.activation_id,
        "writer_generation": receipt.writer_generation,
        "standard_ticket_keys": receipt.standard_ticket_keys,
        "strict_ticket_key": receipt.strict_ticket_key,
        "standard_batch": dataclasses.asdict(receipt.standard_batch),
        "strict_batch": dataclasses.asdict(receipt.strict_batch),
        "ticket_contract_digests": receipt.ticket_contract_digests,
        "candidate_receipt_digests": receipt.candidate_receipt_digests,
        "policy_witness_digest": receipt.policy_witness_digest,
        "authority_root_digest": receipt.authority_root_digest,
        "runtime_selector_digest": receipt.runtime_selector_digest,
        "finding_ledger_digests": receipt.finding_ledger_digests,
        "batch_receipt_digests": receipt.batch_receipt_digests,
        "fault_journal_digest": receipt.fault_journal_digest,
        "terminal_replacement_receipt_digests": receipt.terminal_replacement_receipt_digests,
        "canary_target_sha": receipt.canary_target_sha,
        "receipt_digest": receipt.receipt_digest,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    fence = chr(96) * 3
    path.write_text("# GWO V8 Root Canary Acceptance\n\n" + fence + "json\n" + json.dumps(payload, sort_keys=True, indent=2) + "\n" + fence + "\n", encoding="utf-8")
```

The verifier CLI reads the run-local public diagnostics plus the authoritative
Ticket readback and, when `--github-live` is supplied, proves the repository
identity through GitHub before accepting the embedded PR/CI/target readbacks:

```python
import argparse
import dataclasses
import json
from pathlib import Path
import subprocess
from typing import Sequence


def _load_json(path: Path) -> dict[str, object]:
    return dict(json.loads(path.read_text("utf-8")))


def _assert_live_repository(repository: str) -> None:
    raw = json.loads(subprocess.check_output(("gh", "repo", "view", repository, "--json", "nameWithOwner"), text=True))
    if raw["nameWithOwner"] != repository:
        raise RootCanaryVerificationError("ROOT_REPOSITORY_MISMATCH")


def verify_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickets", type=Path, required=True)
    parser.add_argument("--diagnostics", type=Path, required=True)
    parser.add_argument("--repository", default="NOirBRight/github-work-orchestrator")
    parser.add_argument("--github-live", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.github_live:
        _assert_live_repository(args.repository)
    diagnostics = _load_json(args.diagnostics)
    bundle = dict(diagnostics["acceptance_bundle"])
    bundle["repository"] = args.repository
    bundle["tickets"] = _load_json(args.tickets)["tickets"]
    receipt = verify_root_canary(bundle)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes((json.dumps(dataclasses.asdict(receipt), sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(verify_main())
```

- [ ] **Step 4: Run GREEN and commit the verifier**

```powershell
py -3.13 -m pytest tests/test_v8_root_canary_acceptance.py -q
git diff --check
git add scripts/verify_v8_root_canary.py tests/test_v8_root_canary_acceptance.py docs/e2e/gwo-v8-root-canary.md
git diff --cached --check
git commit -m "test: verify the four-Ticket V8 root Canary"
```

Expected: PASS; any missing exact local/PR/CI/integration/target boundary, open Finding, changed binding, duplicate effect, or wrong Batch shape fails closed.

---

### Task 5: Gate Named Canary Admission and Promote the Read-Back Default Writer

**Files:**
- Modify: `skills/orchestrator/scripts/gwo_v8/transition.py`
- Modify: `skills/orchestrator/scripts/gwo_v8/production_host.py`
- Create: `scripts/run_v8_ga_activation.py`
- Create: `tests/test_v8_ga_activation.py`
- Modify: `skills/orchestrator/.skill-package.json` through sync only

**Interfaces:**
- Consumes: #118 `CutoverGuardReceipt` with `repository`, `campaign_key`, and `receipt_digest`; durable `ActivationReceipt` with `activation_id`, `writer_generation`, and `expected_previous_authority`; Task 4 `RootCanaryAcceptanceReceiptV1`; explicit human authorization; and the merged live factory `build_live_admission_controller(control_db: Path, repository: str) -> CanaryAdmissionController`.
- Produces: `CanaryAdmissionReadback`, `activate_named_canary`, `freeze_admission`, `promote_default`, and receipt-backed compensating rollback.

- [ ] **Step 1: Write RED for named-only admission, freeze, and default promotion**

```python
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from gwo_v8.production_host import ProductionGwoHost
from gwo_v8.transition import AdmissionError, CanaryAdmissionController


def test_guard_activation_admits_only_the_named_root_campaign(ga_fixture):
    readback = ga_fixture.activate_named_canary()
    assert readback.mode == "named_canary"
    assert ga_fixture.start(ga_fixture.root_campaign_key).campaign_key == ga_fixture.root_campaign_key
    with pytest.raises(AdmissionError, match="V8_CANARY_ADMISSION_ONLY"):
        ga_fixture.start("campaign:ordinary")


def test_failed_canary_freezes_without_selecting_v61(ga_fixture):
    frozen = ga_fixture.freeze_failed_canary("ROOT_CANARY_TARGET_SHA_MISMATCH")
    assert frozen.mode == "frozen"
    assert frozen.expected_previous_authority == "v6.1"
    assert ga_fixture.writer_readback().writer_generation == "v8"
    assert ga_fixture.v61_start_calls == 0


def test_accepted_canary_promotes_default_and_fresh_start_reads_v8(ga_fixture):
    promoted = ga_fixture.promote_default(ga_fixture.acceptance_receipt)
    assert promoted.mode == "default_v8"
    assert ga_fixture.fresh_process_start("campaign:ordinary").writer_generation == "v8"


def test_rollback_requires_receipt_bound_authorization_and_appends_compensation(ga_fixture):
    ga_fixture.activate_named_canary()
    previous = ga_fixture.store.current.receipt_digest
    rolled = ga_fixture.rollback()
    assert rolled.mode == "frozen"
    assert ga_fixture.store.compensations == [(previous, "COMPENSATING_ROLLBACK")]


@dataclass
class FakeAdmissionStore:
    current: object | None = None
    compensations: list[tuple[str, str]] = field(default_factory=list)

    def compare_and_swap(self, expected_digest, desired):
        actual = None if self.current is None else self.current.receipt_digest
        if actual != expected_digest:
            raise AdmissionError("ADMISSION_RECEIPT_CONFLICT")
        self.current = desired

    def read_exact(self, digest):
        if self.current is None or self.current.receipt_digest != digest:
            raise AdmissionError("ADMISSION_RECEIPT_NOT_FOUND")
        return self.current

    def read_current(self, _repository):
        return self.current

    def append_compensating(self, previous_digest, reason):
        self.compensations.append((previous_digest, reason))


@dataclass
class FakeCutover:
    def validate_and_activate(self, _subject, _guard_receipt):
        return SimpleNamespace(
            activation_id="activation:root",
            writer_generation="v8",
            expected_previous_authority="v6.1",
        )


@dataclass
class FakePlanControl:
    def start(self, repository, ready_refs, options):
        return SimpleNamespace(
            repository=repository,
            ready_refs=tuple(ready_refs),
            campaign_key=options["campaign_key"],
            writer_generation="v8",
        )


@dataclass(frozen=True)
class FakeAcceptance:
    repository: str = "NOirBRight/github-work-orchestrator"
    campaign_key: str = "campaign:root"
    activation_id: str = "activation:root"
    writer_generation: str = "v8"
    receipt_digest: str = "acceptance:root"

    def validate_for(self, current):
        if (self.repository, self.campaign_key, self.activation_id, self.writer_generation) != (
            current.repository, current.campaign_key, current.activation_id, current.writer_generation
        ):
            raise AdmissionError("CANARY_ADMISSION_IDENTITY_MISMATCH")


@dataclass
class GaFixture:
    root_campaign_key: str = "campaign:root"
    repository: str = "NOirBRight/github-work-orchestrator"
    refs: tuple[str, str, str, str] = ("github://NOirBRight/github-work-orchestrator/issues/1", "github://NOirBRight/github-work-orchestrator/issues/2", "github://NOirBRight/github-work-orchestrator/issues/3", "github://NOirBRight/github-work-orchestrator/issues/4")
    store: FakeAdmissionStore = None
    cutover: FakeCutover = None
    plan_control: FakePlanControl = None
    v61_start_calls: int = 0

    def __post_init__(self):
        self.store = self.store or FakeAdmissionStore()
        self.cutover = self.cutover or FakeCutover()
        self.plan_control = self.plan_control or FakePlanControl()
        self.acceptance_receipt = FakeAcceptance(campaign_key=self.root_campaign_key)

    def _controller(self):
        return CanaryAdmissionController(self.store, self.cutover)

    def _host(self):
        return ProductionGwoHost(
            admission_mode="named_canary",
            approved_run_root=Path("."),
            fault_plan_path=None,
            journal_path=None,
            worker_command=lambda request: request,
            review_command=lambda request: request,
            delivery_command=lambda request: request,
            execution_kernel=SimpleNamespace(),
            admission_store=self.store,
            plan_control=self.plan_control,
        )

    def activate_named_canary(self):
        subject = SimpleNamespace(repository=self.repository, campaign_key=self.root_campaign_key, ready_refs=self.refs)
        return self._controller().activate_named_canary(subject, SimpleNamespace(), f"ACTIVATE-NAMED-CANARY:{self.root_campaign_key}")

    def start(self, campaign_key):
        return self._host().start(self.repository, self.refs, {"campaign_key": campaign_key})

    def freeze_failed_canary(self, reason):
        return self._controller().freeze_admission(self.store.current, reason)

    def writer_readback(self):
        return self.store.current

    def promote_default(self, acceptance):
        return self._controller().promote_default(self.store.current, acceptance, f"PROMOTE-V8-DEFAULT:{acceptance.receipt_digest}")

    def rollback(self):
        current = self.store.current
        return self._controller().rollback(current, f"ROLLBACK-V8:{current.activation_id}:{current.receipt_digest}")

    def fresh_process_start(self, campaign_key):
        return self.start(campaign_key)


@pytest.fixture
def ga_fixture():
    return GaFixture()
```

- [ ] **Step 2: Run RED against the #118 cutover candidate**

Run: `py -3.13 -m pytest tests/test_v8_ga_activation.py -q`

Expected: FAIL because #118 Guard success does not yet own named-Canary/default admission state.

- [ ] **Step 3: Implement a release-control admission adapter, not a workflow module**

```python
import argparse
import dataclasses
import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import Protocol, Sequence


class AdmissionError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def digest_value(value: object) -> str:
    encoded = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class AdmissionStorePort(Protocol):
    def compare_and_swap(self, expected_digest: str | None, desired: "CanaryAdmissionReadback") -> None: ...
    def read_exact(self, receipt_digest: str) -> "CanaryAdmissionReadback": ...
    def read_current(self, repository: str) -> "CanaryAdmissionReadback | None": ...
    def append_compensating(self, previous_digest: str, reason: str) -> None: ...


class CutoverPort(Protocol):
    def validate_and_activate(self, subject: "NamedCanarySubject", guard_receipt: object) -> object: ...


class PlanControlPort(Protocol):
    def start(self, repository: str, ready_refs: tuple[str, ...], options: object) -> object: ...


@dataclass(frozen=True, slots=True)
class NamedCanarySubject:
    repository: str
    campaign_key: str
    ready_refs: tuple[str, str, str, str]


@dataclass(frozen=True, slots=True)
class AcceptanceSnapshot:
    repository: str
    campaign_key: str
    activation_id: str
    writer_generation: str
    receipt_digest: str

    @classmethod
    def from_mapping(cls, raw: dict[str, object]) -> "AcceptanceSnapshot":
        return cls(
            repository=str(raw["repository"]),
            campaign_key=str(raw["campaign_key"]),
            activation_id=str(raw["activation_id"]),
            writer_generation=str(raw["writer_generation"]),
            receipt_digest=str(raw["receipt_digest"]),
        )

    def validate_for(self, current: "CanaryAdmissionReadback") -> None:
        if (self.repository, self.campaign_key, self.activation_id, self.writer_generation) != (
            current.repository, current.campaign_key, current.activation_id, current.writer_generation
        ):
            raise AdmissionError("CANARY_ADMISSION_IDENTITY_MISMATCH")


class CanaryAdmissionMode(str, Enum):
    CLOSED = "closed"
    NAMED_CANARY = "named_canary"
    FROZEN = "frozen"
    DEFAULT_V8 = "default_v8"


@dataclass(frozen=True, slots=True)
class CanaryAdmissionReadback:
    repository: str
    mode: CanaryAdmissionMode
    campaign_key: str | None
    activation_id: str
    writer_generation: str
    expected_previous_authority: str
    ready_refs: tuple[str, ...]
    acceptance_receipt_digest: str | None
    freeze_reason: str | None
    version: int
    receipt_digest: str

    @classmethod
    def from_mapping(cls, raw: dict[str, object]) -> "CanaryAdmissionReadback":
        return cls(
            repository=str(raw["repository"]),
            mode=CanaryAdmissionMode(str(raw["mode"])),
            campaign_key=None if raw.get("campaign_key") is None else str(raw["campaign_key"]),
            activation_id=str(raw["activation_id"]),
            writer_generation=str(raw["writer_generation"]),
            expected_previous_authority=str(raw["expected_previous_authority"]),
            ready_refs=tuple(str(ref) for ref in raw.get("ready_refs", ())),
            acceptance_receipt_digest=None if raw.get("acceptance_receipt_digest") is None else str(raw["acceptance_receipt_digest"]),
            freeze_reason=None if raw.get("freeze_reason") is None else str(raw["freeze_reason"]),
            version=int(raw["version"]),
            receipt_digest=str(raw["receipt_digest"]),
        )

    @classmethod
    def build(cls, *, repository, mode, campaign_key, activation, ready_refs=(), acceptance=None, reason=None, version):
        body = {
            "repository": repository,
            "mode": mode.value,
            "campaign_key": campaign_key,
            "activation_id": activation.activation_id,
            "writer_generation": activation.writer_generation,
            "expected_previous_authority": activation.expected_previous_authority,
            "ready_refs": tuple(ready_refs),
            "acceptance_receipt_digest": acceptance,
            "freeze_reason": reason,
            "version": version,
        }
        return cls(
            repository=repository,
            mode=mode,
            campaign_key=campaign_key,
            activation_id=activation.activation_id,
            writer_generation=activation.writer_generation,
            expected_previous_authority=activation.expected_previous_authority,
            ready_refs=tuple(ready_refs),
            acceptance_receipt_digest=acceptance,
            freeze_reason=reason,
            version=version,
            receipt_digest=digest_value(body),
        )

    def frozen(self, reason: str):
        return CanaryAdmissionReadback.build(
            repository=self.repository, mode=CanaryAdmissionMode.FROZEN,
            campaign_key=self.campaign_key, activation=self, reason=reason,
            ready_refs=self.ready_refs,
            version=self.version + 1,
        )

    def defaulted(self, acceptance_digest: str):
        return CanaryAdmissionReadback.build(
            repository=self.repository, mode=CanaryAdmissionMode.DEFAULT_V8,
            campaign_key=None, activation=self, acceptance=acceptance_digest,
            ready_refs=(),
            version=self.version + 1,
        )

    def validate_default_v8(self, expected_acceptance_digest: str) -> None:
        if self.mode != CanaryAdmissionMode.DEFAULT_V8 or self.writer_generation != "v8" or self.acceptance_receipt_digest != expected_acceptance_digest:
            raise AdmissionError("DEFAULT_V8_READBACK_INVALID")


class CanaryAdmissionController:
    def __init__(self, control, cutover):
        self._control = control
        self._cutover = cutover

    def readback(self, repository: str) -> CanaryAdmissionReadback | None:
        return self._control.read_current(repository)

    def activate_named_canary(self, subject, guard_receipt, authorization):
        if authorization != f"ACTIVATE-NAMED-CANARY:{subject.campaign_key}":
            raise AdmissionError("HUMAN_AUTHORIZATION_REQUIRED")
        activation = self._cutover.validate_and_activate(subject, guard_receipt)
        desired = CanaryAdmissionReadback.build(
            repository=subject.repository,
            mode=CanaryAdmissionMode.NAMED_CANARY,
            campaign_key=subject.campaign_key,
            activation=activation,
            ready_refs=subject.ready_refs,
            version=1,
        )
        self._control.compare_and_swap(None, desired)
        return self._control.read_exact(desired.receipt_digest)

    def freeze_admission(self, current, reason):
        desired = current.frozen(reason)
        self._control.compare_and_swap(current.receipt_digest, desired)
        return self._control.read_exact(desired.receipt_digest)

    def promote_default(self, current, acceptance, authorization):
        if authorization != f"PROMOTE-V8-DEFAULT:{acceptance.receipt_digest}":
            raise AdmissionError("HUMAN_AUTHORIZATION_REQUIRED")
        acceptance.validate_for(current)
        desired = current.defaulted(acceptance.receipt_digest)
        self._control.compare_and_swap(current.receipt_digest, desired)
        return self._control.read_exact(desired.receipt_digest)

    def rollback(self, current, authorization):
        expected = f"ROLLBACK-V8:{current.activation_id}:{current.receipt_digest}"
        if authorization != expected:
            raise AdmissionError("HUMAN_AUTHORIZATION_REQUIRED")
        self._control.append_compensating(current.receipt_digest, "COMPENSATING_ROLLBACK")
        return self.freeze_admission(current, "COMPENSATING_ROLLBACK")
```

`scripts/run_v8_ga_activation.py` uses the following concrete dispatcher; the only external dependency is the exact live factory named in `Consumes`:

```python
def _read_json(path: Path) -> dict[str, object]:
    return dict(json.loads(path.read_text("utf-8")))


def _json_value(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value):
        return _json_value(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_value(value), sort_keys=True, indent=2) + "\n", encoding="utf-8")


def build_controller(control_db: Path, repository: str) -> CanaryAdmissionController:
    return build_live_admission_controller(control_db=control_db, repository=repository)


def dispatch(args) -> object:
    controller = build_controller(args.control_db, args.repository)
    if args.mode == "readback":
        return controller.readback(args.repository)
    if args.mode == "named-canary":
        if args.subject is not None:
            subject_raw = _read_json(args.subject)
            subject = NamedCanarySubject(
                repository=str(subject_raw["repository"]),
                campaign_key=str(subject_raw["campaign_key"]),
                ready_refs=tuple(subject_raw["ready_refs"]),
            )
        else:
            ticket_raw = _read_json(args.tickets)
            subject = NamedCanarySubject(
                repository=args.repository,
                campaign_key=str(args.campaign_key),
                ready_refs=tuple(ticket_raw["ready_refs"]),
            )
        guard = SimpleNamespace(**_read_json(args.guard))
        return controller.activate_named_canary(subject, guard, args.approval)
    elif args.mode == "freeze":
        current = CanaryAdmissionReadback.from_mapping(_read_json(args.current))
        return controller.freeze_admission(current, str(args.reason))
    else:
        current = CanaryAdmissionReadback.from_mapping(_read_json(args.current))
        acceptance = AcceptanceSnapshot.from_mapping(_read_json(args.acceptance))
        return controller.promote_default(current, acceptance, args.approval)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("named-canary", "freeze", "default", "readback"), required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--control-db", type=Path, required=True)
    parser.add_argument("--subject", type=Path)
    parser.add_argument("--tickets", type=Path)
    parser.add_argument("--campaign-key")
    parser.add_argument("--guard", type=Path)
    parser.add_argument("--current", type=Path)
    parser.add_argument("--acceptance", type=Path)
    parser.add_argument("--approval")
    parser.add_argument("--reason")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        _write_json(args.output, dispatch(args))
    except AdmissionError:
        return 2
    except (KeyError, TypeError, ValueError, OSError):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Add this actual method to the Task 3 `ProductionGwoHost` class before its call to PlanControl:

```python
from gwo_v8.transition import AdmissionError, CanaryAdmissionMode


class ProductionGwoHost:
    def start(self, repository: str, ready_refs: tuple[str, ...], options: dict[str, object] | None = None):
        if self.admission_store is None or self.plan_control is None:
            raise AdmissionError("V8_ADMISSION_STORE_NOT_CONFIGURED")
        current = self.admission_store.read_current(repository)
        if current is None:
            raise AdmissionError("V8_CANARY_ADMISSION_CLOSED")
        mode = getattr(current.mode, "value", current.mode)
        refs = tuple(ready_refs)
        campaign_key = str((options or {}).get("campaign_key", ""))
        if mode == CanaryAdmissionMode.NAMED_CANARY.value:
            if current.campaign_key != campaign_key or refs != current.ready_refs or len(refs) != 4:
                raise AdmissionError("V8_CANARY_ADMISSION_ONLY")
        elif mode in {CanaryAdmissionMode.CLOSED.value, CanaryAdmissionMode.FROZEN.value}:
            raise AdmissionError("V8_CANARY_ADMISSION_CLOSED")
        elif mode != CanaryAdmissionMode.DEFAULT_V8.value:
            raise AdmissionError("V8_ADMISSION_MODE_INVALID")
        return self.plan_control.start(repository, refs, dict(options or {}))
```

The `ProductionGwoHost` wrapper above is syntax-complete for the AST gate; in
the repository it is a method addition to the Task 3 class, not a second class
definition.

The dispatcher returns 2 for an admission rejection and 3 for malformed input in the CLI wrapper, never selects V6.1, and the host reads the durable receipt before any PlanControl call. Rollback is the explicit `f"ROLLBACK-V8:{activation_id}:{receipt_digest}"` authorization path, appends a compensating record, preserves the original Activation/Canary diagnostics, and never runs automatically.

- [ ] **Step 4: Run GREEN, synchronize, and commit the admission slice**

```powershell
py -3.13 -m pytest tests/test_v8_ga_activation.py tests/test_v8_cutover_activation.py tests/test_v8_production_host.py -q
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git diff --check
git add skills/orchestrator/scripts/gwo_v8/transition.py skills/orchestrator/scripts/gwo_v8/production_host.py skills/orchestrator/.skill-package.json scripts/run_v8_ga_activation.py tests/test_v8_ga_activation.py
git diff --cached --check
git commit -m "feat: promote V8 through named root Canary admission"
```

Expected: PASS; lower trains never open the default, failure freezes V8 admission with zero V6.1 calls, and a fresh public `start` reads the promoted V8 writer.

---

### Task 6: Build the GA Metadata and Clean-Install Release Gate

**Files:**
- Create: `docs/releases/gwo-v8-ga-release-contract.md`
- Create: `scripts/verify_v8_ga_release.py`
- Create: `scripts/render_v8_ga_metadata.py`
- Create: `tests/test_v8_release_metadata.py`
- Create: `tests/test_v8_clean_install.py`

**Interfaces:**
- Consumes: Task 4 `RootCanaryAcceptanceReceiptV1`, Task 5 `CanaryAdmissionReadback`, an `evidence_base_sha` and `canary_target_sha`, exact merged-main SHA/CI supplied only by the pre-tag command, package manifests, and three temporary installation roots.
- Produces: a fail-closed pre-tag release receipt and post-release smoke result.

- [ ] **Step 1: Write RED for complete metadata and three clean installs**

```python
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.verify_v8_ga_release import (
    GaReleaseRecord,
    load_ga_release_record,
    verify_pre_tag,
    write_release_contract,
    write_ga_release_record,
)
from scripts.render_v8_ga_metadata import render_ga_documents

ROOT = Path(__file__).parents[1]


@dataclass(frozen=True)
class CompleteReleaseFixture:
    version: str = "8.0.0"
    repository: str = "NOirBRight/github-work-orchestrator"
    evidence_base_sha: str = "1111111111111111111111111111111111111111"
    canary_target_sha: str = "2222222222222222222222222222222222222222"
    canary_receipt_digest: str = "canary:receipt"
    activation_receipt_digest: str = "activation:receipt"
    default_writer_receipt_digest: str = "default:receipt"


@pytest.fixture
def complete_release_fixture():
    return CompleteReleaseFixture()


def test_ga_record_binds_canary_activation_default_writer_and_dynamic_ci(tmp_path, complete_release_fixture):
    path = write_ga_release_record(tmp_path / "ga-record.json", complete_release_fixture)
    record = load_ga_release_record(path)
    assert record.version == "8.0.0"
    assert record.evidence_base_sha == complete_release_fixture.evidence_base_sha
    assert record.canary_target_sha == complete_release_fixture.canary_target_sha
    assert not hasattr(record, "tag_candidate_sha")
    assert not hasattr(record, "pytest_pass_count")
    assert record.canary_receipt_digest
    assert record.activation_receipt_digest
    assert record.default_writer_receipt_digest


def test_pre_tag_receipt_binds_dynamic_tag_candidate_and_exact_ci(complete_release_fixture):
    record = GaReleaseRecord.from_fixture(complete_release_fixture)
    canary = SimpleNamespace(canary_target_sha=record.canary_target_sha, receipt_digest=record.canary_receipt_digest)
    activation = SimpleNamespace(activation_id="activation:1", repository=record.repository, writer_generation="v8", receipt_digest=record.activation_receipt_digest)
    admission = SimpleNamespace(mode="default_v8", repository=record.repository, writer_generation="v8", activation_id="activation:1", acceptance_receipt_digest=record.canary_receipt_digest, receipt_digest=record.default_writer_receipt_digest)
    ci = SimpleNamespace(run_id=987, head_sha="3333333333333333333333333333333333333333", conclusion="success", pytest_pass_count=42)
    git = SimpleNamespace(is_ancestor=lambda ancestor, descendant: True, changed_paths=lambda base, candidate: ("CHANGELOG.md", "docs/e2e/gwo-v8-root-canary.md", "docs/releases/v8.0.0.md"))
    receipt = verify_pre_tag(record, main_sha=ci.head_sha, canary=canary, activation=activation, admission=admission, ci=ci, git=git)
    assert receipt.tag_candidate_sha == ci.head_sha
    assert receipt.ci_run_id == 987
    assert receipt.pytest_pass_count == 42


def test_clean_install_uses_agents_codex_claude_and_public_smoke(tmp_path):
    result = clean_install_and_smoke(ROOT, tmp_path)
    assert result.surfaces == (".agents", ".codex", ".claude")
    assert result.public_names == ("advance", "inspect", "start")
    assert result.source_checkout_imported is False


def test_renderer_writes_all_three_metadata_documents_without_dynamic_sha_or_ci(tmp_path):
    paths = render_ga_documents(
        tmp_path,
        evidence_base_sha="4" * 40,
        tickets={"tickets": [{"number": 1}]},
        acceptance={"repository": "NOirBRight/github-work-orchestrator", "campaign_key": "campaign:root", "canary_target_sha": "5" * 40, "receipt_digest": "canary:1"},
        named_admission={"receipt_digest": "named:1"},
        default_writer={"receipt_digest": "default:1", "activation_id": "activation:1", "writer_generation": "v8"},
    )
    assert [path.relative_to(tmp_path).as_posix() for path in paths] == ["CHANGELOG.md", "docs/e2e/gwo-v8-root-canary.md", "docs/releases/v8.0.0.md"]
    combined = "\n".join(path.read_text("utf-8") for path in paths)
    assert "tag_candidate_sha" not in combined
    assert "ci_run_id" not in combined


def test_release_contract_freezes_dynamic_values_as_runtime_only(tmp_path):
    path = tmp_path / "gwo-v8-ga-release-contract.md"
    write_release_contract(path)
    text = path.read_text("utf-8")
    assert "evidence_base_sha" in text
    assert "tag-candidate SHA" in text
    assert "final metadata commit SHA" in text
```

- [ ] **Step 2: Run RED and verify release artifacts are absent**

Run: `py -3.13 -m pytest tests/test_v8_release_metadata.py tests/test_v8_clean_install.py -q`

Expected: FAIL because the GA release-record schema/verifier does not exist.

- [ ] **Step 3: Implement exact release validation and temporary installation**

```python
import argparse
import dataclasses
import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping, Protocol, Sequence


PYTHON = sys.executable
ALLOWED_METADATA_PATHS = ("CHANGELOG.md", "docs/e2e/gwo-v8-root-canary.md", "docs/releases/v8.0.0.md")


class ReleaseGateError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def digest_value(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class GaReleaseRecord:
    version: str
    repository: str
    evidence_base_sha: str
    canary_target_sha: str
    canary_receipt_digest: str
    activation_receipt_digest: str
    default_writer_receipt_digest: str
    post_canary_changed_paths: tuple[str, ...] = ALLOWED_METADATA_PATHS

    @classmethod
    def from_fixture(cls, fixture) -> "GaReleaseRecord":
        return cls(
            version=str(fixture.version),
            repository=str(fixture.repository),
            evidence_base_sha=str(fixture.evidence_base_sha),
            canary_target_sha=str(fixture.canary_target_sha),
            canary_receipt_digest=str(fixture.canary_receipt_digest),
            activation_receipt_digest=str(fixture.activation_receipt_digest),
            default_writer_receipt_digest=str(fixture.default_writer_receipt_digest),
        )

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> "GaReleaseRecord":
        forbidden = {"main_sha", "ci_head_sha", "pytest_pass_count", "tag_candidate_sha", "final_metadata_commit_sha"}
        if forbidden.intersection(raw):
            raise ReleaseGateError("GA_STATIC_RECORD_CONTAINS_DYNAMIC_SHA_OR_CI")
        if str(raw["version"]) != "8.0.0":
            raise ReleaseGateError("GA_VERSION_INVALID")
        if not re.fullmatch(r"[0-9a-f]{40}", str(raw["evidence_base_sha"])) or not re.fullmatch(r"[0-9a-f]{40}", str(raw["canary_target_sha"])):
            raise ReleaseGateError("GA_STATIC_SHA_INVALID")
        return cls(
            version=str(raw["version"]),
            repository=str(raw["repository"]),
            evidence_base_sha=str(raw["evidence_base_sha"]),
            canary_target_sha=str(raw["canary_target_sha"]),
            canary_receipt_digest=str(raw["canary_receipt_digest"]),
            activation_receipt_digest=str(raw["activation_receipt_digest"]),
            default_writer_receipt_digest=str(raw["default_writer_receipt_digest"]),
            post_canary_changed_paths=tuple(str(path) for path in raw.get("post_canary_changed_paths", ALLOWED_METADATA_PATHS)),
        )


def write_ga_release_record(path: Path, fixture) -> Path:
    record = GaReleaseRecord.from_fixture(fixture)
    payload = {"schema": "gwo-v8-ga-release-record.v1", **dataclasses.asdict(record)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload))
    return path


def load_ga_release_record(path: Path) -> GaReleaseRecord:
    raw = json.loads(path.read_text("utf-8"))
    if raw.get("schema") != "gwo-v8-ga-release-record.v1":
        raise ReleaseGateError("GA_RELEASE_RECORD_SCHEMA_INVALID")
    return GaReleaseRecord.from_mapping(raw)


@dataclass(frozen=True, slots=True)
class CleanInstallResult:
    surfaces: tuple[str, str, str]
    public_names: tuple[str, str, str]
    source_checkout_imported: bool


def run(arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(arguments, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise ReleaseGateError(completed.stderr or completed.stdout)
    return completed


def clean_install_and_smoke(source: Path, run_root: Path) -> CleanInstallResult:
    roots = tuple(run_root / name / "skills" for name in (".agents", ".codex", ".claude"))
    install = [PYTHON, "scripts/sync_orchestrator.py", "--root", str(source), "--install"]
    check = [PYTHON, "scripts/sync_orchestrator.py", "--root", str(source), "--check"]
    for root in roots:
        install.extend(("--install-root", str(root)))
        check.extend(("--install-root", str(root)))
    run(install)
    run(check)
    for root in roots:
        python_root = str(root / "orchestrator" / "scripts")
        code = (
            f"import sys; sys.path.insert(0, {python_root!r}); "
            "from gwo_v8 import start, advance, inspect; "
            "print('advance,inspect,start')"
        )
        smoke = subprocess.run([PYTHON, "-I", "-c", code], cwd=run_root, text=True, capture_output=True)
        if smoke.returncode != 0 or smoke.stdout.strip() != "advance,inspect,start":
            raise ReleaseGateError("GA_CLEAN_INSTALL_PUBLIC_IMPORT_FAILED")
    return CleanInstallResult((".agents", ".codex", ".claude"), ("advance", "inspect", "start"), False)


@dataclass(frozen=True, slots=True)
class CiReadback:
    run_id: int
    head_sha: str
    conclusion: str
    pytest_pass_count: int


@dataclass(frozen=True, slots=True)
class ReleaseGateReceipt:
    version: str
    repository: str
    evidence_base_sha: str
    canary_target_sha: str
    tag_candidate_sha: str
    ci_run_id: int
    ci_head_sha: str
    pytest_pass_count: int
    canary_receipt_digest: str
    activation_receipt_digest: str
    default_writer_receipt_digest: str

    @classmethod
    def from_exact(cls, record, canary, activation, admission, ci, main_sha: str) -> "ReleaseGateReceipt":
        return cls(
            version=record.version,
            repository=record.repository,
            evidence_base_sha=record.evidence_base_sha,
            canary_target_sha=canary.canary_target_sha,
            tag_candidate_sha=main_sha,
            ci_run_id=int(ci.run_id),
            ci_head_sha=ci.head_sha,
            pytest_pass_count=int(ci.pytest_pass_count),
            canary_receipt_digest=record.canary_receipt_digest,
            activation_receipt_digest=record.activation_receipt_digest,
            default_writer_receipt_digest=record.default_writer_receipt_digest,
        )


class GitAncestryReadback(Protocol):
    def is_ancestor(self, ancestor: str, descendant: str) -> bool: ...
    def changed_paths(self, ancestor: str, descendant: str) -> tuple[str, ...]: ...


def verify_pre_tag(record: GaReleaseRecord, *, main_sha: str, canary, activation, admission, ci: CiReadback, git: GitAncestryReadback) -> ReleaseGateReceipt:
    if main_sha != ci.head_sha or ci.conclusion != "success" or ci.pytest_pass_count < 1:
        raise ReleaseGateError("GA_EXACT_CI_REQUIRED")
    if canary.receipt_digest != record.canary_receipt_digest:
        raise ReleaseGateError("GA_CANARY_RECEIPT_MISMATCH")
    if activation.receipt_digest != record.activation_receipt_digest or activation.repository != record.repository or activation.writer_generation != "v8":
        raise ReleaseGateError("GA_ACTIVATION_READBACK_INVALID")
    if (
        getattr(admission.mode, "value", admission.mode) != "default_v8"
        or admission.receipt_digest != record.default_writer_receipt_digest
        or admission.acceptance_receipt_digest != canary.receipt_digest
        or admission.activation_id != activation.activation_id
    ):
        raise ReleaseGateError("GA_DEFAULT_WRITER_READBACK_INVALID")
    if not git.is_ancestor(record.evidence_base_sha, main_sha) or not git.is_ancestor(canary.canary_target_sha, main_sha):
        raise ReleaseGateError("GA_CANARY_SHA_NOT_ANCESTOR")
    changed_paths = tuple(git.changed_paths(record.evidence_base_sha, main_sha))
    if set(changed_paths) != set(record.post_canary_changed_paths) or set(changed_paths) - set(ALLOWED_METADATA_PATHS):
        raise ReleaseGateError("GA_POST_CANARY_DELTA_NOT_METADATA_ONLY")
    return ReleaseGateReceipt.from_exact(record, canary, activation, admission, ci, main_sha)


def parse_pytest_count(log: str) -> int:
    matches = re.findall(r"(\d+) passed", log)
    if not matches:
        raise ReleaseGateError("GA_CI_PYTEST_COUNT_MISSING")
    return int(matches[-1])


@dataclass(frozen=True, slots=True)
class GitCliReadback:
    repository: str

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        return subprocess.run(("git", "merge-base", "--is-ancestor", ancestor, descendant), check=False).returncode == 0

    def changed_paths(self, ancestor: str, descendant: str) -> tuple[str, ...]:
        output = subprocess.check_output(("git", "diff", "--name-only", f"{ancestor}..{descendant}"), text=True)
        return tuple(line for line in output.splitlines() if line)


def _snapshot(path: Path) -> SimpleNamespace:
    return SimpleNamespace(**json.loads(path.read_text("utf-8")))


def verify_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pre-tag", action="store_true")
    parser.add_argument("--post-release", action="store_true")
    parser.add_argument("--main-sha")
    parser.add_argument("--record", type=Path)
    parser.add_argument("--canary", type=Path)
    parser.add_argument("--activation", type=Path)
    parser.add_argument("--default-writer", type=Path)
    parser.add_argument("--ci-run", type=int)
    parser.add_argument("--repository", default="NOirBRight/github-work-orchestrator")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--tag")
    parser.add_argument("--run-root", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.pre_tag:
            record = load_ga_release_record(args.record)
            run_json = json.loads(subprocess.check_output(("gh", "run", "view", str(args.ci_run), "--repo", args.repository, "--json", "headSha,conclusion"), text=True))
            log = subprocess.check_output(("gh", "run", "view", str(args.ci_run), "--repo", args.repository, "--log"), text=True)
            ci = CiReadback(run_id=args.ci_run, head_sha=str(run_json["headSha"]), conclusion=str(run_json["conclusion"]), pytest_pass_count=parse_pytest_count(log))
            receipt = verify_pre_tag(
                record,
                main_sha=str(args.main_sha),
                canary=_snapshot(args.canary),
                activation=_snapshot(args.activation),
                admission=_snapshot(args.default_writer),
                ci=ci,
                git=GitCliReadback(args.repository),
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(canonical_json_bytes(dataclasses.asdict(receipt)))
            return 0
        if args.post_release:
            source = args.run_root / "tag-source"
            source.mkdir(parents=True, exist_ok=True)
            archive = subprocess.check_output(("git", "archive", "--format=tar", str(args.tag)))
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
                tar.extractall(source, filter="data")
            result = clean_install_and_smoke(source, args.run_root)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(canonical_json_bytes(dataclasses.asdict(result)))
            return 0
        return 3
    except (ReleaseGateError, subprocess.CalledProcessError, OSError, KeyError, TypeError, ValueError):
        return 2


if __name__ == "__main__":
    raise SystemExit(verify_main())
```

```python
RELEASE_CONTRACT = """# GWO V8 GA Release Contract

Schema: `gwo-v8-ga-release-record.v1`

The committed record contains `evidence_base_sha` and `canary_target_sha`, the
three receipt digests, and the exact metadata path allow-list. It deliberately
contains no tag-candidate SHA, final metadata commit SHA, CI run ID, or pytest
count. The pre-tag receipt obtains those values from the merged-main readback.
"""


def write_release_contract(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(RELEASE_CONTRACT, encoding="utf-8")
```

The following code belongs to `scripts/render_v8_ga_metadata.py` and is the
TDD-tested generator consumed by Task 7. It writes all three final documents
from real readbacks while intentionally excluding dynamic tag-candidate and CI
values from committed metadata:

```python
import argparse
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping, Sequence

from scripts.verify_v8_ga_release import ReleaseGateError, write_ga_release_record


def _require_sha(name: str, value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ReleaseGateError(f"GA_{name.upper()}_SHA_INVALID")
    return value


def _write_markdown_json(path: Path, title: str, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fence = chr(96) * 3
    path.write_text(f"# {title}\n\n" + fence + "json\n" + json.dumps(payload, sort_keys=True, indent=2) + "\n" + fence + "\n", encoding="utf-8")


def render_ga_documents(
    output_root: Path,
    *,
    evidence_base_sha: str,
    tickets: Mapping[str, object],
    acceptance: Mapping[str, object],
    named_admission: Mapping[str, object],
    default_writer: Mapping[str, object],
) -> tuple[Path, Path, Path]:
    evidence_base_sha = _require_sha("evidence_base", evidence_base_sha)
    canary_target_sha = _require_sha("canary_target", str(acceptance["canary_target_sha"]))
    common = {
        "repository": str(acceptance["repository"]),
        "campaign_key": str(acceptance["campaign_key"]),
        "evidence_base_sha": evidence_base_sha,
        "canary_target_sha": canary_target_sha,
        "ticket_manifest": tickets,
        "canary_receipt_digest": str(acceptance["receipt_digest"]),
        "named_admission_receipt_digest": str(named_admission["receipt_digest"]),
        "default_writer_receipt_digest": str(default_writer["receipt_digest"]),
        "activation_id": str(default_writer["activation_id"]),
        "writer_generation": str(default_writer["writer_generation"]),
    }
    changelog = output_root / "CHANGELOG.md"
    changelog.parent.mkdir(parents=True, exist_ok=True)
    entry = (
        "## 8.0.0\n\n"
        f"- Accepted root Canary receipt `{common['canary_receipt_digest']}`.\n"
        f"- Evidence base `{evidence_base_sha}` and Canary target `{canary_target_sha}` were read back.\n"
        "- Final tag-candidate SHA and exact CI are verified by the pre-tag receipt after this metadata commit is merged.\n"
    )
    previous = changelog.read_text("utf-8") if changelog.exists() else ""
    if "## 8.0.0" in previous:
        raise ReleaseGateError("GA_CHANGELOG_VERSION_ALREADY_PRESENT")
    if previous.startswith("# Changelog"):
        previous = previous[len("# Changelog"):].lstrip("\n")
    changelog.write_text("# Changelog\n\n" + entry + ("\n" + previous if previous else ""), encoding="utf-8")
    acceptance_doc = output_root / "docs/e2e/gwo-v8-root-canary.md"
    _write_markdown_json(acceptance_doc, "GWO V8 Root Canary Evidence", common | {"acceptance": acceptance})
    release_note = output_root / "docs/releases/v8.0.0.md"
    _write_markdown_json(
        release_note,
        "GWO V8.0.0",
        common | {"release": {"version": "8.0.0", "tag_and_ci_source": "pre-tag ReleaseGateReceipt"}},
    )
    return changelog, acceptance_doc, release_note


def write_live_release_record(
    path: Path,
    *,
    evidence_base_sha: str,
    acceptance: Mapping[str, object],
    named_admission: Mapping[str, object],
    default_writer: Mapping[str, object],
) -> Path:
    fixture = SimpleNamespace(
        version="8.0.0",
        repository=str(acceptance["repository"]),
        evidence_base_sha=_require_sha("evidence_base", evidence_base_sha),
        canary_target_sha=_require_sha("canary_target", str(acceptance["canary_target_sha"])),
        canary_receipt_digest=str(acceptance["receipt_digest"]),
        activation_receipt_digest=str(named_admission["receipt_digest"]),
        default_writer_receipt_digest=str(default_writer["receipt_digest"]),
    )
    return write_ga_release_record(path, fixture)


def render_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--evidence-base-sha", required=True)
    parser.add_argument("--tickets", type=Path, required=True)
    parser.add_argument("--acceptance", type=Path, required=True)
    parser.add_argument("--named-admission", type=Path, required=True)
    parser.add_argument("--default-writer", type=Path, required=True)
    parser.add_argument("--release-record", type=Path)
    args = parser.parse_args(argv)
    read = lambda path: dict(json.loads(path.read_text("utf-8")))
    inputs = {
        "evidence_base_sha": args.evidence_base_sha,
        "tickets": read(args.tickets),
        "acceptance": read(args.acceptance),
        "named_admission": read(args.named_admission),
        "default_writer": read(args.default_writer),
    }
    render_ga_documents(
        args.root,
        evidence_base_sha=inputs["evidence_base_sha"],
        tickets=inputs["tickets"],
        acceptance=inputs["acceptance"],
        named_admission=inputs["named_admission"],
        default_writer=inputs["default_writer"],
    )
    if args.release_record is not None:
        write_live_release_record(
            args.release_record,
            evidence_base_sha=inputs["evidence_base_sha"],
            acceptance=inputs["acceptance"],
            named_admission=inputs["named_admission"],
            default_writer=inputs["default_writer"],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(render_main())
```

The PowerShell/CLI adapter parses the pytest count from the exact run log with
`re.findall(r"(\\d+) passed", log)` into `CiReadback`; it never writes a
planning-time count into `GaReleaseRecord`. `write_release_contract` creates
`docs/releases/gwo-v8-ga-release-contract.md`, and `render_ga_documents`
creates `CHANGELOG.md`, `docs/e2e/gwo-v8-root-canary.md`, and
`docs/releases/v8.0.0.md` only after real receipt values exist.

- [ ] **Step 4: Run GREEN and commit the fixed release gate**

```powershell
py -3.13 -m pytest tests/test_v8_release_metadata.py tests/test_v8_clean_install.py -q
py -3.13 scripts/quick_validate.py
py -3.13 -c "from pathlib import Path; from scripts.verify_v8_ga_release import write_release_contract; write_release_contract(Path('docs/releases/gwo-v8-ga-release-contract.md'))"
git diff --check
git add docs/releases/gwo-v8-ga-release-contract.md scripts/verify_v8_ga_release.py scripts/render_v8_ga_metadata.py tests/test_v8_release_metadata.py tests/test_v8_clean_install.py
git diff --cached --check
git commit -m "test: define the GWO V8.0.0 release gate"
```

Expected: PASS with temporary installs in `.agents`, `.codex`, `.claude` order and no import from the source checkout. This task does not claim GA or create final release notes.

---

### Task 7: Execute the Real Root Canary and Promote the Default

**Files:**
- Create at execution time: a run-local Ticket manifest, public diagnostics snapshots, fault-proxy journal, `RootCanaryAcceptanceReceiptV1`, Activation/admission receipts, and exact GitHub readbacks under the approved evidence root.
- Modify after the run: `CHANGELOG.md`, `docs/e2e/gwo-v8-root-canary.md`, and `docs/releases/v8.0.0.md` with the exact immutable receipt values before their final PR merge.

**Interfaces:**
- Consumes: closed #118/#123/#136/#137 readback, four real Ticket refs, #118 Guard receipt, explicit authorization, and Tasks 1-6.
- Produces: accepted root Canary, default-V8 readback, or a frozen admission receipt. This task never silently closes or relabels Issues.

- [ ] **Step 1: Prove all external blockers and the exact root Tickets before mutation**

```powershell
$runRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("gwo-v8-ga-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $runRoot | Out-Null
$campaignKey = 'campaign:gwo-v8-ga-root:' + [guid]::NewGuid().ToString('N')
$repo = 'NOirBRight/github-work-orchestrator'
foreach ($number in 118,123,136,137) {
    $gate = gh issue view $number --repo $repo --json number,state,closedAt,body,comments | ConvertFrom-Json
    if ($gate.state -ne 'CLOSED') { throw "Root Canary gate #$number is not closed." }
}
$requiredTicketApproval = 'CREATE-GWO-V8-GA-ROOT-CANARY-TICKETS'
if ($env:GWO_V8_ROOT_TICKET_APPROVAL -ne $requiredTicketApproval) { throw 'STOP for explicit root-Ticket creation approval.' }
py -3.13 scripts/provision_v8_root_canary.py --repository $repo --approval $env:GWO_V8_ROOT_TICKET_APPROVAL --output $runRoot/tickets.json
py -3.13 scripts/provision_v8_root_canary.py --repository $repo --read-only --output $runRoot/tickets-readback.json
```

Expected: exactly four OPEN `ready-for-agent` Issues, complete bodies/comments, zero open/native blockers, four disjoint paths, and identical first/second contract digests. Stop before activation if approval was not explicitly supplied by the owner.

- [ ] **Step 2: Activate only the named Canary and run all fault/restart waves**

```powershell
$requiredActivationApproval = "ACTIVATE-NAMED-CANARY:$campaignKey"
if ($env:GWO_V8_ACTIVATION_APPROVAL -ne $requiredActivationApproval) { throw 'STOP for explicit named-Canary activation approval.' }
$sourceCommit = git rev-parse origin/main
py -3.13 scripts/cutover_guard.py --live --repository $repo --control-branch gwo-control --target-branch main --source-writer-generation v6.1 --target-writer-generation v8 --store-generation store:v8:0001 --source-commit $sourceCommit --package-root (Get-Location).Path --install-root "$HOME/.agents/skills" --install-root "$HOME/.codex/skills" --install-root "$HOME/.claude/skills" --json > $runRoot/guard.json
py -3.13 scripts/run_v8_ga_activation.py --mode named-canary --repository $repo --control-db $runRoot/admission.db --campaign-key $campaignKey --tickets $runRoot/tickets.json --guard $runRoot/guard.json --approval $env:GWO_V8_ACTIVATION_APPROVAL --output $runRoot/admission.json
@'
{
  "schema": "gwo-v8-root-fault-plan.v1",
  "events": [
    {"role": "worker", "point": "candidate_persisted_before_ack", "count": 1},
    {"role": "review", "point": "finding_ledger_persisted_before_ack", "count": 1},
    {"role": "delivery", "point": "hosted_receipt_persisted_before_ack", "count": 1},
    {"role": "wake", "point": "lost_duplicate_reordered", "count": 1},
    {"role": "permission", "point": "same_binding", "count": 1},
    {"role": "runtime", "point": "terminal_evidence_replacement", "count": 1}
  ]
}
'@ | Set-Content -Encoding utf8NoBOM $runRoot/fault-plan.json
do {
    py -3.13 scripts/run_v8_canary.py --manifest $runRoot/tickets.json --campaign-key $campaignKey --fault-plan $runRoot/fault-plan.json --evidence $runRoot/diagnostics.json
    $runnerExit = $LASTEXITCODE
    if ($runnerExit -notin 0,75) { throw "Root Canary runner failed with $runnerExit" }
} while ($runnerExit -eq 75)
```

Expected: one Campaign/Planning Pass, peak four Slots with deterministic refill, four Candidate receipt chains, same-binding permission, complete Finding/repair bounds, one stale diagnosis per binding, at most one terminal replacement binding, and no duplicated semantic/external effect after each fresh process.

- [ ] **Step 3: Verify two delivery boundaries and promote only the accepted receipt**

```powershell
py -3.13 scripts/verify_v8_root_canary.py --tickets $runRoot/tickets-readback.json --diagnostics $runRoot/diagnostics.json --github-live --output $runRoot/canary-acceptance.json
if ($LASTEXITCODE -ne 0) {
    py -3.13 scripts/run_v8_ga_activation.py --mode freeze --repository $repo --control-db $runRoot/admission.db --current $runRoot/admission.json --reason ROOT_CANARY_REJECTED --output $runRoot/frozen.json
    throw 'Root Canary rejected; admission is frozen and V6.1 was not selected.'
}
$acceptance = Get-Content $runRoot/canary-acceptance.json -Raw | ConvertFrom-Json
$requiredDefaultApproval = "PROMOTE-V8-DEFAULT:$($acceptance.receipt_digest)"
if ($env:GWO_V8_DEFAULT_APPROVAL -ne $requiredDefaultApproval) { throw 'STOP for explicit V8-default promotion approval.' }
py -3.13 scripts/run_v8_ga_activation.py --mode default --repository $repo --control-db $runRoot/admission.db --current $runRoot/admission.json --acceptance $runRoot/canary-acceptance.json --approval $env:GWO_V8_DEFAULT_APPROVAL --output $runRoot/default-writer.json
py -3.13 scripts/run_v8_ga_activation.py --mode readback --repository $repo --control-db $runRoot/admission.db --output $runRoot/fresh-default-readback.json
py -3.13 -m pytest tests/test_v8_ga_activation.py::test_accepted_canary_promotes_default_and_fresh_start_reads_v8 -q
$requiredIssueApproval = "CLOSE-119:$($acceptance.receipt_digest)"
if ($env:GWO_V8_ISSUE119_APPROVAL -ne $requiredIssueApproval) { throw 'STOP for explicit #119 close approval.' }
gh issue comment 119 --repo $repo --body "Accepted root Canary receipt: $($acceptance.receipt_digest); evidence will be committed in docs/e2e/gwo-v8-root-canary.md."
gh issue close 119 --repo $repo --reason completed
$issue119 = gh issue view 119 --repo $repo --json number,state,closedAt,comments | ConvertFrom-Json
if ($issue119.state -ne 'CLOSED') { throw '#119 did not read back CLOSED after the approved evidence update.' }
```

Expected: three Standard Candidates share one exact Batch/PR/hosted run; the Strict Candidate has a separate Singleton/PR/hosted run; both target mutations are serialized and read back. The fresh process names the same `repository`, Campaign/Plan Revision, expected previous authority, Activation ID, writer generation, and `default_v8` receipt. On any failure the frozen receipt remains and rollback is not automatic.

- [ ] **Step 4: Commit exact evidence through the final metadata PR**

```powershell
$acceptance = Get-Content $runRoot/canary-acceptance.json -Raw | ConvertFrom-Json
$evidenceBaseSha = [string]$acceptance.canary_target_sha
py -3.13 scripts/render_v8_ga_metadata.py --root (Get-Location).Path --evidence-base-sha $evidenceBaseSha --tickets $runRoot/tickets-readback.json --acceptance $runRoot/canary-acceptance.json --named-admission $runRoot/admission.json --default-writer $runRoot/default-writer.json --release-record $runRoot/ga-release-record.json
foreach ($path in @('CHANGELOG.md', 'docs/e2e/gwo-v8-root-canary.md', 'docs/releases/v8.0.0.md')) {
    if (-not (Test-Path $path)) { throw "Metadata generator did not create $path" }
}
py -3.13 -m pytest -q
py -3.13 scripts/quick_validate.py
git diff --check
git add CHANGELOG.md docs/e2e/gwo-v8-root-canary.md docs/releases/v8.0.0.md
git diff --cached --check
git commit -m "docs: record accepted V8 root Canary evidence"
```

Expected: the generator's actual `render_ga_documents` body writes all three documents with the real four Issue numbers, both Batch/PR/CI/target receipts, fault/restart proof, Canary receipt, Activation/admission/default-writer receipts, `evidence_base_sha`, and `canary_target_sha`. It writes no final metadata commit SHA or CI count; those are produced by the dynamic pre-tag receipt. Merge only after exact PR and post-merge main CI succeed.

---

### Task 8: Publish Immutable `v8.0.0` and Run Post-Release Smoke

**Files:**
- Read only after merge: `CHANGELOG.md`, `docs/releases/v8.0.0.md`, both package manifests, and the Task 7 evidence receipts.
- Create outside Git: temporary `.agents`, `.codex`, and `.claude` install roots plus post-release smoke JSON.

**Interfaces:**
- Consumes: exact green merged-main SHA and dynamic test summary, accepted Canary/default writer receipts, and clean-install verification.
- Produces: immutable annotated `v8.0.0`, a published GitHub Release, and a post-release public-API readback.

- [ ] **Step 1: Re-run the complete pre-tag gate on the merged SHA**

```powershell
git fetch origin main --tags
$sha = git rev-parse origin/main
$run = gh run list --repo NOirBRight/github-work-orchestrator --commit $sha --workflow 'GWO CI' --status success --limit 1 --json databaseId,headSha,conclusion,url | ConvertFrom-Json
if ($run.Count -ne 1 -or $run.headSha -ne $sha -or $run.conclusion -ne 'success') { throw 'GA exact-main CI missing.' }
$summary = @(gh run view $run.databaseId --repo NOirBRight/github-work-orchestrator --log | Select-String -Pattern '[0-9]+ passed')
if ($summary.Count -eq 0) { throw 'GA CI has no dynamic pytest pass summary.' }
$summary[-1].Line
$issue119 = gh issue view 119 --repo NOirBRight/github-work-orchestrator --json number,state,closedAt,comments | ConvertFrom-Json
if ($issue119.state -ne 'CLOSED') { throw '#119 is not closed by accepted Canary evidence.' }
py -3.13 scripts/verify_v8_ga_release.py --pre-tag --main-sha $sha --record $runRoot/ga-release-record.json --canary $runRoot/canary-acceptance.json --activation $runRoot/admission.json --default-writer $runRoot/default-writer.json --ci-run $run.databaseId --output $runRoot/pre-tag-receipt.json
$preTag = Get-Content $runRoot/pre-tag-receipt.json -Raw | ConvertFrom-Json
if ($preTag.tag_candidate_sha -ne $sha -or $preTag.ci_head_sha -ne $sha -or [int]$preTag.pytest_pass_count -lt 1) { throw 'Dynamic pre-tag receipt does not bind the exact tag candidate and CI.' }
py -3.13 scripts/sync_orchestrator.py --check
```

Expected: #119 reads CLOSED by the accepted Canary evidence; Canary, Activation, and default-writer receipts identify the same release subject; package manifests are 8.0.0 and have no drift.

- [ ] **Step 2: Clean-install the exact source into all three temporary surfaces**

```powershell
$installRoot = Join-Path $runRoot 'clean-install'
$agents = Join-Path $installRoot '.agents/skills'
$codex = Join-Path $installRoot '.codex/skills'
$claude = Join-Path $installRoot '.claude/skills'
py -3.13 scripts/sync_orchestrator.py --install --install-root $agents --install-root $codex --install-root $claude
py -3.13 scripts/sync_orchestrator.py --check --install-root $agents --install-root $codex --install-root $claude
py -3.13 -m pytest tests/test_v8_clean_install.py -q
```

Expected: both packages match manifests on `.agents`, `.codex`, and `.claude`; a fresh Python process imports only installed `start/advance/inspect`; user installations are untouched.

- [ ] **Step 3: Create, push, peel-verify, and publish in the immutable order**

```powershell
if (git ls-remote --tags origin refs/tags/v8.0.0) { throw 'v8.0.0 already exists; never move it.' }
git tag -a v8.0.0 $sha -m 'GWO V8.0.0 GA'
git push origin refs/tags/v8.0.0
$peeled = ((git ls-remote --tags origin 'refs/tags/v8.0.0^{}').Trim() -split '\s+')[0]
if ($peeled -ne $sha) { throw 'Remote v8.0.0 does not peel to the approved main SHA.' }
gh release create v8.0.0 --repo NOirBRight/github-work-orchestrator --verify-tag --title 'GWO V8.0.0' --notes-file docs/releases/v8.0.0.md
gh release view v8.0.0 --repo NOirBRight/github-work-orchestrator --json tagName,targetCommitish,isDraft,isPrerelease,publishedAt,url
```

Expected: annotated remote tag peels to `$sha`; Release is published, non-draft, non-prerelease, and uses the exact immutable tag. `gh release create --verify-tag` is never run before the push and peeled-SHA check.

- [ ] **Step 4: Run post-release clean-install and default-writer smoke**

```powershell
py -3.13 scripts/verify_v8_ga_release.py --post-release --tag v8.0.0 --run-root $runRoot/post-release --output $runRoot/post-release/release-smoke.json
git ls-remote --tags origin refs/tags/v8.0.0 'refs/tags/v8.0.0^{}'
```

Expected: a clean install from the tag passes on all three surfaces, the public API has exactly the three workflow operations, Release/tag SHA is exact, and a fresh read-only inspection returns the same V8 default-writer receipt. A post-release failure freezes new admission and starts incident handling; it does not move the tag, delete the Release, or automatically restore V6.1.

## #119 Acceptance Coverage

| #119 requirement | Plan proof |
| --- | --- |
| Four complete real root Tickets and closed blockers | Task 1 plus Task 7 Step 1 authoritative GitHub readback |
| One Planning Pass, four Slots/refill, Runtime selector, authority root | Task 3 proof fields and Task 4 verifier |
| Three Standard in one Batch; Strict Singleton | Task 1 fixed contracts and Task 4 exact two-Batch verifier |
| Separate local suite, PR, hosted CI, serial integration, target readback | Task 4 `require_*` boundaries and live GitHub readback |
| Lost/duplicate/reordered wakes, process restart, no duplicate effects | Task 3 fault proxy/table tests and Task 7 fault waves |
| Permission same binding, Finding/repair bounds, stale diagnosis, terminal replacement | Task 3 proof projection and Task 4 fail-closed verification |
| Public surface only `start`, `advance`, `inspect` | Task 2 AST test and Task 8 clean-install smoke |
| #118/#123 gates, named admission, success-only default | Tasks 5 and 7 |
| Failure freezes and no automatic V6.1 fallback | Tasks 5, 7, and 8 |
| Immutable GA release and post-release smoke | Tasks 6 and 8 |

## Final Self-Review Before Execution

- [ ] Parse every Markdown fence and confirm no task heading is inside an unintended code block.
- [ ] Search for unresolved-marker phrases and undefined non-Protocol bodies; fix every hit before handoff.
- [ ] Re-read Issues #118, #119, #123, #136, and #137 plus ADR-0046 and verify the dependency/readback order remains exact.
- [ ] Confirm `scripts/run_v8_canary.py` has exactly one `gwo_v8` import containing only `start`, `advance`, and `inspect`.
- [ ] Confirm all four live Issue refs belong to `NOirBRight/github-work-orchestrator`, are `ready-for-agent`, have complete contracts and no open blockers, and are not synthetic fixtures.
- [ ] Confirm every changed Skill-package commit performs sync, then `--check`, and stages `skills/orchestrator/.skill-package.json`.
- [ ] Confirm publication still performs annotated tag, push, peeled-SHA verification, then `gh release create --verify-tag`, Release readback, and post-release clean-install smoke.
