from __future__ import annotations

from dataclasses import asdict, replace
import json
from pathlib import Path
import sqlite3
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from gwo_v8 import (  # noqa: E402
    ActivationError,
    AuthoritativeNodeReadback,
    AuthoritativeRepositoryReadback,
    CanaryRunReadback,
    CanaryRunVerifier,
    EvidenceVerifier,
    ExecutionBudgetReadback,
    GitHubCanaryEvidenceControl,
    GitHubContent,
    GitHubLegacyWriterControl,
    GitHubWriterTransitionControl,
    InMemoryCanaryEvidenceControl,
    InMemoryDurablePlanControl,
    InMemoryLegacyWriterControl,
    InMemoryRuntimeAdapter,
    InMemoryWriterTransitionControl,
    InMemoryV8OwnershipControl,
    Kernel,
    KernelError,
    LocalPlanPublication,
    LegacyWriterReadback,
    PlanCompiler,
    ReviewAxisBinding,
    ReviewAxisObservation,
    ReviewChildReadback,
    RuntimeBinding,
    RuntimePrompt,
    ShadowEvaluator,
    StoreV8OwnershipControl,
    StoreReconstructor,
    TypedEvidence,
    V8OwnershipReadback,
    WriterCutoverController,
)
from gwo_v8._canonical import canonical_bytes, digest_value  # noqa: E402


class _GitHubContentClient:
    def __init__(self):
        self.contents: dict[tuple[str, str, str], GitHubContent] = {}
        self.writes = 0

    def read(self, repository: str, branch: str, path: str):
        return self.contents.get((repository, branch, path))

    def compare_and_swap(
        self,
        repository: str,
        branch: str,
        path: str,
        content: bytes,
        *,
        expected_blob_sha: str | None,
        message: str,
    ):
        del message
        key = (repository, branch, path)
        current = self.contents.get(key)
        current_sha = None if current is None else current.blob_sha
        if current_sha != expected_blob_sha:
            raise RuntimeError("compare-and-swap conflict")
        self.writes += 1
        written = GitHubContent(content=content, blob_sha=f"blob:{self.writes}")
        self.contents[key] = written
        return written


class _FailFinalTransitionOnce(InMemoryWriterTransitionControl):
    def __init__(self):
        super().__init__(initial_writer="v6.1")
        self.failed = False

    def publish(self, record):
        if record.status == "cut_over" and not self.failed:
            self.failed = True
            raise RuntimeError("simulated final transition CAS conflict")
        super().publish(record)


def test_production_legacy_writer_fence_survives_restart_and_preserves_readback():
    client = _GitHubContentClient()
    observed = LegacyWriterReadback(
        repository="owner/repo",
        stopped=False,
        active_dispatches=("dispatch-7",),
        integration_lease=True,
        active_workers=("agent-7",),
    )
    control = GitHubLegacyWriterControl(
        client,
        branch="gwo-control",
        execution_readback=lambda repository: replace(
            observed,
            repository=repository,
        ),
    )

    control.stop("owner/repo", action_key="stop-v61:owner-repo")
    control.stop("owner/repo", action_key="stop-v61:owner-repo")
    assert client.writes == 1

    recovered = GitHubLegacyWriterControl(
        client,
        branch="gwo-control",
        execution_readback=lambda repository: replace(
            observed,
            repository=repository,
        ),
    )
    assert recovered.readback("owner/repo") == replace(observed, stopped=True)

    recovered.restore("owner/repo", action_key="restore-v61:owner-repo")
    recovered.restore("owner/repo", action_key="restore-v61:owner-repo")
    assert client.writes == 2
    assert control.readback("owner/repo") == observed
    durable = json.loads(
        client.contents[
            (
                "owner/repo",
                "gwo-control",
                ".gwo-v8/legacy-writer-fence.json",
            )
        ].content
    )
    assert [event["operation"] for event in durable["events"]] == [
        "stop",
        "restore",
    ]


def _compiled(
    count: int = 3,
    *,
    reviewed: frozenset[int] = frozenset(),
    hosted: bool = False,
):
    work_items = []
    nodes = []
    command = ["python", "-c", "raise SystemExit(0)"]
    for ordinal in range(1, count + 1):
        path = f"module-{ordinal}.txt"
        work_item_key = f"issue:{ordinal}"
        work_items.append(
            {
                "work_item_key": work_item_key,
                "tracker_state": "ready-for-agent",
                "source_ref": f"github://issue/{ordinal}",
                "title": f"Module {ordinal}",
                "outcome_contract": {"path": path, "content": f"module {ordinal}\n"},
            }
        )
        checks = [{"check_id": "local", "command": command}]
        if hosted:
            checks.append(
                {
                    "check_id": "hosted",
                    "command": command,
                }
            )
        nodes.append(
            {
                "goal_key": "goal:phase-4bc",
                "work_item_key": work_item_key,
                "kind": "work",
                "inputs": {
                    "file_changes": [{"path": path, "content": f"module {ordinal}\n"}]
                },
                "output_contract": {
                    "required_evidence": [
                        {"kind": "candidate"},
                        {"kind": "check", "check_id": "local"},
                    ],
                    "checks": checks,
                },
                "effect_contract": {
                    "write_scopes": [path],
                    "external_effects": [],
                },
                "resource_claims": [f"module:{ordinal}"],
                "runtime_requirements": {"capabilities": ["git", "local_check"]},
                "difficulty": "standard",
                "risk": "standard" if ordinal in reviewed else "low",
                "recovery_policy": {"semantic_attempts": 2, "repair_rounds": 1},
                "skill_reference": None,
            }
        )
    policy = {"version": 2}
    if hosted:
        policy = {
            "version": 3,
            "low_risk_allowlist": ["module-*.txt"],
            "check_definitions": [
                {
                    "check_id": "local",
                    "version": 1,
                    "command": command,
                    "hosted_name": None,
                    "environment_requirements": [],
                    "input_selector": ["module-*.txt"],
                    "base_sensitive": False,
                    "risk": "low",
                    "hosted_only": False,
                    "suite": "repository",
                },
                {
                    "check_id": "hosted",
                    "version": 1,
                    "command": command,
                    "hosted_name": "Phase 4BC CI",
                    "environment_requirements": [],
                    "input_selector": ["module-*.txt"],
                    "base_sensitive": False,
                    "risk": "low",
                    "hosted_only": True,
                    "suite": "hosted",
                },
            ],
            "strict_review": {
                "specialist_requirements": [],
                "human_decision_required": True,
            },
        }
    return PlanCompiler().compile(
        {
            "parent_plan_digest": None,
            "goals": [
                {
                    "goal_key": "goal:phase-4bc",
                    "objective": "Prove reconstruction and cutover.",
                    "acceptance": ["Every module is integrated."],
                }
            ],
            "nodes": nodes,
            "edges": [],
        },
        {
            "repository": "local/phase-four-bc",
            "work_items": work_items,
        },
        policy,
    )


def _durable_readback(
    tmp_path: Path,
    *,
    count: int = 3,
    reviewed: frozenset[int] = frozenset(),
):
    compiled = _compiled(count, reviewed=reviewed)
    durable = InMemoryDurablePlanControl()
    publication = LocalPlanPublication(tmp_path / "source.sqlite3", durable=durable)
    publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="v8-canary",
    )
    return compiled, durable


def _capture(
    *,
    kind: str,
    subject: str,
    observer_type: str,
    observer_id: str,
    source_ref: str,
    payload: dict,
) -> TypedEvidence:
    return TypedEvidence._capture(
        kind=kind,
        subject=subject,
        observer_type=observer_type,
        observer_id=observer_id,
        observed_at="2026-07-24T00:00:00+00:00",
        source_ref=source_ref,
        payload=payload,
    )


