#!/usr/bin/env python3
"""Opt-in live Windows E2E for GWO Paseo Prompt transport and restart readback."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from gwo_v8 import (  # noqa: E402
    PaseoCliClient,
    PaseoRuntimeAdapter,
    ReviewAxisRequest,
    RuntimeAdapterError,
    RuntimeAdmission,
    RuntimeProfile,
    RuntimePrompt,
)


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _prompt(byte_floor: int, *, role: str) -> RuntimePrompt:
    text = json.dumps(
        {
            "instruction": (
                f"GWO live Paseo transport E2E for {role}. "
                "Do not modify files. Confirm receipt briefly, then stop."
            ),
            "payload": role[0] * byte_floor,
        },
        separators=(",", ":"),
    )
    return RuntimePrompt(
        text=text,
        digest=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def _wait_worker(
    adapter: PaseoRuntimeAdapter,
    admission: RuntimeAdmission,
    prompt: RuntimePrompt,
    *,
    deadline: float,
):
    binding = None
    while time.monotonic() < deadline:
        try:
            binding = adapter.read_binding(admission, prompt)
            if binding is None:
                binding = adapter.materialize(admission, prompt)
            if binding.prompt_accepted:
                return binding
            adapter.accept_prompt(binding, prompt)
        except RuntimeAdapterError as error:
            if error.failure_class == "permanent":
                raise
        time.sleep(1.0)
    raise TimeoutError("Worker Prompt acceptance did not converge before timeout")


def _wait_review(
    adapter: PaseoRuntimeAdapter,
    request: ReviewAxisRequest,
    profile: RuntimeProfile,
    *,
    parent_agent_id: str,
    deadline: float,
):
    last_error: RuntimeAdapterError | None = None
    while time.monotonic() < deadline:
        try:
            return adapter.materialize_review_axis(
                request,
                profile,
                parent_agent_id=parent_agent_id,
            )
        except RuntimeAdapterError as error:
            last_error = error
            if error.failure_class == "permanent":
                raise
        time.sleep(1.0)
    raise TimeoutError(
        "Review Prompt acceptance did not converge before timeout"
    ) from last_error


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Confirm that the installed Paseo daemon may create live Agents.",
    )
    parser.add_argument("--executable", default="paseo")
    parser.add_argument(
        "--provider",
        default=os.environ.get("GWO_E2E_PROVIDER", "codex"),
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("GWO_E2E_MODEL", "gpt-5.6-sol"),
    )
    parser.add_argument(
        "--thinking",
        default=os.environ.get("GWO_E2E_THINKING", "high"),
    )
    parser.add_argument(
        "--mode",
        default=os.environ.get("GWO_E2E_MODE", "full-access"),
    )
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    if sys.platform != "win32":
        raise SystemExit("this transport E2E is intentionally Windows-only")
    if not args.live and os.environ.get("GWO_RUN_PASEO_TRANSPORT_E2E") != "1":
        raise SystemExit(
            "live Paseo Agent creation is opt-in; pass --live or set "
            "GWO_RUN_PASEO_TRANSPORT_E2E=1"
        )
    if args.timeout_seconds <= 0:
        raise SystemExit("--timeout-seconds must be positive")

    profile = RuntimeProfile(
        name="paseo-transport-e2e",
        provider=args.provider,
        model=args.model,
        thinking=args.thinking,
        mode=args.mode,
        features={},
    )
    run_id = uuid.uuid4().hex
    repository_key = f"local/paseo-transport-e2e/{run_id}"
    client = PaseoCliClient(executable=args.executable)
    worker_prompt = _prompt(310_000, role="worker")
    review_requests: list[ReviewAxisRequest] = []
    worker_binding = None
    review_bindings = {}

    with tempfile.TemporaryDirectory(
        prefix="gwo-paseo-transport-",
        ignore_cleanup_errors=True,
    ) as temporary:
        repository = Path(temporary) / "repository"
        subprocess.run(
            ["git", "init", "-b", "main", str(repository)],
            check=True,
            capture_output=True,
        )
        _git(repository, "config", "user.name", "GWO Transport E2E")
        _git(repository, "config", "user.email", "gwo-e2e@example.test")
        (repository / "README.md").write_text(
            "Paseo transport E2E\n",
            encoding="utf-8",
        )
        _git(repository, "add", "README.md")
        _git(repository, "commit", "-m", "Paseo transport E2E base")
        candidate_sha = _git(repository, "rev-parse", "HEAD")
        admission = RuntimeAdmission(
            repository=repository_key,
            plan_digest=hashlib.sha256(f"plan:{run_id}".encode("utf-8")).hexdigest(),
            node_key=f"node:paseo-transport:{run_id}",
            admission_id=f"admission:paseo-transport:{run_id}",
            repository_path=repository,
            base_sha=candidate_sha,
            runtime_profile=profile,
        )
        deadline = time.monotonic() + args.timeout_seconds
        try:
            first_adapter = PaseoRuntimeAdapter(client)
            worker_binding = _wait_worker(
                first_adapter,
                admission,
                worker_prompt,
                deadline=deadline,
            )
            if worker_binding.agent_id is None:
                raise AssertionError("Worker Binding omitted Agent identity")

            # Coordinator-client restart: no process-local labels survive.
            client = PaseoCliClient(executable=args.executable)
            restarted = PaseoRuntimeAdapter(client)
            adopted_worker = restarted.read_binding(admission, worker_prompt)
            if (
                adopted_worker is None
                or adopted_worker.agent_id != worker_binding.agent_id
                or not adopted_worker.prompt_accepted
            ):
                raise AssertionError("Worker restart adoption changed identity")
            if (
                client.prompt_acceptance_count(
                    adopted_worker.agent_id,
                    worker_prompt,
                )
                != 1
            ):
                raise AssertionError("Worker Prompt was not accepted exactly once")

            review_requests = [
                ReviewAxisRequest(
                    repository=repository_key,
                    attempt_id=f"attempt:paseo-transport:{run_id}",
                    candidate_sha=candidate_sha,
                    base_sha=candidate_sha,
                    axis=axis,
                    recovery_ordinal=0,
                    workspace=repository,
                    diff_command=(
                        "git",
                        "diff",
                        f"{candidate_sha}...{candidate_sha}",
                    ),
                    commit_list=("Paseo transport E2E base",),
                    spec_source_ref=f"e2e://paseo-transport/{run_id}",
                    spec_text=json.dumps(
                        {
                            "instruction": (
                                "Transport probe only. Do not modify files."
                            ),
                            "payload": axis[0] * 180_000,
                        },
                        separators=(",", ":"),
                    ),
                    standards_sources=("AGENTS.md", "CONTEXT.md"),
                    check_manifest_digest=hashlib.sha256(
                        f"checks:{axis}:{run_id}".encode("utf-8")
                    ).hexdigest(),
                )
                for axis in ("standards", "spec")
            ]
            if any(
                len(request.to_prompt().text.encode("utf-8")) <= 170_000
                for request in review_requests
            ):
                raise AssertionError("Review probe did not exceed the MCP limit")
            review_bindings = {
                request.axis: _wait_review(
                    restarted,
                    request,
                    profile,
                    parent_agent_id=adopted_worker.agent_id,
                    deadline=deadline,
                )
                for request in review_requests
            }

            # A second fresh client must adopt both axes without another create/send.
            client = PaseoCliClient(executable=args.executable)
            final_adapter = PaseoRuntimeAdapter(client)
            readback_bindings = {
                request.axis: _wait_review(
                    final_adapter,
                    request,
                    profile,
                    parent_agent_id=adopted_worker.agent_id,
                    deadline=deadline,
                )
                for request in review_requests
            }
            for request in review_requests:
                original = review_bindings[request.axis]
                readback = readback_bindings[request.axis]
                if readback.agent_id != original.agent_id:
                    raise AssertionError(
                        f"{request.axis} restart adoption changed identity"
                    )
                if (
                    client.prompt_acceptance_count(
                        readback.agent_id,
                        request.to_prompt(),
                    )
                    != 1
                ):
                    raise AssertionError(
                        f"{request.axis} Prompt was not accepted exactly once"
                    )

            print(
                json.dumps(
                    {
                        "repository": repository_key,
                        "review_agents": {
                            axis: binding.agent_id
                            for axis, binding in readback_bindings.items()
                        },
                        "review_prompt_bytes": {
                            request.axis: len(request.to_prompt().text.encode("utf-8"))
                            for request in review_requests
                        },
                        "status": "passed",
                        "worker_agent": adopted_worker.agent_id,
                        "worker_prompt_bytes": len(worker_prompt.text.encode("utf-8")),
                    },
                    sort_keys=True,
                )
            )
        finally:
            try:
                records = client.find_by_labels({"gwo.repository": repository_key})
            except RuntimeAdapterError:
                records = ()
            for record in records:
                if record.archived:
                    continue
                try:
                    client.archive(record.agent_id)
                except RuntimeAdapterError as error:
                    print(
                        f"warning: could not archive {record.agent_id}: {error.code}",
                        file=sys.stderr,
                    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
