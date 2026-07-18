#!/usr/bin/env python3
"""Shared redaction checks for durable GWO request summaries."""

from __future__ import annotations

import re


# A drive path must start at a token boundary. Without the lookbehind, the
# trailing ``s:/`` in ``https://`` is incorrectly treated as a Windows drive.
WINDOWS_ABSOLUTE_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:[a-z]:[\\/]|\\\\[^\\/\s]+[\\/][^\\/\s]+)"
)
# URI bodies can contain slash-prefixed path components without naming a local
# filesystem path. Remove them before applying the general POSIX-path check.
# Require at least two scheme characters so a Windows drive written as
# ``C://Users/...`` remains available to the drive-path check below.
URI_RE = re.compile(
    r"(?i)\b[a-z][a-z0-9+.-]+://[^\s<>\"',;|()\[\]{}]+"
)
LOCAL_FILE_URI_RE = re.compile(r"(?i)\bfile:(?://)?[\\/]")
POSIX_ABSOLUTE_RE = re.compile(r"(?<![A-Za-z0-9._~%+-])/(?![/\s])")
SENSITIVE_RE = re.compile(
    r"(?i)(?:\bsk-[a-z0-9_-]{4,}|\bgithub_pat_[a-z0-9_]+|\bgh[pousr]_[a-z0-9]+|"
    r"\bbearer\s+[a-z0-9._~+/-]+=*|\bAKIA[0-9A-Z]{16}\b|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"\b(?:api[_ -]?key|token|password|secret)\s*[:=])"
)


def text_is_sensitive(value: str) -> bool:
    """Return whether text contains a credential shape or local absolute path."""

    if (
        LOCAL_FILE_URI_RE.search(value)
        or SENSITIVE_RE.search(value)
        or WINDOWS_ABSOLUTE_RE.search(value)
    ):
        return True
    without_uris = URI_RE.sub("", value)
    return bool(POSIX_ABSOLUTE_RE.search(without_uris))