def _axis_payload(observation: ReviewAxisObservation) -> dict:
    return {
        "axis": observation.axis,
        "action_key": observation.action_key,
        "fixed_input_digest": observation.fixed_input_digest,
        "recovery_ordinal": observation.recovery_ordinal,
        "runtime": {
            "runtime_id": observation.runtime_id,
            "agent_id": observation.agent_id,
            "session_id": observation.session_id,
            "profile_digest": observation.profile_digest,
            "provider": observation.provider,
            "model": observation.model,
            "thinking": observation.thinking,
            "mode": observation.mode,
        },
        "output_digest": observation.output_digest,
        "findings": list(observation.findings),
    }


def _node(
    compiled,
    ordinal: int,
    *,
    status: str = "waiting",
    wait_condition: str | None = "hosted_ci",
    admission_id: str | None = None,
    attempt_id: str | None = None,
    integrated_sha: str | None = None,
) -> AuthoritativeNodeReadback:
    plan = json.loads(compiled.canonical_bytes)
    work = [item for item in plan["nodes"] if item["kind"] == "work"][ordinal - 1]
    candidate_sha = f"{ordinal:040x}"
    base_sha = f"{ordinal + 100:040x}"
    prompt = RuntimePrompt(
        text=f"implement module {ordinal}",
        digest=digest_value(f"implement module {ordinal}"),
        authority_digest=work["contract_digest"],
    )
    admission = admission_id or f"admission:{ordinal}"
    attempt = attempt_id or f"attempt:{ordinal}"
    runtime_id = f"runtime:{ordinal}"
    binding = RuntimeBinding(
        adapter="paseo",
        runtime_id=runtime_id,
        repository=compiled.repository,
        plan_digest=compiled.digest,
        node_key=work["node_key"],
        admission_id=admission,
        repository_path=str(ROOT),
        workspace=str(ROOT),
        prompt_accepted=True,
        prompt_digest=prompt.digest,
        attempt_id=attempt,
        agent_id=f"agent:{ordinal}",
        session_id=f"session:{ordinal}",
        workspace_id=f"workspace:{ordinal}",
        runtime_profile="standard",
        profile_digest=f"{ordinal:064x}",
        provider="kimi-cli",
        model="kimi-code/k2.7",
        thinking="max",
        mode="yolo",
        features_digest=f"{ordinal + 1:064x}",
        base_sha=base_sha,
    )
    candidate = _capture(
        kind="candidate",
        subject=candidate_sha,
        observer_type="runtime_adapter",
        observer_id=runtime_id,
        source_ref=f"git://candidate/{candidate_sha}",
        payload={"node_key": work["node_key"], "tree_sha": candidate_sha},
    )
    check = next(
        item
        for item in work["output_contract"]["checks"]
        if item.get("hosted_only") is not True
    )
    environment = {"platform": "test"}
    check_evidence = _capture(
        kind="check",
        subject=candidate_sha,
        observer_type="runtime_adapter",
        observer_id=runtime_id,
        source_ref=f"git://candidate/{candidate_sha}/checks/local",
        payload={
            "check_id": "local",
            "outcome": "passed",
            "definition_digest": check["definition_digest"],
            "command_digest": digest_value(check["command"]),
            "observed_tree_digest": candidate_sha,
            "environment_requirements": [],
            "environment_identity": environment,
            "environment_digest": digest_value(environment),
            "input_projection_digest": "a" * 64,
            "log_digest": "b" * 64,
        },
    )
    evidence = [candidate, check_evidence]
    review_bindings: tuple[ReviewAxisBinding, ...] = ()
    review_children: tuple[ReviewChildReadback, ...] = ()
    review_observations: tuple[ReviewAxisObservation, ...] = ()
    requirement = work["output_contract"]["review_requirement"]
    if requirement["mode"] != "none":
        review_bindings = tuple(
            ReviewAxisBinding(
                action_key=f"review:{axis}:{ordinal}",
                axis=axis,
                candidate_sha=candidate_sha,
                fixed_input_digest=f"{ordinal + index + 2:064x}",
                runtime_id=f"review-runtime:{axis}:{ordinal}",
                agent_id=f"review-agent:{axis}:{ordinal}",
                session_id=f"review-session:{axis}:{ordinal}",
                workspace_id=f"review-workspace:{axis}:{ordinal}",
                workspace=str(ROOT),
                parent_agent_id=binding.agent_id,
                runtime_profile="standard-axis",
                profile_digest=f"{ordinal + index + 3:064x}",
                provider="codex",
                model="gpt-5.6-sol",
                thinking="high",
                mode="default",
                prompt_digest=f"{ordinal + index + 4:064x}",
            )
            for index, axis in enumerate(("standards", "spec"))
        )
        review_observations = tuple(
            ReviewAxisObservation(
                lifecycle="completed",
                axis=child.axis,
                attempt_id=attempt,
                candidate_sha=candidate_sha,
                base_sha=base_sha,
                recovery_ordinal=0,
                spec_digest="c" * 64,
                check_manifest_digest="d" * 64,
                fixed_input_digest=child.fixed_input_digest,
                action_key=child.action_key,
                runtime_id=child.runtime_id,
                agent_id=child.agent_id,
                session_id=child.session_id,
                profile_digest=child.profile_digest,
                provider=child.provider,
                model=child.model,
                thinking=child.thinking,
                mode=child.mode,
                output_digest=f"{ordinal + index + 10:064x}",
                findings=(),
            )
            for index, child in enumerate(review_bindings)
        )
        review_children = tuple(
            ReviewChildReadback(
                recovery_ordinal=0,
                binding=child,
                observed_prompt_digest=child.prompt_digest,
            )
            for child in review_bindings
        )
        evidence.append(
            _capture(
                kind="review",
                subject=candidate_sha,
                observer_type="kernel",
                observer_id=runtime_id,
                source_ref=f"github://review/{candidate_sha}",
                payload={
                    "record_type": "envelope",
                    "attempt_id": attempt,
                    "candidate_sha": candidate_sha,
                    "acceptance_digest": "c" * 64,
                    "check_manifest_digest": "d" * 64,
                    "axes": [
                        _axis_payload(observation)
                        for observation in review_observations
                    ],
                },
            )
        )
    return AuthoritativeNodeReadback(
        node_key=work["node_key"],
        goal_key=work["goal_key"],
        work_item_key=work["work_item_key"],
        status=status,
        directive="goal_complete" if status == "complete" else "wait_for_hosted_ci",
        admission_id=admission,
        admission_state="consumed",
        attempt_id=attempt,
        attempt_state="verified" if status == "complete" else "parked",
        attempt_record_state="verified" if status == "complete" else "running",
        attempt_terminal_reason=None,
        budgets=ExecutionBudgetReadback(
            attempt_ordinal=1,
            repair_rounds_used=0,
            materialization_create_executions=1,
            materialization_prompt_executions=1,
            hosted_retry_count=0,
            runtime_observation_failures=0,
            runtime_circuits={},
        ),
        base_sha=base_sha,
        prompt=prompt,
        runtime_binding=binding,
        candidate_sha=candidate_sha,
        wait_condition=wait_condition,
        wait_source_ref=(
            None if wait_condition is None else f"github://checks/{candidate_sha}"
        ),
        publication_state=(
            "published" if wait_condition == "hosted_ci" else None
        ),
        publication_ref=(
            f"github://publication/{candidate_sha}"
            if wait_condition == "hosted_ci"
            else None
        ),
        hosted_check_state=(
            "pending" if wait_condition == "hosted_ci" else None
        ),
        hosted_check_evidence=(),
        worker_parked_for_ci=wait_condition == "hosted_ci",
        resume_sent=False,
        publication_eligible=True,
        evidence=tuple(evidence),
        review_children=review_children,
        review_observations=review_observations,
        held_resource_claims=(),
        integrated_sha=integrated_sha,
        candidate_source_ref=f"git://candidate/{candidate_sha}",
        integration_source_ref=(
            None if integrated_sha is None else f"git://main/{integrated_sha}"
        ),
    )


