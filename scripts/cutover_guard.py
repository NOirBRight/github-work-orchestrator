from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Callable, Sequence


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "orchestrator" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from gwo_v8.cutover_guard import (  # noqa: E402
    CutoverGuard,
    CutoverGuardError,
    CutoverGuardReport,
    CutoverGuardSources,
    CutoverSubject,
    JsonCutoverReadPorts,
    source_tree_digest,
)
from gwo_v8.plan_control_host import (  # noqa: E402
    CutoverGuardRequest,
    load_production_cutover_guard,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only GWO V8 cutover evidence")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--bundle", type=Path)
    mode.add_argument("--live", action="store_true")
    parser.add_argument("--repository")
    parser.add_argument("--control-branch")
    parser.add_argument("--target-branch")
    parser.add_argument("--source-writer-generation")
    parser.add_argument("--target-writer-generation")
    parser.add_argument("--store-generation")
    parser.add_argument("--source-commit")
    parser.add_argument("--package-root", type=Path)
    parser.add_argument("--install-root", dest="install_roots", type=Path, action="append", default=[])
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def _live_subject(args: argparse.Namespace) -> CutoverSubject:
    required = (
        "repository",
        "control_branch",
        "target_branch",
        "source_writer_generation",
        "target_writer_generation",
        "store_generation",
        "source_commit",
        "package_root",
    )
    if any(getattr(args, name) in (None, "") for name in required):
        raise CutoverGuardError(
            "CUTOVER_GUARD_UNAVAILABLE",
            "--live requires every subject and package-root option",
        )
    roots = tuple(args.install_roots)
    if len(roots) != 3 or tuple(root.parent.name for root in roots) != (
        ".agents",
        ".codex",
        ".claude",
    ):
        raise CutoverGuardError(
            "CUTOVER_GUARD_UNAVAILABLE",
            "--live requires exactly three ordered .agents/.codex/.claude roots",
        )
    return CutoverSubject(
        repository=args.repository,
        control_branch=args.control_branch,
        target_branch=args.target_branch,
        source_writer_generation=args.source_writer_generation,
        target_writer_generation=args.target_writer_generation,
        store_generation=args.store_generation,
        source_commit=args.source_commit,
        source_tree_digest=source_tree_digest(args.package_root),
        production_entry_refs=(
            "gwo_v8.plan_control_host:ProductionPlanControlStartHost.start",
            "gwo_v8.execution_kernel:advance",
            "gwo_v8.execution_kernel:inspect",
        ),
    )


def _payload(report: CutoverGuardReport, evidence_mode: str) -> dict[str, object]:
    payload = dict(report.canonical())
    payload["evidence_mode"] = evidence_mode
    payload["activation_performed"] = False
    return payload


def _print_report(
    report: CutoverGuardReport,
    *,
    evidence_mode: str,
    json_output: bool,
) -> None:
    payload = _payload(report, evidence_mode)
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return
    print(
        " ".join(
            (
                f"decision={payload['decision']}",
                f"evidence_mode={payload['evidence_mode']}",
                f"repository={payload['repository']}",
                f"subject_digest={payload['subject_digest']}",
                f"readback_digest={payload['readback_digest']}",
                "activation_performed=false",
            )
        )
    )
    for check in payload["checks"]:
        print(
            f"check={check['check_id']} passed={check['passed']} "
            f"observed_digest={check['observed_digest']}"
        )
    for blocker in payload["blockers"]:
        print(
            f"blocker={blocker['code']} check_id={blocker['check_id']} "
            f"detail={blocker['detail']}"
        )
    receipt = payload["receipt"]
    print(
        "receipt_digest="
        + ("none" if receipt is None else str(receipt["receipt_digest"]))
    )


def _print_error(code: str, detail: str, *, json_output: bool) -> None:
    payload = {"error_code": code, "detail": detail}
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    else:
        print(f"error_code={code} detail={detail}")


def main(
    argv: Sequence[str] | None = None,
    *,
    guard_factory: Callable[[CutoverGuardSources], CutoverGuard] | None = None,
    live_host_factory: Callable[[CutoverGuardRequest], object] | None = None,
) -> int:
    try:
        args = parse_args(argv)
    except SystemExit:
        _print_error("CUTOVER_GUARD_UNAVAILABLE", "command-line arguments are invalid", json_output=True)
        return 3
    if args.bundle is not None:
        try:
            ports = JsonCutoverReadPorts.load(args.bundle)
            guard = (guard_factory or CutoverGuard)(ports.sources())
            report = guard.evaluate(ports.subject)
        except Exception as error:
            _print_error(
                "CUTOVER_BUNDLE_INVALID",
                "offline readback bundle is malformed or unavailable",
                json_output=args.json_output,
            )
            return 3
        _print_report(report, evidence_mode="readback_bundle", json_output=args.json_output)
        return 0 if report.decision == "GO" else 2
    try:
        subject = _live_subject(args)
        roots = tuple(args.install_roots)
        request = CutoverGuardRequest(
            subject=subject,
            package_root=args.package_root,
            install_roots=(roots[0], roots[1], roots[2]),
        )
        host = (live_host_factory or load_production_cutover_guard)(request)
        report = host.check(subject)
    except Exception as error:
        _print_error(
            "CUTOVER_GUARD_UNAVAILABLE",
            "live composed read ports are unavailable",
            json_output=args.json_output,
        )
        return 3
    _print_report(report, evidence_mode="live_composed_ports", json_output=args.json_output)
    return 0 if report.decision == "GO" else 2


if __name__ == "__main__":
    raise SystemExit(main())
