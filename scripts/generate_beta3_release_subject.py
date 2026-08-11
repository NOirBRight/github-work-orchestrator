"""Generate the fixed V8 external release subject manifest."""

from __future__ import annotations

import sys
from typing import Sequence

from beta3_release_subject import (
    generate_production_subject,
    write_production_subject_exclusive,
)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(argv) if argv is not None else sys.argv[1:]
    if arguments:
        return 1
    subject = generate_production_subject()
    binding = write_production_subject_exclusive(subject)
    try:
        print(subject.subject_digest)
        binding.assert_stable()
    finally:
        binding.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