def _kernel(store: Path, durable, tmp_path: Path) -> Kernel:
    return Kernel(
        store_path=store,
        publication=LocalPlanPublication(store, durable=durable),
        runtime=InMemoryRuntimeAdapter(tmp_path / "runtime"),
        verifier=EvidenceVerifier(),
        repository_path=ROOT,
        integration_branch="dev",
        writer_generation="v8-canary",
        runtime_config={
            "active_turn_pools": {"workers": 8, "coordinators": 1},
            "tiers": {
                "light": {
                    "provider": "kimi-cli",
                    "settings": {
                        "model": "kimi-code/kimi-for-coding",
                        "thinkingOptionId": "on",
                        "modeId": "yolo",
                        "features": {},
                    },
                },
                "standard": {
                    "provider": "kimi-cli",
                    "settings": {
                        "model": "kimi-code/kimi-for-coding",
                        "thinkingOptionId": "on",
                        "modeId": "yolo",
                        "features": {},
                    },
                },
                "heavy": {
                    "provider": "kimi-cli",
                    "settings": {
                        "model": "kimi-code/k3",
                        "thinkingOptionId": "high",
                        "modeId": "yolo",
                        "features": {},
                    },
                },
                "frontier": {
                    "provider": "codex",
                    "settings": {
                        "model": "sol/xhigh",
                        "thinkingOptionId": "xhigh",
                        "modeId": "full-access",
                        "features": {},
                    },
                },
            },
            "repositories": {},
        },
    )


def test_fresh_store_atomically_reconstructs_native_kernel_state(tmp_path):
    compiled, durable = _durable_readback(
        tmp_path,
        count=2,
        reviewed=frozenset({2}),
    )
    readback = AuthoritativeRepositoryReadback.from_durable(
        durable,
        compiled.repository,
        nodes=(
            replace(
                _node(compiled, 1),
                held_resource_claims=("module:1",),
            ),
            _node(compiled, 2),
        ),
    )
    destination = tmp_path / "reconstructed.sqlite3"

    result = StoreReconstructor().reconstruct(readback, destination)

    assert result.status == "reconstructed", result.blockers
    assert result.active_plan_digest == compiled.digest
    assert result.node_count == result.admission_count == result.attempt_count == 2
    assert result.runtime_count == 2
    assert result.review_child_count == result.axis_observation_count == 2
    assert result.check_evidence_count == 2
    assert result.review_evidence_count == 1
    assert result.reviewer_lifecycle_count == 0
    with sqlite3.connect(destination) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM v8_node_execution_state"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM v8_verified_results"
        ).fetchone()[0] == 2
        claim_owner = connection.execute(
            """
            SELECT admission_id, attempt_id FROM v8_resource_claims
            WHERE resource_key = 'module:1'
            """
        ).fetchone()
        assert claim_owner == (None, "attempt:1")
        assert connection.execute(
            """
            SELECT COUNT(*) FROM sqlite_master
            WHERE name LIKE 'v8_reconstructed_%'
            """
        ).fetchone()[0] == 0


def test_reconstruction_preserves_integration_batch_identity(tmp_path):
    compiled, durable = _durable_readback(tmp_path, count=2)
    first = _node(
        compiled,
        1,
        status="complete",
        wait_condition=None,
        integrated_sha="f" * 40,
    )
    second_original = _node(
        compiled,
        2,
        status="complete",
        wait_condition=None,
        integrated_sha="f" * 40,
    )
    second = replace(
        second_original,
        base_sha=first.base_sha,
        runtime_binding=replace(
            second_original.runtime_binding,
            base_sha=first.base_sha,
        ),
    )
    batch_id = "batch:" + "b" * 24
    member_node_keys = [first.node_key, second.node_key]

    def with_batch(node):
        evidence = _capture(
            kind="integration",
            subject="f" * 40,
            observer_type="kernel",
            observer_id="v8-canary",
            source_ref="git://local/phase-four-bc/main",
            payload={
                "integration_batch_id": batch_id,
                "batch_sha": "f" * 40,
                "candidate_sha": node.candidate_sha,
                "member_node_keys": member_node_keys,
                "branch": "main",
            },
        )
        return replace(
            node,
            integration_batch_id=batch_id,
            integration_batch_sha="f" * 40,
            integration_source_ref="git://local/phase-four-bc/main",
            integration_evidence=evidence,
        )

    readback = AuthoritativeRepositoryReadback.from_durable(
        durable,
        compiled.repository,
        nodes=(with_batch(first), with_batch(second)),
    )
    destination = tmp_path / "batch-reconstructed.sqlite3"

    result = StoreReconstructor().reconstruct(readback, destination)

    assert result.status == "reconstructed", result.blockers
    with sqlite3.connect(destination) as connection:
        batch = json.loads(
            connection.execute(
                """
                SELECT state_json FROM v8_integration_batches
                WHERE repository = ? AND plan_digest = ? AND batch_id = ?
                """,
                (compiled.repository, compiled.digest, batch_id),
            ).fetchone()[0]
        )
        states = [
            json.loads(row[0])
            for row in connection.execute(
                """
                SELECT state_json FROM v8_node_execution_state
                WHERE repository = ? AND plan_digest = ?
                ORDER BY node_key
                """,
                (compiled.repository, compiled.digest),
            )
        ]
    assert batch["state"] == "integrated"
    assert batch["batch_sha"] == "f" * 40
    assert batch["member_node_keys"] == sorted(member_node_keys)
    assert {state["integration_batch_id"] for state in states} == {batch_id}
    assert {state["integrated_sha"] for state in states} == {"f" * 40}


def _completed_review_retirements(node):
    from gwo_v8.retirement import (
        authorize_review_after_evidence,
        completed_review_retirement,
        review_retirement_readback,
    )

    review_evidence = next(
        item for item in node.evidence if item.kind == "review"
    )
    records = {}
    for child in node.review_children:
        authorization = authorize_review_after_evidence(
            worker_binding=node.runtime_binding,
            review_binding=child.binding,
            review_evidence=review_evidence,
        )
        readback = review_retirement_readback(
            authorization=authorization,
            workspace_disposition="shared_preserved",
            agent_archived=True,
            directory_absent=False,
            worktree_absent=False,
            branch_deleted=False,
        )
        records[f"{child.binding.axis}:{child.recovery_ordinal}"] = (
            completed_review_retirement(authorization, readback)
        )
    return records


def test_reconstruction_does_not_trust_review_children_retired_without_records(
    tmp_path,
):
    compiled, durable = _durable_readback(
        tmp_path,
        count=1,
        reviewed=frozenset({1}),
    )
    node = replace(
        _node(compiled, 1),
        review_children_retired=True,
        review_retirements={},
    )

    result = StoreReconstructor().reconstruct(
        AuthoritativeRepositoryReadback.from_durable(
            durable,
            compiled.repository,
            nodes=(node,),
        ),
        tmp_path / "forged-review-retired-bool.sqlite3",
    )

    assert "REVIEW_RETIREMENT_READBACK_CONTRADICTION" in result.blockers


