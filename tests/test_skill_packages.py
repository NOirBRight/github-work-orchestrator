from __future__ import annotations

import importlib.util
import json
import re
import shutil
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
                self.assertTrue((skill / "agents" / "openai.yaml").is_file())

    def test_shared_references_are_synchronized_into_every_skill(self) -> None:
        for name, filenames in SYNC.PACKAGES.items():
            packaged_names = {
                path.name
                for path in (
                    ROOT / "skills" / name / "references" / "shared"
                ).glob("*.md")
            }
            self.assertEqual(set(filenames), packaged_names)
            for filename in filenames:
                source = ROOT / "shared" / filename
                packaged = (
                    ROOT / "skills" / name / "references" / "shared" / filename
                )
                with self.subTest(skill=name, reference=filename):
                    self.assertEqual(source.read_bytes(), packaged.read_bytes())

    def test_sync_check_accepts_committed_packages(self) -> None:
        self.assertEqual([], SYNC.find_drift(ROOT))

    def test_package_manifests_pin_version_and_content_digest(self) -> None:
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

    def test_legacy_root_install_loads_the_packaged_orchestrator(self) -> None:
        wrapper = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn(
            "skills/github-work-orchestrator/SKILL.md", wrapper
        )
        self.assertEqual(
            (ROOT / "skills/github-work-orchestrator/agents/openai.yaml").read_bytes(),
            (ROOT / "agents/openai.yaml").read_bytes(),
        )

    def test_trigger_descriptions_are_role_specific(self) -> None:
        descriptions = {}
        for name in SKILLS:
            text = (ROOT / "skills" / name / "SKILL.md").read_text(
                encoding="utf-8"
            )
            frontmatter = text.split("---", 2)[1]
            description = next(
                line.removeprefix("description:").strip().strip('"')
                for line in frontmatter.splitlines()
                if line.startswith("description:")
            )
            descriptions[name] = description.lower()

        self.assertIn("dispatch", descriptions["github-work-orchestrator"])
        self.assertNotIn("bug reports", descriptions["github-work-orchestrator"])
        self.assertIn("bug reports", descriptions["github-issue-intake"])
        self.assertNotIn("dispatch", descriptions["github-issue-intake"])
        self.assertIn("one assigned github issue", descriptions["github-issue-worker"])
        self.assertNotIn("rough ideas", descriptions["github-issue-worker"])

    def test_all_packaged_markdown_links_resolve_inside_each_skill(self) -> None:
        link_pattern = re.compile(r"\[[^\]]+\]\(([^)#]+)(?:#[^)]+)?\)")
        for name in SKILLS:
            skill = ROOT / "skills" / name
            for markdown in skill.rglob("*.md"):
                for target in link_pattern.findall(
                    markdown.read_text(encoding="utf-8")
                ):
                    if "://" in target:
                        continue
                    resolved = (markdown.parent / target).resolve()
                    with self.subTest(skill=name, file=markdown.name, link=target):
                        self.assertTrue(resolved.is_file())
                        self.assertTrue(resolved.is_relative_to(skill.resolve()))

    def test_callback_delivery_requires_native_send_before_final_answer(self) -> None:
        protocol = (ROOT / "shared" / "communication-protocol.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "A final answer in the Worker task is not callback delivery", protocol
        )
        self.assertIn("send_message_to_thread", protocol)
        self.assertIn("SIGNAL_RECEIVED", protocol)
        self.assertIn("CALLBACK_DELIVERY_FAILED", protocol)
        worker = (ROOT / "skills/github-issue-worker/SKILL.md").read_text(
            encoding="utf-8"
        )
        orchestrator = (
            ROOT / "skills/github-work-orchestrator/SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "worker-execution.md#publish-and-callback", worker
        )
        self.assertIn("exact Orchestrator callback", worker)
        self.assertIn(
            "communication-protocol.md#delivery-handshake", orchestrator
        )
        self.assertIn("exact Orchestrator callback task ID", orchestrator)
        self.assertIn(
            "communication-protocol.md#signal-driven-monitoring", orchestrator
        )

    def test_shared_protocol_preserves_reliability_guards(self) -> None:
        state_rules = (ROOT / "shared" / "github-state-rules.md").read_text(
            encoding="utf-8"
        )
        communication = (
            ROOT / "shared" / "communication-protocol.md"
        ).read_text(encoding="utf-8")
        for required in (
            "Reliable task materialization",
            "Permission and repository preflight",
            "Recovery and WIP preservation",
        ):
            self.assertIn(required, state_rules)
        self.assertIn("at least ten minutes", communication)

    def test_verification_policy_has_one_review_owner_and_tiered_gates(self) -> None:
        policy = (ROOT / "shared" / "verification-policy.md").read_text(
            encoding="utf-8"
        )
        worker = (ROOT / "skills/github-issue-worker/SKILL.md").read_text(
            encoding="utf-8"
        )
        orchestrator = (
            ROOT / "skills/github-work-orchestrator/SKILL.md"
        ).read_text(encoding="utf-8")
        for verification_class in ("fast", "standard", "strict"):
            self.assertIn(f"`{verification_class}`", policy)
        self.assertIn("Review-Owner: orchestrator", policy)
        self.assertIn("must not run", policy)
        self.assertIn("Do not invoke the generic `code-review` Skill", worker)
        self.assertIn("exactly one Orchestrator-owned", orchestrator)

    def test_worker_signal_includes_execution_metrics(self) -> None:
        protocol = (ROOT / "shared" / "communication-protocol.md").read_text(
            encoding="utf-8"
        )
        for field in (
            "Verification-Class",
            "Phase-Timings",
            "Full-Suite-Runs",
            "Review-Runs",
            "Scope-Delta",
        ):
            self.assertIn(field, protocol)

    def test_routine_profiles_do_not_default_to_sol_or_max(self) -> None:
        profiles = (ROOT / "shared" / "model-profiles.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("| `orchestrator` | `gpt-5.6-terra / high`", profiles)
        self.assertIn("| `architecture` | `gpt-5.6-sol / max`", profiles)
        self.assertIn("| `standard` | `gpt-5.6-luna / high`", profiles)
        self.assertIn("Max reasoning is not a routine default", profiles)


if __name__ == "__main__":
    unittest.main()
