#!/usr/bin/env python3
"""Shared provider-neutral execution-contract vocabulary."""

ROLE_CATEGORIES = {
    "orchestrator": {"planning"},
    "intake": {"research"},
    "implementation": {"impl", "ui"},
    "review": {"audit"},
    "monitor": {"audit"},
}
VERIFICATION_CLASSES = {"fast", "standard", "strict"}
EXECUTION_MODES = {"inline", "paseo-agent"}

DEFAULT_MAX_ACTIVE_AGENTS_PER_CAMPAIGN = 6
DEFAULT_MAX_WORKER_SLOTS_PER_CAMPAIGN = 3
DEFAULT_MAX_REVIEW_SLOTS_PER_CAMPAIGN = 2
DEFAULT_MAX_ACTIVE_AGENTS_GLOBAL = 13
DEFAULT_MAX_DISPATCH_ATTEMPTS_PER_ISSUE = 3
DEFAULT_COORDINATOR_WAIT_SECONDS = 60
DEFAULT_WORKER_HEARTBEAT_SECONDS = 300
DEFAULT_WORKER_STALE_SECONDS = 900
DEFAULT_STALE_RECHECK_SECONDS = 900
