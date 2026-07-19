from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_sync_module():
    path = ROOT / "scripts" / "sync_skill_references.py"
    spec = importlib.util.spec_from_file_location("sync_skill_references", path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SYNC = load_sync_module()
SKILLS = tuple(SYNC.PACKAGES)


class SkillPackageTests(unittest.TestCase):
    def test_three_self_contained_skill_packages_exist(self) -> None:
        for name in SKILLS:
            skill = ROOT / "skills" / name
            with self.subTest(skill=name):
                self.assertTrue((skill / "SKILL.md").is_file())
                self.assertTrue((skill / ".skill-package.json").is_file())
                self.assertTrue((skill / "scripts" / "paseo_room.py").is_file())
                self.assertTrue(
                    (skill / "scripts" / "material_delivery.py").is_file()
                )

    def test_shared_references_and_room_runtime_are_synchronized(self) -> None:
        for name, filenames in SYNC.PACKAGES.items():
            shared = ROOT / "skills" / name / "references" / "shared"
            self.assertEqual(set(filenames), {path.name for path in shared.glob("*.md")})
            for filename in filenames:
                self.assertEqual(
                    (ROOT / "shared" / filename).read_bytes(),
                    (shared / filename).read_bytes(),
                )
            for runtime in SYNC.RUNTIME_SCRIPTS:
                canonical = ROOT / "skills/github-work-orchestrator/scripts" / runtime
                self.assertEqual(
                    canonical.read_bytes(),
                    (ROOT / "skills" / name / "scripts" / runtime).read_bytes(),
                )

    def test_sync_check_accepts_committed_packages(self) -> None:
        self.assertEqual([], SYNC.find_drift(ROOT))

    def test_quick_validation_accepts_committed_packages(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "quick_validate.py")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_package_manifests_pin_version_and_content_digest(self) -> None:
        self.assertEqual("4.3.1", SYNC.PACKAGE_VERSION)
        for name in SKILLS:
            package = ROOT / "skills" / name
            manifest = json.loads(
                (package / SYNC.PACKAGE_MANIFEST).read_text(encoding="utf-8")
            )
            with self.subTest(skill=name):
                self.assertEqual(SYNC.PACKAGE_VERSION, manifest["version"])
                self.assertEqual(name, manifest["skill"])
                self.assertEqual(
                    SYNC.package_digest(package), manifest["content_sha256"]
                )

    def test_installed_skill_digest_detects_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install_root = Path(temporary)
            for name in SKILLS:
                shutil.copytree(ROOT / "skills" / name, install_root / name)
            self.assertEqual([], SYNC.find_install_drift(ROOT, install_root))
            (install_root / SKILLS[0] / "SKILL.md").write_text(
                "drift", encoding="utf-8"
            )
            self.assertTrue(SYNC.find_install_drift(ROOT, install_root))

    def test_package_digest_ignores_platform_line_endings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary)
            source = package / "SKILL.md"
            source.write_bytes(b"---\nname: example\n---\n")
            lf_digest = SYNC.package_digest(package)
            source.write_bytes(b"---\r\nname: example\r\n---\r\n")
            self.assertEqual(lf_digest, SYNC.package_digest(package))

    def test_legacy_root_wrapper_loads_packaged_orchestrator(self) -> None:
        wrapper = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("skills/github-work-orchestrator/SKILL.md", wrapper)
        self.assertIn("provider-neutral Paseo", wrapper)

    def test_trigger_descriptions_are_role_specific(self) -> None:
        descriptions = {}
        for name in SKILLS:
            text = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
            frontmatter = text.split("---", 2)[1]
            descriptions[name] = next(
                line.removeprefix("description:").strip().strip('"').lower()
                for line in frontmatter.splitlines()
                if line.startswith("description:")
            )
        self.assertIn("dispatch", descriptions["github-work-orchestrator"])
        self.assertIn("reports", descriptions["github-issue-intake"])
        self.assertIn("one assigned github issue", descriptions["github-issue-worker"])

    def test_all_packaged_markdown_links_resolve_inside_each_skill(self) -> None:
        link_pattern = re.compile(r"\[[^\]]+\]\(([^)#]+)(?:#[^)]+)?\)")
        for name in SKILLS:
            skill = ROOT / "skills" / name
            for markdown in skill.rglob("*.md"):
                for target in link_pattern.findall(markdown.read_text(encoding="utf-8")):
                    if "://" in target:
                        continue
                    resolved = (markdown.parent / target).resolve()
                    with self.subTest(skill=name, file=markdown.name, link=target):
                        self.assertTrue(resolved.is_file())
                        self.assertTrue(resolved.is_relative_to(skill.resolve()))

    def test_room_is_primary_and_native_send_is_only_an_idle_wakeup(self) -> None:
        protocol = (ROOT / "shared/communication-protocol.md").read_text(
            encoding="utf-8"
        )
        normalized = " ".join(protocol.split()).lower()
        for phrase in (
            "room messages are the primary coordination surface",
            "message uuid",
            "not that the claimed author or evidence is true",
            "do not mention or send a prompt to a busy agent",
            "--identity-receipts",
            "paseo_agent_id",
            "chat author",
            "already accepted ask",
            "replay",
            "signal_id",
        ):
            self.assertIn(phrase, normalized)
        self.assertIn("not that the claimed author or evidence is true", normalized)

    def test_material_delivery_requires_wake_receipt_and_recipient_ack(self) -> None:
        protocol = (ROOT / "shared/communication-protocol.md").read_text(
            encoding="utf-8"
        )
        normalized = " ".join(protocol.split()).lower()
        for phrase in (
            "paseo_room.py post-material",
            "material_delivery.py delivery-plan",
            "delivery_wake",
            "delivery_ack",
            "wake-unacknowledged",
            "cannot poison the valid business event",
            "never prompt a running agent",
        ):
            self.assertIn(phrase, normalized)
        for skill in SKILLS:
            packaged = ROOT / "skills" / skill / "scripts" / "material_delivery.py"
            self.assertTrue(packaged.is_file())
        cleanup = (
            ROOT
            / "skills/github-work-orchestrator/references/cleanup-safety-policy.md"
        ).read_text(encoding="utf-8")
        self.assertIn("DELIVERY_WAKE", cleanup)
        self.assertIn("DELIVERY_ACK", cleanup)

    def test_provider_selection_is_role_based_and_fail_closed(self) -> None:
        orchestrator = (ROOT / "skills/github-work-orchestrator/SKILL.md").read_text(
            encoding="utf-8"
        )
        provider = (
            ROOT / "skills/github-work-orchestrator/scripts/provider_policy.py"
        ).read_text(encoding="utf-8")
        normalized = " ".join(orchestrator.split()).lower()
        self.assertIn("orchestration-preferences.json", normalized)
        for category in ("planning", "research", "impl", "audit", "ui"):
            self.assertIn(category, normalized)
            self.assertIn(category, provider)
        self.assertIn("fails closed", normalized)
        self.assertNotIn("ollama-cloud/glm", orchestrator.lower())
        self.assertIn("exactly one advertised `isunattended: true` mode", normalized)
        self.assertIn("never infer it from names or prose", normalized)

    def test_execution_contract_is_v3_provider_neutral(self) -> None:
        contract = (
            ROOT / "skills/github-work-orchestrator/scripts/validate_execution_contract.py"
        ).read_text(encoding="utf-8")
        for field in (
            "campaign_id",
            "dispatch_id",
            "agent_role",
            "role_category",
            "execution_mode",
            "done_when",
        ):
            self.assertIn(field, contract)
        self.assertIn("FORBIDDEN_RUNTIME_FIELDS", contract)
        for obsolete in ("model_binding", "task_id", "callback_task"):
            self.assertIn(f'"{obsolete}"', contract)

    def test_retired_provider_specific_runtime_paths_are_absent(self) -> None:
        retired = (
            "agents/openai.yaml",
            "shared/model-profiles.md",
            "skills/github-issue-intake/agents/openai.yaml",
            "skills/github-issue-intake/scripts/intake_signal.py",
            "skills/github-issue-worker/agents/openai.yaml",
            "skills/github-issue-worker/scripts/worker_signal.py",
            "skills/github-work-orchestrator/agents/openai.yaml",
            "skills/github-work-orchestrator/assets/worker.toml",
            "skills/github-work-orchestrator/scripts/install_worker_agent.py",
            "skills/github-work-orchestrator/scripts/task_creation_lease.py",
        )
        for relative in retired:
            with self.subTest(path=relative):
                self.assertFalse((ROOT / relative).exists())

    def test_dev_is_integration_and_main_is_release(self) -> None:
        orchestrator = (ROOT / "skills/github-work-orchestrator/SKILL.md").read_text(
            encoding="utf-8"
        )
        state = (ROOT / "shared/github-state-rules.md").read_text(encoding="utf-8")
        combined = " ".join((orchestrator + state).split()).lower()
        self.assertIn("work/issue-", combined)
        self.assertIn("target `dev`", combined)
        self.assertIn("release merge from `dev`", combined)

    def test_two_tier_orchestration_keeps_repository_ownership_singular(self) -> None:
        orchestrator = (ROOT / "skills/github-work-orchestrator/SKILL.md").read_text(
            encoding="utf-8"
        )
        state = (ROOT / "shared/github-state-rules.md").read_text(encoding="utf-8")
        lifecycle = (ROOT / "shared/lifecycle.md").read_text(encoding="utf-8")
        combined = " ".join((orchestrator + state + lifecycle).split()).lower()
        for phrase in (
            "keep one repository coordinator per repository",
            "coordinator home workspace",
            "integration control worktree",
            "direct `subagent` of the coordinator",
            "provider binding",
            "carries no feature commit",
            "`campaign_closed` never archives the coordinator",
            "unlabeled root agents are foreign",
        ):
            self.assertIn(phrase, combined)

    def test_concurrent_campaigns_use_hotsets_and_one_integration_lease(self) -> None:
        orchestrator = (ROOT / "skills/github-work-orchestrator/SKILL.md").read_text(
            encoding="utf-8"
        )
        state = (ROOT / "shared/github-state-rules.md").read_text(encoding="utf-8")
        combined = " ".join((orchestrator + state).split()).lower()
        for phrase in (
            "different campaigns execute concurrently",
            "hotsets do not overlap",
            "repository-scoped integration lease",
            "refresh an advanced `dev` base",
        ):
            self.assertIn(phrase, combined)

    def test_coordinator_loop_is_event_driven_and_plans_full_waves(self) -> None:
        skill = (ROOT / "skills/github-work-orchestrator/SKILL.md").read_text(
            encoding="utf-8"
        )
        loop = (
            ROOT / "skills/github-work-orchestrator/references/coordinator-loop.md"
        ).read_text(encoding="utf-8")
        combined = " ".join((skill + loop).split()).lower()
        for phrase in (
            "campaign_scheduler.py plan-wave",
            "complete worker wave",
            "do not wait for another worker",
            "heartbeat is worker liveness, never coordinator polling",
            "`chat wait` at most 60 seconds",
            "15 minutes",
            "silence alone",
            "attempt four is never automatic",
        ):
            self.assertIn(phrase, combined)
        self.assertNotIn("create recurring paseo heartbeats by default", skill.lower())

    def test_worker_reports_heartbeat_and_worker_done_but_not_completion(self) -> None:
        worker = (ROOT / "skills/github-issue-worker/SKILL.md").read_text(
            encoding="utf-8"
        )
        normalized = " ".join(worker.split()).lower()
        self.assertIn("five minutes", normalized)
        self.assertIn("`heartbeat`", normalized)
        self.assertIn("`worker_done`", normalized)
        self.assertIn("campaign orchestrator alone", normalized)

    def test_room_terminal_and_cleanup_evidence_are_distinct(self) -> None:
        protocol = (ROOT / "shared/communication-protocol.md").read_text(
            encoding="utf-8"
        )
        cleanup = (
            ROOT
            / "skills/github-work-orchestrator/references/cleanup-safety-policy.md"
        ).read_text(encoding="utf-8")
        combined = " ".join((protocol + cleanup).split()).lower()
        for phrase in (
            "worker_done",
            "never authorizes completion",
            "heartbeat, checkpoint, worker_done, review_result, delivery_wake, and delivery_ack are never terminal cleanup evidence",
            "event=merged",
            "branch_merged: true",
        ):
            self.assertIn(phrase, combined)

    def test_cleanup_safety_is_owned_by_the_portable_gwo_skill(self) -> None:
        skill = ROOT / "skills/github-work-orchestrator/SKILL.md"
        contract = (
            ROOT
            / "skills/github-work-orchestrator/references/cleanup-safety-policy.md"
        )
        guard = ROOT / "skills/github-work-orchestrator/scripts/archive_policy.py"
        retired = (
            ROOT
            / "skills/github-work-orchestrator/references/runtime-archive-contract.md"
        )
        self.assertTrue(contract.is_file())
        self.assertFalse(retired.exists())
        normalized = " ".join(
            (
                skill.read_text(encoding="utf-8")
                + contract.read_text(encoding="utf-8")
                + guard.read_text(encoding="utf-8")
            ).split()
        ).lower()
        for phrase in (
            "gwo owns cleanup authorization",
            "existing paseo operations",
            "schema_version: 2",
            "direct idle child",
            "cleanup remains staged",
            "self_archive_forbidden",
            "root_archive_requires_supervisor",
            "archive_target_not_direct_child",
            "worktree_in_use",
            "integration control",
            "removal of all worktree bindings",
        ):
            self.assertIn(phrase, normalized)
        for forbidden in ("codexhub", "daemon-side", "until the paseo daemon"):
            self.assertNotIn(forbidden, normalized)

    def test_recovery_cross_checks_room_agent_git_and_github(self) -> None:
        communication = (ROOT / "shared/communication-protocol.md").read_text(
            encoding="utf-8"
        )
        recovery = (
            ROOT / "skills/github-work-orchestrator/references/communication.md"
        ).read_text(encoding="utf-8")
        combined = " ".join((communication + recovery).split()).lower()
        for phrase in (
            "daemon restart",
            "replay the bounded campaign room",
            "finish callback",
            "duplicate",
            "github",
            "git",
        ):
            self.assertIn(phrase, combined)
        self.assertIn("pending paseo permissions", combined)
        self.assertIn("notifies the parent", combined)

    def test_worker_requires_parentage_and_parent_permission_fallback(self) -> None:
        worker = (ROOT / "shared/worker-execution.md").read_text(encoding="utf-8")
        orchestrator = (ROOT / "skills/github-work-orchestrator/SKILL.md").read_text(
            encoding="utf-8"
        )
        combined = " ".join((worker + orchestrator).split()).lower()
        for phrase in (
            "relationship: subagent",
            "notify_on_finish: true",
            "unexpected_request_fallback: parent",
            "exact `parent_agent_id`",
            "non-destructive",
        ):
            self.assertIn(phrase, combined)

    def test_verification_policy_has_independent_review_axes_and_tiered_gates(self) -> None:
        policy = (ROOT / "shared/verification-policy.md").read_text(encoding="utf-8")
        worker = (ROOT / "skills/github-issue-worker/SKILL.md").read_text(
            encoding="utf-8"
        )
        orchestrator = (ROOT / "skills/github-work-orchestrator/SKILL.md").read_text(
            encoding="utf-8"
        )
        for verification_class in ("fast", "standard", "strict"):
            self.assertIn(f"`{verification_class}`", policy)
        self.assertIn("Review-Owner: orchestrator", policy)
        self.assertIn("Do not run formal review", worker)
        self.assertIn("one `Spec Reviewer` and one `Quality Reviewer`", orchestrator)
        self.assertIn("Both receive the same candidate SHA", orchestrator)

    def test_v43_workspace_relay_capacity_and_review_model_is_explicit(self) -> None:
        skill = (ROOT / "skills/github-work-orchestrator/SKILL.md").read_text(
            encoding="utf-8"
        )
        normalized = " ".join(skill.split()).lower()
        for phrase in (
            "relay · <repo> → coordinator",
            "repository room",
            "campaign control workspace",
            "three worker slots",
            "two review slots",
            "spec reviewer",
            "quality reviewer",
            "provider-native agent/task/swarm",
            "waiting_integration",
        ):
            self.assertIn(phrase, normalized)

    def test_no_new_skill_or_host_runtime_dependency_was_added(self) -> None:
        self.assertEqual(
            {"github-work-orchestrator", "github-issue-intake", "github-issue-worker"},
            set(SKILLS),
        )
        combined = " ".join(
            (
                ROOT / "skills/github-work-orchestrator/SKILL.md"
            ).read_text(encoding="utf-8").split()
        ).lower()
        for phrase in ("do not modify or depend on paseo", "do not add another skill"):
            self.assertIn(phrase, combined)
        self.assertNotIn("paseo-orchestration", combined)

    def test_upstream_ui_evidence_is_prepared_but_not_published(self) -> None:
        for name in (
            "paseo-empty-new-agent-draft-tab.md",
            "paseo-provider-native-zombie-subagent.md",
        ):
            evidence = (ROOT / "docs" / "evidence" / name).read_text(encoding="utf-8")
            self.assertIn("not published", evidence.lower())
            self.assertIn("## Acceptance", evidence)


if __name__ == "__main__":
    unittest.main()
