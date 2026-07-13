from __future__ import annotations

import importlib.util
import re
import sys
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
            "communication-protocol.md#delivery-handshake", worker
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


if __name__ == "__main__":
    unittest.main()
