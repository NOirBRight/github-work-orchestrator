from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "skills" / "github-work-orchestrator" / "scripts"


def load_module(name: str):
    path = SCRIPT_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    sys.path.insert(0, str(SCRIPT_DIR))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


repository_room = load_module("repository_room")


RELAY_ID = "11111111-1111-4111-8111-111111111111"
COORDINATOR_ID = "22222222-2222-4222-8222-222222222222"
REQUEST_SIGNAL = "repo-request-53d395a4-793d-43d3-80f0-0f3b53acd94d"


def event(
    *,
    signal_id: str = REQUEST_SIGNAL,
    sequence: int = 1,
    event_type: str = "OPERATOR_REQUEST",
    sender_agent_id: str = RELAY_ID,
    sender_role: str = "operator-relay",
    in_reply_to: str | None = None,
    payload: dict | None = None,
) -> dict:
    if payload is None:
        payload = {
            "summary": "Please reconcile issue 151.",
            "original_message_sha256": "a" * 64,
        }
    return {
        "schema_version": 1,
        "repository": "owner/repo",
        "signal_id": signal_id,
        "sequence": sequence,
        "event_type": event_type,
        "sender_agent_id": sender_agent_id,
        "sender_role": sender_role,
        "in_reply_to": in_reply_to,
        "payload": payload,
    }


def receipt(agent_id: str, role: str) -> dict:
    return {
        "agent_id": agent_id,
        "repository": "owner/repo",
        "role": role,
        "labels": {"repository": "owner/repo", "role": role},
        "relationship": "root",
        "parent_agent_id": None,
        "read_back": True,
    }


class StubRunner:
    def __init__(self, messages: list[dict] | None = None):
        self.messages = messages or []
        self.calls: list[list[str]] = []

    def __call__(self, arguments):
        args = list(arguments)
        self.calls.append(args)
        if args[:2] == ["chat", "read"]:
            output = self.messages
        elif args[:2] == ["chat", "post"]:
            output = {"id": "message-001"}
        elif args[:2] in (["chat", "create"], ["chat", "inspect"]):
            output = {"ok": True}
        else:
            output = {"ok": True}
        return subprocess.CompletedProcess(args, 0, json.dumps(output), "")