@pytest.mark.parametrize(
    "forgery",
    (
        "absolute_path",
        "identity_mismatch",
        "complete_without_readback",
    ),
)
def test_reconstruction_rejects_forged_review_retirement_records(
    tmp_path,
    forgery,
):
    compiled, durable = _durable_readback(
        tmp_path,
        count=1,
        reviewed=frozenset({1}),
    )
    original = _node(compiled, 1)
    records = _completed_review_retirements(original)
    axis_key = sorted(records)[0]
    forged = json.loads(json.dumps(records))
    authorization = forged[axis_key]["authorization"]
    if forgery == "absolute_path":
        authorization["workspace"] = str(ROOT.resolve())
    elif forgery == "identity_mismatch":
        authorization["agent_id"] = "review-agent:forged"
        identity = {
            key: value
            for key, value in authorization.items()
            if key != "authorization_digest"
        }
        authorization["authorization_digest"] = digest_value(identity)
    else:
        forged[axis_key]["evidence"] = None
    node = replace(
        original,
        review_children_retired=True,
        review_retirements=forged,
    )

    result = StoreReconstructor().reconstruct(
        AuthoritativeRepositoryReadback.from_durable(
            durable,
            compiled.repository,
            nodes=(node,),
        ),
        tmp_path / f"forged-review-retirement-{forgery}.sqlite3",
    )

    assert "REVIEW_RETIREMENT_READBACK_CONTRADICTION" in result.blockers


def test_reconstruction_derives_review_children_retired_from_typed_records(
    tmp_path,
):
    compiled, durable = _durable_readback(
        tmp_path,
        count=1,
        reviewed=frozenset({1}),
    )
    original = _node(compiled, 1)
    node = replace(
        original,
        review_children_retired=False,
        review_retirements=_completed_review_retirements(original),
    )
    destination = tmp_path / "review-retirement-roundtrip.sqlite3"

    result = StoreReconstructor().reconstruct(
        AuthoritativeRepositoryReadback.from_durable(
            durable,
            compiled.repository,
            nodes=(node,),
        ),
        destination,
    )

    assert result.status == "reconstructed", result.blockers
    with sqlite3.connect(destination) as connection:
        state = json.loads(
            connection.execute(
                "SELECT state_json FROM v8_node_execution_state"
            ).fetchone()[0]
        )
    assert state["review_children_retired"] is True


def test_reconstruction_preserves_publication_hosted_and_resume_progress(tmp_path):
    compiled = _compiled(1, hosted=True)
    durable = InMemoryDurablePlanControl()
    LocalPlanPublication(
        tmp_path / "progress-source.sqlite3",
        durable=durable,
    ).publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="v8-canary",
    )
    plan = json.loads(compiled.canonical_bytes)
    work = next(node for node in plan["nodes"] if node["kind"] == "work")
    hosted = next(
        check
        for check in work["output_contract"]["checks"]
        if check.get("hosted_only") is True
    )
    node = _node(
        compiled,
        1,
        wait_condition="integration_refresh",
    )
    hosted_evidence = _capture(
        kind="check",
        subject=str(node.candidate_sha),
        observer_type="github",
        observer_id="github-actions",
        source_ref=f"github://checks/{node.candidate_sha}",
        payload={
            "check_id": hosted["check_id"],
            "definition_digest": hosted["definition_digest"],
            "hosted_name": hosted["hosted_name"],
            "candidate_sha": node.candidate_sha,
            "outcome": "passed",
        },
    )
    progressed = replace(
        node,
        directive="reconcile_again",
        attempt_state="integration_wait",
        publication_state="published",
        publication_ref=f"github://publication/{node.candidate_sha}",
        hosted_check_state="passed",
        hosted_check_evidence=(hosted_evidence,),
        worker_parked_for_ci=True,
        resume_sent=True,
    )
    readback = AuthoritativeRepositoryReadback.from_durable(
        durable,
        compiled.repository,
        nodes=(progressed,),
    )
    destination = tmp_path / "progress.sqlite3"

    result = StoreReconstructor().reconstruct(readback, destination)

    assert result.status == "reconstructed", result.blockers
    with sqlite3.connect(destination) as connection:
        state = json.loads(
            connection.execute(
                "SELECT state_json FROM v8_node_execution_state"
            ).fetchone()[0]
        )
        evidence_record = json.loads(
            connection.execute(
                "SELECT evidence_json FROM v8_verified_results"
            ).fetchone()[0]
        )
    assert state["publication_state"] == "published"
    assert state["hosted_check_state"] == "passed"
    assert state["resume_sent"] is True
    assert state["worker_parked_for_ci"] is True
    assert evidence_record["hosted_check_evidence"] == [
        asdict(hosted_evidence)
    ]


def test_reconstruction_separates_execution_and_native_attempt_states(tmp_path):
    compiled, durable = _durable_readback(tmp_path, count=2)
    superseded = replace(
        _node(compiled, 1, status="superseded", wait_condition=None),
        directive="invoke_coordinator",
        attempt_state="superseded",
        attempt_record_state="terminal",
        attempt_terminal_reason="superseded",
        publication_state=None,
        publication_ref=None,
        hosted_check_state=None,
        worker_parked_for_ci=False,
    )
    runtime_unavailable = replace(
        _node(compiled, 2, status="blocked", wait_condition="runtime_available"),
        directive="wait_for_runtime",
        attempt_state="runtime_unavailable",
        attempt_record_state="running",
        attempt_terminal_reason="runtime_lost",
        publication_state=None,
        publication_ref=None,
        hosted_check_state=None,
        worker_parked_for_ci=False,
    )
    destination = tmp_path / "attempt-record-states.sqlite3"

    result = StoreReconstructor().reconstruct(
        AuthoritativeRepositoryReadback.from_durable(
            durable,
            compiled.repository,
            nodes=(superseded, runtime_unavailable),
        ),
        destination,
    )

    assert result.status == "reconstructed", result.blockers
    with sqlite3.connect(destination) as connection:
        native = dict(
            connection.execute(
                "SELECT attempt_id, state FROM v8_attempts"
            ).fetchall()
        )
        execution = {
            value["attempt_id"]: (
                value["attempt_state"],
                value["attempt_terminal_reason"],
            )
            for (raw,) in connection.execute(
                "SELECT state_json FROM v8_node_execution_state"
            ).fetchall()
            for value in (json.loads(raw),)
        }
    assert native == {"attempt:1": "terminal", "attempt:2": "running"}
    assert execution == {
        "attempt:1": ("superseded", "superseded"),
        "attempt:2": ("runtime_unavailable", "runtime_lost"),
    }


