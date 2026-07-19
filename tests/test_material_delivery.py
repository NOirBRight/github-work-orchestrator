from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "skills"
    / "github-work-orchestrator"
    / "scripts"
    / "material_delivery.py"
)
FIXTURE = ROOT / "tests" / "fixtures" / "material_delivery_disconnects.json"


def load_module():
    spec = importlib.util.spec_from_file_location("material_delivery", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


DELIVERY = load_module()


def agent(
    agent_id: str,
    *,
    role: str,
    status: str,
    parent_agent_id: str | None,
    campaign_id: str = "campaign-20260718",
) -> dict:
    labels = {"repository": "owner/repo", "role": role}
    if role != "repository-coordinator":
        labels["campaign_id"] = campaign_id
    return {
        "agent_id": agent_id,
        "status": status,
        "archived": False,
        "parent_agent_id": parent_agent_id,
        "relationship": "subagent" if parent_agent_id else "root",
        "labels": labels,
        "read_back": True,
    }


def pending_snapshot(trace: dict) -> dict:
    campaign_id = trace["campaign_id"]
    if trace["boundary"] == "worker-to-campaign":
        recipient = agent(
            trace["recipient_agent_id"],
            role="orchestrator",
            status="idle",
            parent_agent_id="11111111-1111-4111-8111-111111111114",
            campaign_id=campaign_id,
        )
        sender = agent(
            trace["sender_agent_id"],
            role="implementation",
            status="running",
            parent_agent_id=trace["recipient_agent_id"],
            campaign_id=campaign_id,
        )
    else:
        recipient = agent(
            trace["recipient_agent_id"],
            role="repository-coordinator",
            status="idle",
            parent_agent_id=None,
            campaign_id=campaign_id,
        )
        sender = agent(
            trace["sender_agent_id"],
            role="orchestrator",
            status="running",
            parent_agent_id=trace["recipient_agent_id"],
            campaign_id=campaign_id,
        )
    return {
        "schema_version": 1,
        "repository": "owner/repo",
        "campaign_id": campaign_id,
        "delivery": {
            "state": "pending",
            "room": trace["room"],
            "event_type": trace["event_type"],
            "authority_scope": trace["authority_scope"],
            "signal_id": trace["signal_id"],
            "message_id": trace["message_id"],
            "dispatch_id": "dispatch-issue-7",
            "issue": "#7",
            "sender_agent_id": trace["sender_agent_id"],
            "recipient_agent_id": trace["recipient_agent_id"],
            "identity_verified": True,
        },
        "sender": sender,
        "recipient": recipient,
    }


def mark_wake_sent(snapshot: dict) -> None:
    snapshot["next_sequence"] = 2
    snapshot["wake_result"] = {
        "agent_id": snapshot["delivery"]["recipient_agent_id"],
        "accepted": True,
    }
    event = DELIVERY.wake_receipt_event_plan(snapshot)["event"]
    snapshot["delivery"].update(
        {
            "state": "wake-sent",
            "delivery_id": event["evidence"]["delivery_id"],
            "wake_signal_id": event["signal_id"],
            "wake_message_id": "77777777-7777-4777-8777-777777777771",
        }
    )


def mark_acknowledged(snapshot: dict) -> None:
    snapshot["recipient"]["status"] = "running"
    snapshot["next_sequence"] = 3
    event = DELIVERY.ack_event_plan(snapshot)["event"]
    snapshot["delivery"].update(
        {
            "state": "acknowledged",
            "delivery_id": event["evidence"]["delivery_id"],
            "ack_signal_id": event["signal_id"],
            "ack_message_id": "77777777-7777-4777-8777-777777777772",
        }
    )


class MaterialDeliveryRegressionTests(unittest.TestCase):
    def test_real_idle_parent_disconnects_produce_signal_only_wake(self) -> None:
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

        for trace in fixture["traces"]:
            with self.subTest(trace=trace["trace_id"]):
                self.assertGreater(trace["observed_follow_up_delay_minutes"], 60)
                result = DELIVERY.delivery_plan(pending_snapshot(trace))

                self.assertEqual("wake-required", result["status"])
                self.assertTrue(result["automatic_execution"])
                self.assertEqual(
                    [
                        {
                            "action": "send-signal-only",
                            "agent_id": trace["recipient_agent_id"],
                            "prompt": (
                                f"GWO_WAKE room={trace['room']} "
                                f"signal={trace['signal_id']} "
                                f"message={trace['message_id']}"
                            ),
                        }
                    ],
                    result["actions"],
                )
                self.assertNotIn(trace["event_type"], result["actions"][0]["prompt"])
                self.assertEqual([], result["blockers"])

    def test_recipient_ack_is_deterministic_and_correlated_to_source_receipt(self) -> None:
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        snapshot = pending_snapshot(fixture["traces"][0])
        snapshot["next_sequence"] = 9

        first = DELIVERY.ack_event_plan(snapshot)
        second = DELIVERY.ack_event_plan(snapshot)

        self.assertEqual(first, second)
        self.assertEqual("eligible", first["status"])
        self.assertTrue(first["automatic_execution"])
        ack = first["event"]
        source = snapshot["delivery"]
        self.assertEqual("DELIVERY_ACK", ack["event_type"])
        self.assertEqual(source["signal_id"], ack["in_reply_to"])
        self.assertEqual(source["recipient_agent_id"], ack["sender_agent_id"])
        self.assertEqual(source["sender_agent_id"], ack["recipient_agent_id"])
        self.assertEqual(source["message_id"], ack["evidence"]["source_message_id"])
        self.assertEqual(source["signal_id"], ack["evidence"]["source_signal_id"])
        self.assertEqual(9, ack["sequence"])

    def test_successful_signal_only_send_produces_one_durable_wake_receipt(self) -> None:
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        snapshot = pending_snapshot(fixture["traces"][0])
        snapshot["next_sequence"] = 10
        snapshot["wake_result"] = {
            "agent_id": snapshot["delivery"]["recipient_agent_id"],
            "accepted": True,
        }

        first = DELIVERY.wake_receipt_event_plan(snapshot)
        second = DELIVERY.wake_receipt_event_plan(snapshot)

        self.assertEqual(first, second)
        receipt = first["event"]
        source = snapshot["delivery"]
        self.assertEqual("DELIVERY_WAKE", receipt["event_type"])
        self.assertEqual(source["signal_id"], receipt["in_reply_to"])
        self.assertEqual(source["sender_agent_id"], receipt["sender_agent_id"])
        self.assertEqual(source["recipient_agent_id"], receipt["recipient_agent_id"])
        self.assertEqual("sent", receipt["evidence"]["outcome"])
        self.assertEqual(source["message_id"], receipt["evidence"]["source_message_id"])

    def test_heartbeat_and_progress_never_wake_the_parent(self) -> None:
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        snapshot = pending_snapshot(fixture["traces"][0])

        for event_type in ("HEARTBEAT", "PROGRESS"):
            with self.subTest(event_type=event_type):
                snapshot["delivery"]["event_type"] = event_type
                result = DELIVERY.delivery_plan(snapshot)

                self.assertEqual("not-required", result["status"])
                self.assertFalse(result["automatic_execution"])
                self.assertEqual([], result["actions"])

    def test_visibility_event_cannot_mint_a_delivery_ack(self) -> None:
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        snapshot = pending_snapshot(fixture["traces"][0])
        snapshot["delivery"]["event_type"] = "HEARTBEAT"
        snapshot["next_sequence"] = 9

        result = DELIVERY.ack_event_plan(snapshot)

        self.assertEqual("protected", result["status"])
        self.assertEqual([], result["actions"])
        self.assertNotIn("event", result)
        self.assertIn("delivery-ack-not-required", result["blockers"])

    def test_closed_recipient_after_wake_escalates_instead_of_waiting_forever(self) -> None:
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        snapshot = pending_snapshot(fixture["traces"][0])
        mark_wake_sent(snapshot)
        snapshot["recipient"]["status"] = "closed"

        result = DELIVERY.delivery_plan(snapshot)

        self.assertEqual("protected", result["status"])
        self.assertFalse(result["automatic_execution"])
        self.assertEqual([], result["actions"])
        self.assertIn("recipient-not-wakeable", result["blockers"])

    def test_claimed_acknowledged_state_without_room_ack_receipt_fails_closed(self) -> None:
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        snapshot = pending_snapshot(fixture["traces"][0])
        snapshot["delivery"]["state"] = "acknowledged"

        result = DELIVERY.delivery_plan(snapshot)

        self.assertEqual("protected", result["status"])
        self.assertEqual([], result["actions"])
        self.assertIn("delivery-state-receipt-invalid", result["blockers"])

    def test_valid_ack_remains_delivered_after_recipient_is_archived(self) -> None:
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        snapshot = pending_snapshot(fixture["traces"][0])
        mark_wake_sent(snapshot)
        mark_acknowledged(snapshot)
        snapshot["recipient"]["archived"] = True
        snapshot["recipient"]["status"] = "closed"

        result = DELIVERY.delivery_plan(snapshot)

        self.assertEqual("delivered", result["status"])
        self.assertEqual([], result["actions"])
        self.assertEqual([], result["blockers"])

    def test_running_parent_waits_without_receiving_a_prompt(self) -> None:
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        snapshot = pending_snapshot(fixture["traces"][0])

        for status in ("running", "initializing"):
            with self.subTest(status=status):
                candidate = copy.deepcopy(snapshot)
                candidate["recipient"]["status"] = status
                result = DELIVERY.delivery_plan(candidate)

                self.assertEqual("awaiting-ack", result["status"])
                self.assertEqual(
                    [
                        {
                            "action": "wait-for-delivery-ack",
                            "room": "gwo-space2-campaign",
                            "signal_id": "space2-worker-done-001",
                            "timeout": "60s",
                        }
                    ],
                    result["actions"],
                )
                self.assertNotIn("prompt", result["actions"][0])

    def test_unacked_wake_returning_to_idle_fails_closed_and_ack_finishes_delivery(self) -> None:
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        snapshot = pending_snapshot(fixture["traces"][0])
        mark_wake_sent(snapshot)

        unacknowledged = DELIVERY.delivery_plan(snapshot)
        self.assertEqual("protected", unacknowledged["status"])
        self.assertEqual([], unacknowledged["actions"])
        self.assertIn(
            "wake-unacknowledged-recipient-idle", unacknowledged["blockers"]
        )

        mark_acknowledged(snapshot)
        delivered = DELIVERY.delivery_plan(snapshot)
        self.assertEqual("delivered", delivered["status"])
        self.assertFalse(delivered["automatic_execution"])
        self.assertEqual([], delivered["actions"])

    def test_foreign_sibling_scope_and_unverified_source_fail_closed(self) -> None:
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        baseline = pending_snapshot(fixture["traces"][0])
        cases = []

        sibling = copy.deepcopy(baseline)
        sibling["sender"]["parent_agent_id"] = "agent-other-parent"
        cases.append((sibling, "delivery-target-not-direct-relative"))

        wrong_scope = copy.deepcopy(baseline)
        wrong_scope["delivery"]["authority_scope"] = "review-dispatch"
        cases.append((wrong_scope, "delivery-authority-scope-mismatch"))

        unverified = copy.deepcopy(baseline)
        unverified["delivery"]["identity_verified"] = False
        cases.append((unverified, "source-event-identity-not-verified"))

        archived = copy.deepcopy(baseline)
        archived["recipient"]["archived"] = True
        cases.append((archived, "delivery-agent-archived"))

        for snapshot, blocker in cases:
            with self.subTest(blocker=blocker):
                result = DELIVERY.delivery_plan(snapshot)
                self.assertEqual("protected", result["status"])
                self.assertFalse(result["automatic_execution"])
                self.assertEqual([], result["actions"])
                self.assertIn(blocker, result["blockers"])

    def test_missing_issue_or_dispatch_cannot_trigger_a_wake(self) -> None:
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        baseline = pending_snapshot(fixture["traces"][0])
        for field, value in (("issue", ""), ("dispatch_id", None)):
            with self.subTest(field=field):
                snapshot = copy.deepcopy(baseline)
                snapshot["delivery"][field] = value
                with self.assertRaisesRegex(ValueError, field):
                    DELIVERY.delivery_plan(snapshot)

    def test_delivery_cli_exposes_the_same_fail_closed_public_interface(self) -> None:
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        snapshot = pending_snapshot(fixture["traces"][0])
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "snapshot.json"
            path.write_text(json.dumps(snapshot), encoding="utf-8")
            accepted = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "delivery-plan",
                    "--snapshot",
                    str(path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            snapshot["schema_version"] = 2
            path.write_text(json.dumps(snapshot), encoding="utf-8")
            rejected = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "delivery-plan",
                    "--snapshot",
                    str(path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(0, accepted.returncode, accepted.stderr)
        self.assertEqual(
            "wake-required", json.loads(accepted.stdout)["result"]["status"]
        )
        self.assertEqual(2, rejected.returncode)
        self.assertFalse(json.loads(rejected.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
