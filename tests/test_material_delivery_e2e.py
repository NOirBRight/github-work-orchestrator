from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "github-work-orchestrator" / "scripts"


def load_module(name: str):
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


ROOM = load_module("paseo_room")
DELIVERY = load_module("material_delivery")

WORKER_ID = "agent-worker-e2e"
CAMPAIGN_ID = "agent-campaign-e2e"
ROOT_ID = "agent-root-e2e"


class InMemoryPaseoRoom:
    """Paseo chat boundary double; protocol and policy code stay real."""

    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    def __call__(self, arguments):
        args = list(arguments)
        action = args[1]
        if action == "post":
            index = len(self.messages) + 1
            message = {
                "id": f"55555555-5555-4555-8555-{index:012d}",
                "body": args[3],
                "author": os.environ["PASEO_AGENT_ID"],
            }
            self.messages.append(message)
            payload = message
        elif action == "read":
            payload = self.messages
        elif action == "wait":
            payload = []
        else:
            raise AssertionError(args)
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")


def room_event() -> dict:
    return {
        "schema_version": 1,
        "signal_id": "e2e-worker-done-1",
        "campaign_id": "campaign-e2e",
        "dispatch_id": "dispatch-issue-701-a1",
        "sequence": 1,
        "event_type": "WORKER_DONE",
        "issue": "#701",
        "sender_agent_id": WORKER_ID,
        "recipient_agent_id": CAMPAIGN_ID,
        "evidence": {
            "head_sha": "a" * 40,
            "verification": ["python -m pytest: passed"],
            "changed_paths": ["src/material_delivery.py"],
            "pr": "https://example.test/pr/701",
        },
        "next_action": "Campaign verifies candidate",
    }


def agent_readbacks() -> list[dict]:
    return [
        {
            "agent_id": WORKER_ID,
            "parent_agent_id": CAMPAIGN_ID,
            "relationship": "subagent",
            "labels": {
                "repository": "owner/repo",
                "campaign_id": "campaign-e2e",
                "dispatch_id": "dispatch-issue-701-a1",
                "role": "implementation",
            },
            "read_back": True,
        },
        {
            "agent_id": CAMPAIGN_ID,
            "parent_agent_id": ROOT_ID,
            "relationship": "subagent",
            "labels": {
                "repository": "owner/repo",
                "campaign_id": "campaign-e2e",
                "role": "orchestrator",
            },
            "read_back": True,
        },
    ]


def runtime_agent(readback: dict, status: str) -> dict:
    return {
        **readback,
        "status": status,
        "archived": False,
    }