def test_review_recovery_child_keeps_its_action_and_provider_identity(tmp_path):
    compiled, durable = _durable_readback(
        tmp_path,
        count=1,
        reviewed=frozenset({1}),
    )
    node = _node(compiled, 1)
    primary = node.review_children[0].binding
    recovered_binding = replace(
        primary,
        action_key="review:standards:recovery",
        fixed_input_digest="f" * 64,
        runtime_id="review-runtime:standards:recovery",
        agent_id="review-agent:standards:recovery",
        session_id="review-session:standards:recovery",
    )
    recovered_observation = replace(
        node.review_observations[0],
        recovery_ordinal=1,
        action_key=recovered_binding.action_key,
        fixed_input_digest=recovered_binding.fixed_input_digest,
        runtime_id=recovered_binding.runtime_id,
        agent_id=recovered_binding.agent_id,
        session_id=recovered_binding.session_id,
        output_digest="9" * 64,
    )
    old_review = node.evidence[-1]
    review = _capture(
        kind="review",
        subject=node.candidate_sha,
        observer_type=old_review.observer_type,
        observer_id=old_review.observer_id,
        source_ref=old_review.source_ref,
        payload={
            **old_review.payload,
            "axes": [
                _axis_payload(recovered_observation),
                _axis_payload(node.review_observations[1]),
            ],
        },
    )
    recovered = replace(
        node,
        evidence=(*node.evidence[:-1], review),
        review_children=(
            *node.review_children,
            ReviewChildReadback(
                recovery_ordinal=1,
                binding=recovered_binding,
                observed_prompt_digest=recovered_binding.prompt_digest,
            ),
        ),
        review_observations=(
            *node.review_observations,
            recovered_observation,
        ),
    )
    readback = AuthoritativeRepositoryReadback.from_durable(
        durable,
        compiled.repository,
        nodes=(recovered,),
    )
    destination = tmp_path / "review-recovery.sqlite3"

    result = StoreReconstructor().reconstruct(readback, destination)

    assert result.status == "reconstructed"
    with sqlite3.connect(destination) as connection:
        state = json.loads(
            connection.execute(
                "SELECT state_json FROM v8_node_execution_state"
            ).fetchone()[0]
        )
    assert set(state["review_bindings"]) == {
        "standards:0",
        "standards:1",
        "spec:0",
    }
    assert (
        state["review_bindings"]["standards:1"]["action_key"]
        == recovered_binding.action_key
    )


def test_reconstruction_preserves_pending_retirement_authorization(tmp_path):
    from gwo_v8._canonical import digest_value
    from gwo_v8.retirement import RetirementAuthorization, pending_retirement

    compiled, durable = _durable_readback(tmp_path, count=1)
    node = _node(compiled, 1)
    binding = node.runtime_binding
    assert binding is not None
    identity = {
        "repository": compiled.repository,
        "plan_digest": compiled.digest,
        "node_key": node.node_key,
        "admission_id": node.admission_id,
        "attempt_id": node.attempt_id,
        "agent_id": binding.agent_id,
        "workspace_id": binding.workspace_id,
        "candidate_sha": node.candidate_sha,
        "integrated_sha": node.candidate_sha,
        "target_branch": "main",
        "temporary_branch": "gwo/reconstructed",
    }
    authorization = RetirementAuthorization(
        **identity,
        authorization_digest=digest_value(identity),
    )
    retirement = pending_retirement(authorization)
    pending = replace(
        node,
        status="waiting",
        directive="reconcile_again",
        attempt_state="retirement_pending",
        wait_condition="runtime_retirement",
        wait_source_ref="paseo://retirement/pending",
        integrated_sha=node.candidate_sha,
        integration_source_ref=f"git://main/{node.candidate_sha}",
        retirement=retirement,
        retirement_state="pending",
        last_retirement_error=None,
    )
    readback = AuthoritativeRepositoryReadback.from_durable(
        durable,
        compiled.repository,
        nodes=(pending,),
    )
    destination = tmp_path / "retirement-reconstruction.sqlite3"

    result = StoreReconstructor().reconstruct(readback, destination)

    assert result.status == "reconstructed"
    with sqlite3.connect(destination) as connection:
        state = json.loads(
            connection.execute(
                "SELECT state_json FROM v8_node_execution_state"
            ).fetchone()[0]
        )
    assert state["retirement"] == retirement
    assert state["retirement_state"] == "pending"
    assert state["attempt_state"] == "retirement_pending"

    outcome = _kernel(destination, durable, tmp_path).reconcile_once(
        compiled.repository
    )

    assert outcome.retirement_state == "error"
    assert outcome.last_retirement_error == {
        "code": "RUNTIME_IDENTITY_MISMATCH",
        "failure_class": "ambiguous",
    }
    with sqlite3.connect(destination) as connection:
        retried = json.loads(
            connection.execute(
                "SELECT state_json FROM v8_node_execution_state"
            ).fetchone()[0]
        )
    assert (
        retried["retirement"]["authorization"]
        == retirement["authorization"]
    )


def test_partial_review_and_consumed_budgets_resume_without_reset(tmp_path):
    compiled, durable = _durable_readback(
        tmp_path,
        count=1,
        reviewed=frozenset({1}),
    )
    node = _node(compiled, 1)
    partial = replace(
        node,
        publication_eligible=False,
        evidence=node.evidence[:-1],
        review_observations=node.review_observations[:1],
        status="waiting",
        directive="wait_for_review",
        wait_condition="review_axis",
        wait_source_ref="github://review/pending",
        publication_state=None,
        publication_ref=None,
        hosted_check_state=None,
        worker_parked_for_ci=False,
        budgets=ExecutionBudgetReadback(
            attempt_ordinal=2,
            repair_rounds_used=1,
            materialization_create_executions=2,
            materialization_prompt_executions=1,
            hosted_retry_count=1,
            runtime_observation_failures=2,
            runtime_circuits={
                "paseo:observe:attempt:1": {
                    "state": "open",
                    "failures": 2,
                }
            },
        ),
    )
    readback = AuthoritativeRepositoryReadback.from_durable(
        durable,
        compiled.repository,
        nodes=(partial,),
    )
    destination = tmp_path / "partial-review.sqlite3"

    result = StoreReconstructor().reconstruct(readback, destination)

    assert result.status == "reconstructed"
    with sqlite3.connect(destination) as connection:
        state = json.loads(
            connection.execute(
                "SELECT state_json FROM v8_node_execution_state"
            ).fetchone()[0]
        )
    assert state["attempt_ordinal"] == 2
    assert state["repair_rounds_used"] == 1
    assert state["materialization_actions"] == {"create": 2, "prompt": 1}
    assert state["hosted_retry_count"] == 1
    assert state["runtime_observation_failures"] == 2
    assert state["wait_condition"] == "review_axis"


def test_reconstruction_fails_closed_before_activation(tmp_path):
    compiled, durable = _durable_readback(tmp_path, count=1)
    invalid = replace(_node(compiled, 1), admission_id=None)
    readback = AuthoritativeRepositoryReadback.from_durable(
        durable,
        compiled.repository,
        nodes=(invalid,),
        legacy_identities=("v6-dispatch:42",),
    )
    destination = tmp_path / "blocked.sqlite3"

    result = StoreReconstructor().reconstruct(readback, destination)

    assert result.status == "blocked"
    assert {"ATTEMPT_ADMISSION_MISSING", "LEGACY_IDENTITY_PRESENT"}.issubset(
        result.blockers
    )
    assert LocalPlanPublication(
        destination,
        durable=durable,
    ).read_active(compiled.repository) is None


def test_reconstruction_rejects_stale_publication_eligibility(tmp_path):
    compiled, durable = _durable_readback(tmp_path, count=1)
    node = _node(compiled, 1)
    failed_check = replace(
        node.evidence[1],
        payload={**node.evidence[1].payload, "outcome": "failed"},
    )
    invalid = replace(node, evidence=(node.evidence[0], failed_check))
    readback = AuthoritativeRepositoryReadback.from_durable(
        durable,
        compiled.repository,
        nodes=(invalid,),
    )

    result = StoreReconstructor().reconstruct(
        readback,
        tmp_path / "invalid-evidence.sqlite3",
    )

    assert result.status == "blocked"
    assert "EVIDENCE_OR_ELIGIBILITY_INVALID" in result.blockers