class RepositoryRoomValidationTests(unittest.TestCase):
    def test_windows_cmd_wrapper_preserves_paseo_cmd_arguments(self) -> None:
        command = repository_room._windows_cmd_command(
            r"C:\Program Files\Paseo\paseo.cmd",
            ["chat", "post", "gwo-repo", "two words"],
        )
        self.assertEqual(["/d", "/c"], command[1:3])
        self.assertEqual(r"C:\Program Files\Paseo\paseo.cmd", command[3])
        self.assertEqual("two words", command[-1])

    def test_room_name_is_repository_scoped_and_stable(self) -> None:
        room = repository_room.room_name("Owner/Repo")
        self.assertTrue(room.startswith("gwo-repo-owner-repo-"))
        self.assertEqual(room, repository_room.room_name("owner/repo"))

    def test_room_name_digest_prevents_slug_collisions(self) -> None:
        self.assertNotEqual(
            repository_room.room_name("owner-a/repo"),
            repository_room.room_name("owner/a-repo"),
        )

    def test_operator_request_requires_sanitized_summary_and_digest(self) -> None:
        self.assertEqual([], repository_room.validate_event(event()))
        invalid = event(
            payload={
                "summary": r"Read C:\\Users\\operator\\secret.txt token=abc123",
                "original_message_sha256": "a" * 64,
            }
        )
        self.assertIn("operator-request-payload-sensitive", repository_room.validate_event(invalid))

    def test_sanitizer_rejects_delimiter_paths_unc_and_common_secret_shapes(self) -> None:
        for summary in (
            r"file=C:\Users\operator\secret.txt",
            "file=C://Users/operator/secret.txt",
            "path=/home/operator/secret.txt",
            "path=/opt/company/repo/file.py",
            "path=/workspace/repo/file.py",
            "url=https://example.com,/home/operator/secret.txt",
            "url=https://example.com|C://Users/operator/secret.txt",
            r"url=https://example.com;file=C:\Users\operator\secret.txt",
            r"share=\\server\private\secret.txt",
            "authorization=Bearer abc.def.ghi",
            "token=github_pat_example123",
            "key=AKIAABCDEFGHIJKLMNOP",
            "-----BEGIN PRIVATE KEY-----",
        ):
            with self.subTest(summary=summary):
                invalid = event(
                    payload={
                        "summary": summary,
                        "original_message_sha256": "a" * 64,
                    }
                )
                self.assertIn(
                    "operator-request-payload-sensitive",
                    repository_room.validate_event(invalid),
                )

    def test_https_url_is_not_misclassified_as_a_windows_drive_path(self) -> None:
        request = event(
            payload={
                "summary": "Please inspect https://github.com/owner/repo/issues/151",
                "original_message_sha256": "a" * 64,
            }
        )

        self.assertEqual([], repository_room.validate_event(request))

    def test_payload_rejects_private_prompt_and_unknown_fields(self) -> None:
        invalid = event(
            payload={
                "summary": "Reconcile issue 151.",
                "original_message_sha256": "a" * 64,
                "private_prompt": "hidden chain",
            }
        )
        self.assertIn("operator-request-payload-fields-invalid", repository_room.validate_event(invalid))

    def test_coordinator_reply_requires_request_correlation(self) -> None:
        accepted = event(
            signal_id="repo-response-53d395a4-793d-43d3-80f0-0f3b53acd94d",
            event_type="REQUEST_ACCEPTED",
            sender_agent_id=COORDINATOR_ID,
            sender_role="repository-coordinator",
            in_reply_to=REQUEST_SIGNAL,
            payload={"disposition": "queued"},
        )
        self.assertEqual([], repository_room.validate_event(accepted))
        accepted["in_reply_to"] = None
        self.assertIn("repository-response-requires-in-reply-to", repository_room.validate_event(accepted))

    def test_roles_cannot_author_each_others_events(self) -> None:
        invalid = event(sender_agent_id=COORDINATOR_ID, sender_role="repository-coordinator")
        self.assertIn("event-role-not-authorized", repository_room.validate_event(invalid))