class MaterialDeliveryEndToEndTests(unittest.TestCase):
    def test_worker_done_reaches_idle_campaign_and_finishes_acknowledged(self) -> None:
        boundary = InMemoryPaseoRoom()
        protocol = ROOM.PaseoRoom(boundary)
        readbacks = agent_readbacks()
        receipts = ROOM.identity_receipt_plan(
            {
                "schema_version": 1,
                "repository": "owner/repo",
                "campaign_id": "campaign-e2e",
                "dispatch_id": "dispatch-issue-701-a1",
                "authority_scope": "worker-dispatch",
                "agent_readbacks": readbacks,
            }
        )["receipts"]

        with mock.patch.dict(os.environ, {"PASEO_AGENT_ID": WORKER_ID}):
            publish = protocol.post_material(
                "gwo-campaign-e2e",
                room_event(),
                authority_scope="worker-dispatch",
                identity_receipts=receipts,
            )

        snapshot = {
            "schema_version": 1,
            "repository": "owner/repo",
            "campaign_id": "campaign-e2e",
            "delivery": publish["delivery"],
            "sender": runtime_agent(readbacks[0], "running"),
            "recipient": runtime_agent(readbacks[1], "idle"),
        }
        wake_plan = DELIVERY.delivery_plan(snapshot)
        self.assertEqual("send-signal-only", wake_plan["actions"][0]["action"])
        self.assertNotIn("WORKER_DONE", wake_plan["actions"][0]["prompt"])

        snapshot["next_sequence"] = 2
        snapshot["wake_result"] = {"agent_id": CAMPAIGN_ID, "accepted": True}
        wake_receipt = DELIVERY.wake_receipt_event_plan(snapshot)["event"]
        with mock.patch.dict(os.environ, {"PASEO_AGENT_ID": WORKER_ID}):
            protocol.post("gwo-campaign-e2e", wake_receipt)

        campaign_replay = protocol.replay(
            "gwo-campaign-e2e", identity_receipts=receipts
        )
        self.assertEqual("wake-sent", campaign_replay["deliveries"][0]["state"])

        snapshot["delivery"] = campaign_replay["deliveries"][0]
        snapshot["recipient"]["status"] = "running"
        snapshot["next_sequence"] = 1
        ack = DELIVERY.ack_event_plan(snapshot)["event"]
        with mock.patch.dict(os.environ, {"PASEO_AGENT_ID": CAMPAIGN_ID}):
            protocol.post("gwo-campaign-e2e", ack)

        worker_replay = protocol.replay(
            "gwo-campaign-e2e",
            identity_receipts=receipts,
            dispatch_id="dispatch-issue-701-a1",
            consumer_role="worker",
        )
        final_delivery = worker_replay["deliveries"][0]
        self.assertEqual("acknowledged", final_delivery["state"])
        snapshot["delivery"] = final_delivery
        final_plan = DELIVERY.delivery_plan(snapshot)
        self.assertEqual("delivered", final_plan["status"])
        self.assertEqual([], final_plan["actions"])

        start = {
            "schema_version": 1,
            "signal_id": "e2e-start-1",
            "campaign_id": "campaign-e2e",
            "dispatch_id": "dispatch-issue-701-a1",
            "sequence": 2,
            "event_type": "START",
            "issue": "#701",
            "sender_agent_id": CAMPAIGN_ID,
            "recipient_agent_id": WORKER_ID,
            "evidence": "AGENT_READY delivery acknowledged",
            "next_action": "Worker accepts START",
        }
        with mock.patch.dict(os.environ, {"PASEO_AGENT_ID": CAMPAIGN_ID}):
            start_publish = protocol.post_material(
                "gwo-campaign-e2e",
                start,
                authority_scope="worker-dispatch",
                identity_receipts=receipts,
            )
        start_snapshot = {
            "schema_version": 1,
            "repository": "owner/repo",
            "campaign_id": "campaign-e2e",
            "delivery": start_publish["delivery"],
            "sender": runtime_agent(readbacks[1], "running"),
            "recipient": runtime_agent(readbacks[0], "running"),
        }
        busy_plan = DELIVERY.delivery_plan(start_snapshot)
        self.assertEqual("wait-for-delivery-ack", busy_plan["actions"][0]["action"])
        self.assertNotIn("prompt", busy_plan["actions"][0])

        worker_sees_start = protocol.replay(
            "gwo-campaign-e2e",
            identity_receipts=receipts,
            dispatch_id="dispatch-issue-701-a1",
            consumer_role="worker",
        )
        start_snapshot["delivery"] = next(
            item
            for item in worker_sees_start["deliveries"]
            if item["signal_id"] == "e2e-start-1"
        )
        start_snapshot["next_sequence"] = 3
        start_ack = DELIVERY.ack_event_plan(start_snapshot)["event"]
        with mock.patch.dict(os.environ, {"PASEO_AGENT_ID": WORKER_ID}):
            protocol.post("gwo-campaign-e2e", start_ack)

        final_campaign_replay = protocol.replay(
            "gwo-campaign-e2e", identity_receipts=receipts
        )
        delivery_states = {
            item["signal_id"]: item["state"]
            for item in final_campaign_replay["deliveries"]
        }
        self.assertEqual(
            {
                "e2e-worker-done-1": "acknowledged",
                "e2e-start-1": "acknowledged",
            },
            delivery_states,
        )
        self.assertEqual(
            ["WORKER_DONE"],
            [item["event_type"] for item in worker_replay["events"]],
        )
        self.assertEqual([], worker_replay["blocked_dispatches"])
        self.assertEqual([], worker_replay["rejected"])


if __name__ == "__main__":
    unittest.main()