def test_reconstruction_does_not_infer_completion_from_status_alone(tmp_path):
    compiled, durable = _durable_readback(tmp_path, count=1)
    incomplete = _node(
        compiled,
        1,
        status="complete",
        wait_condition=None,
        integrated_sha=None,
    )
    readback = AuthoritativeRepositoryReadback.from_durable(
        durable,
        compiled.repository,
        nodes=(incomplete,),
    )

    result = StoreReconstructor().reconstruct(
        readback,
        tmp_path / "incomplete.sqlite3",
    )

    assert result.status == "blocked"
    assert "COMPLETION_FACTS_MISSING" in result.blockers


def test_missing_durable_activation_becomes_typed_blocked_readback(tmp_path):
    result = StoreReconstructor().reconstruct_from_durable(
        InMemoryDurablePlanControl(),
        "local/missing",
        tmp_path / "missing.sqlite3",
    )

    assert result.status == "blocked"
    assert result.blockers == ("DURABLE_ACTIVATION_MISSING",)


@pytest.mark.parametrize(
    ("nodes", "expected_action"),
    [
        ((), "would_admit"),
        ((1, "hosted_ci"), "would_wait_for_hosted_ci"),
        ((1, "integration_refresh"), "would_request_integration_refresh"),
        ((1, None, "complete"), "idle"),
    ],
)
def test_shadow_uses_live_kernel_planner_without_mutating_store(
    tmp_path,
    nodes,
    expected_action,
):
    compiled, durable = _durable_readback(tmp_path, count=1)
    readback_nodes = (
        ()
        if not nodes
        else (
            _node(
                compiled,
                nodes[0],
                status=nodes[2] if len(nodes) > 2 else "waiting",
                wait_condition=nodes[1],
                integrated_sha=(
                    f"{nodes[0]:040x}"
                    if len(nodes) > 2 and nodes[2] == "complete"
                    else None
                ),
            ),
        )
    )
    readback = AuthoritativeRepositoryReadback.from_durable(
        durable,
        compiled.repository,
        nodes=readback_nodes,
    )
    destination = tmp_path / f"shadow-{expected_action}.sqlite3"
    StoreReconstructor().reconstruct(readback, destination)
    before = destination.read_bytes()

    first = ShadowEvaluator(
        _kernel(destination, durable, tmp_path),
    ).evaluate_store(compiled.repository)
    second = ShadowEvaluator(
        _kernel(destination, durable, tmp_path),
    ).evaluate_store(compiled.repository)

    assert first == second
    assert first.proposed_actions[0].kind == expected_action
    assert json.loads(first.audit_record)["actions"][0]["kind"] == expected_action
    assert destination.read_bytes() == before


def _accepted_canary() -> CanaryRunReadback:
    repository = "local/gwo-v8-canary"
    nodes = ("node:1", "node:2", "node:3")
    scenario_evidence = {
        scenario: _capture(
            kind="canary",
            subject=repository,
            observer_type="github",
            observer_id="github-actions",
            source_ref=f"github://canary/evidence/{scenario}",
            payload={"scenario": scenario, "outcome": "passed"},
        )
        for scenario in {
            "contract_activation",
            "prompt_acceptance",
            "local_first_publication",
            "dual_axis_review",
            "hosted_code_failure",
            "hosted_infrastructure_failure",
            "recovery",
            "ci_parking",
            "capacity_refill",
            "parallel_8_1",
            "conflict_exclusion",
            "serial_integration",
            "rollback",
        }
    }
    candidates = {
        node: _capture(
            kind="candidate",
            subject=f"{index:040x}",
            observer_type="github",
            observer_id="github-actions",
            source_ref=f"github://canary/candidate/{index}",
            payload={"node_key": node, "tree_sha": f"{index:040x}"},
        )
        for index, node in enumerate(nodes, 1)
    }
    reviews = {
        node: _capture(
            kind="review",
            subject=candidates[node].subject,
            observer_type="kernel",
            observer_id="v8-canary",
            source_ref=f"github://canary/review/{index}",
            payload={
                "record_type": "envelope",
                "attempt_id": f"attempt:{index}",
                "candidate_sha": candidates[node].subject,
                "acceptance_digest": "a" * 64,
                "check_manifest_digest": "b" * 64,
                "axes": [
                    {
                        "axis": axis,
                        "action_key": f"review:{axis}:{index}",
                        "fixed_input_digest": "c" * 64,
                        "recovery_ordinal": 0,
                        "runtime": {
                            "runtime_id": f"runtime:{axis}:{index}",
                            "agent_id": f"agent:{axis}:{index}",
                            "session_id": f"session:{axis}:{index}",
                            "profile_digest": "d" * 64,
                            "provider": "codex",
                            "model": "gpt-5.6-sol",
                            "thinking": "high",
                            "mode": "default",
                        },
                        "output_digest": "e" * 64,
                        "findings": [],
                    }
                    for axis in ("standards", "spec")
                ],
            },
        )
        for index, node in enumerate(nodes, 1)
    }
    return CanaryRunReadback(
        repository=repository,
        node_keys=nodes,
        hosted_ci_seconds=120,
        coverage=frozenset(scenario_evidence),
        scenario_evidence=scenario_evidence,
        candidate_evidence=candidates,
        review_evidence=reviews,
        managed_reviewer_identities=(),
    )


def _verify_canary(readback: CanaryRunReadback):
    evidence = (
        *readback.scenario_evidence.values(),
        *readback.candidate_evidence.values(),
        *readback.review_evidence.values(),
    )
    return CanaryRunVerifier(
        InMemoryCanaryEvidenceControl(tuple(evidence)),
    ).verify(readback)


def test_canary_accepts_only_typed_durable_evidence():
    acceptance = _verify_canary(_accepted_canary())

    assert acceptance.accepted is True
    assert acceptance.evidence_package_digest
    assert acceptance.manifest_ref
    assert len(acceptance.evidence_refs) > 13

    readback = _accepted_canary()
    scenario = next(iter(readback.scenario_evidence))
    invalid = replace(
        readback,
        scenario_evidence={
            **readback.scenario_evidence,
            scenario: replace(
                readback.scenario_evidence[scenario],
                source_ref="memory://not-durable",
            ),
        },
    )
    rejected = _verify_canary(invalid)
    assert rejected.accepted is False
    assert "CANARY_SCENARIO_EVIDENCE_INVALID" in rejected.blockers


def test_github_canary_manifest_readback_survives_adapter_restart():
    readback = _accepted_canary()
    client = _GitHubContentClient()
    locations = {}
    evidence = (
        *readback.scenario_evidence.values(),
        *readback.candidate_evidence.values(),
        *readback.review_evidence.values(),
    )
    for index, item in enumerate(evidence):
        location = (
            readback.repository,
            "gwo-control",
            f".gwo-v8/evidence/{index}.json",
        )
        locations[item.source_ref] = location
        client.contents[location] = GitHubContent(
            content=canonical_bytes(asdict(item)),
            blob_sha=f"evidence:{index}",
        )
    first = GitHubCanaryEvidenceControl(
        client,
        locations,
        manifest_repository=readback.repository,
        manifest_branch="gwo-control",
    )

    acceptance = CanaryRunVerifier(first).verify(readback)
    recovered = GitHubCanaryEvidenceControl(
        client,
        locations,
        manifest_repository=readback.repository,
        manifest_branch="gwo-control",
    )

    assert acceptance.accepted is True
    assert acceptance.manifest_ref is not None
    assert recovered.read_manifest(acceptance.manifest_ref) is not None