class RepositoryRoomReplayTests(unittest.TestCase):
    def test_post_requires_runtime_sender_and_matching_repository_room(self) -> None:
        runner = StubRunner()
        protocol = repository_room.RepositoryRoom(runner)
        with patch.dict(os.environ, {"PASEO_AGENT_ID": RELAY_ID}, clear=False):
            result = protocol.post(repository_room.room_name("owner/repo"), event())
        self.assertEqual(REQUEST_SIGNAL, result["signal_id"])
        with patch.dict(os.environ, {"PASEO_AGENT_ID": COORDINATOR_ID}, clear=False):
            with self.assertRaises(repository_room.RoomProtocolError):
                protocol.post(repository_room.room_name("owner/repo"), event())

    def test_replay_verifies_identity_and_deduplicates_exact_retry(self) -> None:
        body = json.dumps(event(), separators=(",", ":"), sort_keys=True)
        runner = StubRunner(
            [
                {"id": "m1", "author": RELAY_ID, "body": body},
                {"id": "m2", "author": RELAY_ID, "body": body},
            ]
        )
        protocol = repository_room.RepositoryRoom(runner)

        result = protocol.replay(
            repository_room.room_name("owner/repo"),
            identity_receipts=[receipt(RELAY_ID, "operator-relay")],
        )

        self.assertEqual(1, len(result["events"]))
        self.assertEqual([], result["rejected"])

    def test_conflicting_duplicate_and_nonmonotonic_sequence_are_rejected(self) -> None:
        first = event()
        conflict = event(payload={"summary": "Different", "original_message_sha256": "b" * 64})
        late = event(
            signal_id="repo-request-63d395a4-793d-43d3-80f0-0f3b53acd94d",
            sequence=1,
        )
        messages = [
            {"id": "m1", "author": RELAY_ID, "body": json.dumps(first)},
            {"id": "m2", "author": RELAY_ID, "body": json.dumps(conflict)},
            {"id": "m3", "author": RELAY_ID, "body": json.dumps(late)},
        ]
        result = repository_room.RepositoryRoom(StubRunner(messages)).replay(
            repository_room.room_name("owner/repo"),
            identity_receipts=[receipt(RELAY_ID, "operator-relay")],
        )

        reasons = ",".join(item["reason"] for item in result["rejected"])
        self.assertIn("duplicate-signal-conflict", reasons)
        self.assertIn("nonmonotonic-sequence", reasons)
        self.assertIn(RELAY_ID, result["blocked_senders"])

    def test_cross_repository_and_author_mismatch_are_rejected(self) -> None:
        cross = event()
        cross["repository"] = "other/repo"
        messages = [
            {"id": "m1", "author": RELAY_ID, "body": json.dumps(cross)},
            {"id": "m2", "author": COORDINATOR_ID, "body": json.dumps(event())},
        ]
        result = repository_room.RepositoryRoom(StubRunner(messages)).replay(
            repository_room.room_name("owner/repo"),
            identity_receipts=[receipt(RELAY_ID, "operator-relay")],
        )

        reasons = ",".join(item["reason"] for item in result["rejected"])
        self.assertIn("room-repository-mismatch", reasons)
        self.assertIn("message-author-mismatch", reasons)

    def test_conflicting_request_poisons_responses_before_and_after_conflict(self) -> None:
        request = event()
        before = event(
            signal_id="repo-response-13d395a4-793d-43d3-80f0-0f3b53acd94d",
            sequence=1,
            event_type="REQUEST_ACCEPTED",
            sender_agent_id=COORDINATOR_ID,
            sender_role="repository-coordinator",
            in_reply_to=REQUEST_SIGNAL,
            payload={"disposition": "queued"},
        )
        conflict = event(
            sequence=2,
            payload={
                "summary": "Conflicting request body.",
                "original_message_sha256": "b" * 64,
            },
        )
        after = event(
            signal_id="repo-response-23d395a4-793d-43d3-80f0-0f3b53acd94d",
            sequence=2,
            event_type="REQUEST_REJECTED",
            sender_agent_id=COORDINATOR_ID,
            sender_role="repository-coordinator",
            in_reply_to=REQUEST_SIGNAL,
            payload={"reason": "duplicate"},
        )
        messages = [
            {"id": f"m{index}", "author": payload["sender_agent_id"], "body": json.dumps(payload)}
            for index, payload in enumerate((request, before, conflict, after), start=1)
        ]

        result = repository_room.RepositoryRoom(StubRunner(messages)).replay(
            repository_room.room_name("owner/repo"),
            identity_receipts=[
                receipt(RELAY_ID, "operator-relay"),
                receipt(COORDINATOR_ID, "repository-coordinator"),
            ],
        )

        self.assertEqual([], result["events"])
        self.assertEqual([REQUEST_SIGNAL], result["blocked_requests"])
        reasons = [item["reason"] for item in result["rejected"]]
        self.assertIn("duplicate-signal-conflict", reasons)
        self.assertEqual(2, reasons.count("repository-request-poisoned"))

    def test_mislabeled_or_nonroot_coordinator_response_is_rejected(self) -> None:
        request_body = json.dumps(event())
        response = event(
            signal_id="repo-response-13d395a4-793d-43d3-80f0-0f3b53acd94d",
            event_type="REQUEST_ACCEPTED",
            sender_agent_id=COORDINATOR_ID,
            sender_role="repository-coordinator",
            in_reply_to=REQUEST_SIGNAL,
            payload={"disposition": "queued"},
        )
        invalid = receipt(COORDINATOR_ID, "repository-coordinator")
        invalid["relationship"] = "subagent"
        invalid["parent_agent_id"] = "foreign-parent"
        result = repository_room.RepositoryRoom(
            StubRunner(
                [
                    {"id": "m1", "author": RELAY_ID, "body": request_body},
                    {"id": "m2", "author": COORDINATOR_ID, "body": json.dumps(response)},
                ]
            )
        ).replay(
            repository_room.room_name("owner/repo"),
            identity_receipts=[receipt(RELAY_ID, "operator-relay"), invalid],
        )

        self.assertEqual(
            ["OPERATOR_REQUEST"], [item["event_type"] for item in result["events"]]
        )
        self.assertIn(
            "identity-receipt-coordinator-parentage-invalid",
            result["rejected"][0]["reason"],
        )


if __name__ == "__main__":
    unittest.main()