def _cutover_controller(tmp_path: Path, *, github=False, legacy=None):
    compiled = _compiled()
    durable = InMemoryDurablePlanControl()
    if github:
        client = _GitHubContentClient()
        transitions = GitHubWriterTransitionControl(
            client,
            branch="gwo-control",
            initial_writer="v6.1",
        )
    else:
        client = None
        transitions = InMemoryWriterTransitionControl(initial_writer="v6.1")
    publication = LocalPlanPublication(
        tmp_path / "v8.sqlite3",
        durable=durable,
        writer_authority=transitions,
    )
    controller = WriterCutoverController(
        legacy=legacy or InMemoryLegacyWriterControl(),
        transitions=transitions,
        publication=publication,
    )
    return compiled, durable, transitions, publication, controller, client


def test_cutover_fences_v61_then_authorizes_exact_activation_and_capacity(tmp_path):
    compiled, _durable, transitions, publication, controller, _client = (
        _cutover_controller(tmp_path)
    )
    acceptance = _verify_canary(_accepted_canary())

    outcome = controller.cutover(
        compiled,
        canary=acceptance,
        writer_generation="v8-generation-1",
        worker_capacity=8,
        coordinator_capacity=1,
    )

    active = publication.read_active(compiled.repository)
    assert outcome.status == "cut_over"
    assert active is not None and active.activation_id == outcome.activation_id
    assert transitions.allows(
        compiled.repository,
        "v8-generation-1",
        outcome.activation_id,
    )
    assert transitions.capacity_limits(
        compiled.repository,
        "v8-generation-1",
        outcome.activation_id,
    ) == (8, 1)
    record = transitions.history(compiled.repository)[0]
    assert record.canary_evidence_refs == acceptance.evidence_refs
    repeated = controller.cutover(
        compiled,
        canary=acceptance,
        writer_generation="v8-generation-1",
        worker_capacity=8,
        coordinator_capacity=1,
    )
    assert repeated == outcome
    assert len(transitions.history(compiled.repository)) == 2


def test_cutover_rolls_forward_from_durable_pending_after_final_cas_failure(
    tmp_path,
):
    compiled = _compiled()
    durable = InMemoryDurablePlanControl()
    transitions = _FailFinalTransitionOnce()
    publication = LocalPlanPublication(
        tmp_path / "v8.sqlite3",
        durable=durable,
        writer_authority=transitions,
    )
    controller = WriterCutoverController(
        legacy=InMemoryLegacyWriterControl(),
        transitions=transitions,
        publication=publication,
    )
    acceptance = _verify_canary(_accepted_canary())

    with pytest.raises(RuntimeError, match="simulated final transition"):
        controller.cutover(
            compiled,
            canary=acceptance,
            writer_generation="v8-generation-1",
            worker_capacity=8,
            coordinator_capacity=1,
        )

    pending = transitions.read_current(compiled.repository)
    pending_record = transitions.read(compiled.repository, pending.record_id)
    assert pending_record.status == "pending"
    assert pending_record.plan_digest == compiled.digest
    receipt = durable.read_current_activation(compiled.repository)
    assert receipt is not None
    with pytest.raises(ActivationError):
        publication.read_active(compiled.repository)

    changed = controller.cutover(
        _compiled(count=4),
        canary=acceptance,
        writer_generation="v8-generation-1",
        worker_capacity=8,
        coordinator_capacity=1,
    )
    assert changed.status == "blocked"
    assert changed.blockers == ("CUTOVER_SOURCE_WRITER_INVALID",)
    assert durable.read_current_activation(compiled.repository) == receipt

    recovered = controller.cutover(
        compiled,
        canary=acceptance,
        writer_generation="v8-generation-1",
        worker_capacity=8,
        coordinator_capacity=1,
    )
    assert recovered.status == "cut_over"
    assert recovered.activation_id == receipt.activation_id


def test_failed_cutover_is_durable_without_activation_or_v61_precondition_stop(
    tmp_path,
):
    compiled, _durable, transitions, publication, controller, _client = (
        _cutover_controller(tmp_path)
    )
    rejected = replace(
        _verify_canary(_accepted_canary()),
        accepted=False,
        evidence_package_digest=None,
        blockers=("forced",),
    )

    outcome = controller.cutover(
        compiled,
        canary=rejected,
        writer_generation="v8-generation-1",
        worker_capacity=7,
        coordinator_capacity=1,
    )

    assert outcome.status == "blocked"
    assert publication.read_active(compiled.repository) is None
    assert transitions.read_current(compiled.repository).writer_generation == "v6.1"
    assert transitions.history(compiled.repository)[-1].status == "blocked"
    assert controller.legacy.readback(compiled.repository).stopped is False


def test_rollback_discards_pre_activation_pending_reservation(tmp_path):
    compiled = _compiled()
    durable = InMemoryDurablePlanControl()
    transitions = InMemoryWriterTransitionControl(initial_writer="v6.1")

    def fail_after_reservation(checkpoint: str) -> None:
        if checkpoint == "pending_reserved":
            raise RuntimeError("simulated pre-publication crash")

    publication = LocalPlanPublication(
        tmp_path / "pending-rollback.sqlite3",
        durable=durable,
        writer_authority=transitions,
        checkpoint=fail_after_reservation,
    )
    controller = WriterCutoverController(
        legacy=InMemoryLegacyWriterControl(),
        transitions=transitions,
        publication=publication,
    )
    with pytest.raises(RuntimeError, match="pre-publication"):
        controller.cutover(
            compiled,
            canary=_verify_canary(_accepted_canary()),
            writer_generation="v8-generation-1",
            worker_capacity=8,
            coordinator_capacity=1,
        )

    outcome = controller.rollback(
        repository=compiled.repository,
        ownership=InMemoryV8OwnershipControl(
            V8OwnershipReadback(
                active_admissions=(),
                active_attempts=(),
                integration_lease=False,
                runtime_resources=(),
            )
        ),
        restore_writer_generation="v6.1",
        reason="abort pending cutover",
    )

    assert outcome.status == "rolled_back"
    assert publication.read_active(compiled.repository) is None
    assert controller.legacy.readback(compiled.repository).stopped is False


def test_rollback_rolls_forward_receipt_backed_pending_before_compensation(
    tmp_path,
):
    compiled = _compiled()
    durable = InMemoryDurablePlanControl()
    transitions = InMemoryWriterTransitionControl(initial_writer="v6.1")

    def fail_before_local_finalize(checkpoint: str) -> None:
        if checkpoint == "receipt_read_back":
            raise RuntimeError("simulated post-receipt crash")

    publication = LocalPlanPublication(
        tmp_path / "receipt-rollback.sqlite3",
        durable=durable,
        writer_authority=transitions,
        checkpoint=fail_before_local_finalize,
    )
    controller = WriterCutoverController(
        legacy=InMemoryLegacyWriterControl(),
        transitions=transitions,
        publication=publication,
    )
    with pytest.raises(RuntimeError, match="post-receipt"):
        controller.cutover(
            compiled,
            canary=_verify_canary(_accepted_canary()),
            writer_generation="v8-generation-1",
            worker_capacity=8,
            coordinator_capacity=1,
        )
    assert durable.read_current_activation(compiled.repository) is not None
    with pytest.raises(ActivationError) as pending:
        publication.read_active(compiled.repository)
    assert pending.value.code == "ACTIVATION_PENDING"

    outcome = controller.rollback(
        repository=compiled.repository,
        ownership=InMemoryV8OwnershipControl(
            V8OwnershipReadback(
                active_admissions=(),
                active_attempts=(),
                integration_lease=False,
                runtime_resources=(),
            )
        ),
        restore_writer_generation="v6.1",
        reason="compensate receipt-backed pending",
    )

    assert outcome.status == "rolled_back"
    with pytest.raises(ActivationError) as fenced:
        publication.read_active(compiled.repository)
    assert fenced.value.code == "WRITER_GENERATION_FENCED"
    assert [item.kind for item in transitions.history(compiled.repository)] == [
        "cutover_pending",
        "drain",
        "rollback",
    ]


def test_rollback_fences_v8_restores_v61_and_is_idempotent(tmp_path):
    compiled, durable, transitions, publication, controller, _client = (
        _cutover_controller(tmp_path)
    )
    cutover = controller.cutover(
        compiled,
        canary=_verify_canary(_accepted_canary()),
        writer_generation="v8-generation-1",
        worker_capacity=8,
        coordinator_capacity=1,
    )

    rollback = controller.rollback(
        repository=compiled.repository,
        ownership=InMemoryV8OwnershipControl(
            V8OwnershipReadback(
                active_admissions=(),
                active_attempts=(),
                integration_lease=False,
                runtime_resources=(),
            )
        ),
        restore_writer_generation="v6.1",
        reason="canary rollback exercise",
    )
    repeated = controller.rollback(
        repository=compiled.repository,
        ownership=InMemoryV8OwnershipControl(
            V8OwnershipReadback(
                active_admissions=(),
                active_attempts=(),
                integration_lease=False,
                runtime_resources=(),
            )
        ),
        restore_writer_generation="v6.1",
        reason="canary rollback exercise",
    )

    assert repeated == rollback
    assert rollback.status == "rolled_back"
    assert controller.legacy.readback(compiled.repository).stopped is False
    with pytest.raises(ActivationError) as fenced:
        publication.read_active(compiled.repository)
    assert fenced.value.code == "WRITER_GENERATION_FENCED"
    assert durable.read_activation(
        compiled.repository,
        cutover.activation_id,
    ) is not None
    assert [record.kind for record in transitions.history(compiled.repository)] == [
        "cutover_pending",
        "cutover",
        "drain",
        "rollback",
    ]
    assert rollback.imported_legacy_identity_count == 0


def test_rollback_drains_and_rereads_ownership_before_restoring_v61(tmp_path):
    compiled, _durable, transitions, publication, controller, _client = (
        _cutover_controller(tmp_path)
    )
    controller.cutover(
        compiled,
        canary=_verify_canary(_accepted_canary()),
        writer_generation="v8-generation-1",
        worker_capacity=8,
        coordinator_capacity=1,
    )
    ownership = InMemoryV8OwnershipControl(
        V8OwnershipReadback(
            active_admissions=("admission:in-flight",),
            active_attempts=(),
            integration_lease=False,
            runtime_resources=(),
        ),
        auto_drain=False,
    )

    waiting = controller.rollback(
        repository=compiled.repository,
        ownership=ownership,
        restore_writer_generation="v6.1",
        reason="drain before rollback",
    )

    assert waiting.status == "blocked"
    assert waiting.blockers == ("V8_DRAIN_PENDING",)
    assert transitions.read_current(compiled.repository).writer_generation == (
        "v8-generation-1"
    )
    assert controller.legacy.readback(compiled.repository).stopped is True
    active = publication.read_active(compiled.repository)
    assert active is not None
    with pytest.raises(ActivationError) as fenced:
        publication.assert_new_work(
            compiled.repository,
            writer_generation=active.writer_generation,
            activation_id=active.activation_id,
        )
    assert fenced.value.code == "WRITER_NEW_WORK_FENCED"

    ownership.current = V8OwnershipReadback(
        active_admissions=(),
        active_attempts=(),
        integration_lease=False,
        runtime_resources=(),
    )
    completed = controller.rollback(
        repository=compiled.repository,
        ownership=ownership,
        restore_writer_generation="v6.1",
        reason="drain before rollback",
    )
    assert completed.status == "rolled_back"
    assert ownership.reads == 2
    assert controller.legacy.readback(compiled.repository).stopped is False


def test_store_drain_uses_kernel_supersession_and_preserves_inflight_lease(
    tmp_path,
):
    compiled, durable = _durable_readback(tmp_path, count=1)
    destination = tmp_path / "drain.sqlite3"
    StoreReconstructor().reconstruct(
        AuthoritativeRepositoryReadback.from_durable(
            durable,
            compiled.repository,
            nodes=(_node(compiled, 1),),
        ),
        destination,
    )
    with sqlite3.connect(destination) as connection:
        connection.execute(
            """
            INSERT INTO v8_integration_leases (repository, holder)
            VALUES (?, 'integration:in-flight')
            """,
            (compiled.repository,),
        )
    runtime_resources = []
    ownership = StoreV8OwnershipControl(
        destination,
        lambda _repository: tuple(runtime_resources),
        lambda _repository: runtime_resources.clear(),
    )

    ownership.drain(
        compiled.repository,
        source_ref="writer-transition://drain-record",
    )

    readback = ownership.readback(compiled.repository)
    assert readback.active_attempts == ()
    assert readback.integration_lease is True
    with sqlite3.connect(destination) as connection:
        state = json.loads(
            connection.execute(
                "SELECT state_json FROM v8_node_execution_state"
            ).fetchone()[0]
        )
        node_state = connection.execute(
            "SELECT state FROM v8_node_states"
        ).fetchone()[0]
        attempt_state = connection.execute(
            "SELECT state FROM v8_attempts"
        ).fetchone()[0]
    assert state["status"] == node_state == "superseded"
    assert state["attempt_state"] == "superseded"
    assert state["attempt_terminal_reason"] == "superseded"
    assert state["supersession_source_ref"] == (
        "writer-transition://drain-record"
    )
    assert attempt_state == "terminal"


def test_integration_lease_acquisition_rechecks_local_drain_fence(tmp_path):
    compiled, durable = _durable_readback(tmp_path, count=1)
    store = tmp_path / "lease-fence.sqlite3"
    publication = LocalPlanPublication(store, durable=durable)
    publication.reconstruct_active_from_readback(
        durable.read_plan(compiled.repository, compiled.digest),
        durable.read_current_activation(compiled.repository),
    )
    active = publication.read_active(compiled.repository)
    assert active is not None
    publication.begin_writer_drain(
        compiled.repository,
        writer_generation=active.writer_generation,
        activation_id=active.activation_id,
    )
    kernel = _kernel(store, durable, tmp_path)

    with pytest.raises(KernelError) as fenced:
        kernel._acquire_integration_lease(
            compiled.repository,
            "node:integration",
            activation_id=active.activation_id,
        )

    assert fenced.value.code == "WRITER_NEW_WORK_FENCED"
    with sqlite3.connect(store) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM v8_integration_leases"
        ).fetchone()[0] == 0


def test_github_transition_control_survives_process_restart(tmp_path):
    compiled, _durable, transitions, _publication, controller, client = (
        _cutover_controller(tmp_path, github=True)
    )
    cutover = controller.cutover(
        compiled,
        canary=_verify_canary(_accepted_canary()),
        writer_generation="v8-generation-1",
        worker_capacity=8,
        coordinator_capacity=1,
    )
    controller.rollback(
        repository=compiled.repository,
        ownership=InMemoryV8OwnershipControl(
            V8OwnershipReadback(
                active_admissions=(),
                active_attempts=(),
                integration_lease=False,
                runtime_resources=(),
            )
        ),
        restore_writer_generation="v6.1",
        reason="durable GitHub rollback exercise",
    )
    recovered = GitHubWriterTransitionControl(
        client,
        branch="gwo-control",
        initial_writer="v6.1",
    )

    assert cutover.status == "cut_over"
    assert recovered.read_current(compiled.repository).writer_generation == "v6.1"
    assert [record.kind for record in recovered.history(compiled.repository)] == [
        "cutover_pending",
        "cutover",
        "drain",
        "rollback",
    ]
