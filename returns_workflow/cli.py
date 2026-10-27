"""Command-line entry points for generating and checking datasets."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .generator import DEFAULT_SEED, generate_dataset, verify_reproducibility
from .validation import validate_dataset

SCALES = {
    "starter": (300, 50, 80),
    "full": (30_000, 5_000, 8_000),
}


def _scale_values(arguments: argparse.Namespace) -> tuple[int, int, int]:
    custom = (arguments.orders, arguments.requests, arguments.lines)
    if any(value is not None for value in custom):
        if not all(value is not None for value in custom):
            raise ValueError("Custom scale requires --orders, --requests, and --lines together.")
        if any(value <= 0 for value in custom):
            raise ValueError("Custom counts must be positive integers.")
        return custom
    return SCALES[arguments.scale]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="returns-workflow",
        description="Generate, validate, and reproduce a synthetic returns dataset.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate", help="generate SQLite, CSV, JSON, and SQL exports")
    generate.add_argument("--scale", choices=SCALES, default="starter")
    generate.add_argument("--orders", type=int)
    generate.add_argument("--requests", type=int)
    generate.add_argument("--lines", type=int)
    generate.add_argument("--seed", type=int, default=DEFAULT_SEED)
    generate.add_argument("--output", type=Path, default=Path("data/generated"))

    validate = commands.add_parser("validate", help="run data integrity and business-rule checks")
    validate.add_argument("--input", type=Path, default=Path("data/generated"))

    reproduce = commands.add_parser(
        "verify-reproducibility", help="generate twice and compare every exported-file checksum"
    )
    reproduce.add_argument("--scale", choices=SCALES, default="starter")
    reproduce.add_argument("--orders", type=int)
    reproduce.add_argument("--requests", type=int)
    reproduce.add_argument("--lines", type=int)
    reproduce.add_argument("--seed", type=int, default=DEFAULT_SEED)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "generate":
            orders, requests, lines = _scale_values(arguments)
            summary = generate_dataset(
                arguments.output,
                order_count=orders,
                request_count=requests,
                return_line_count=lines,
                seed=arguments.seed,
            )
        elif arguments.command == "validate":
            summary = validate_dataset(arguments.input)
        else:
            orders, requests, lines = _scale_values(arguments)
            summary = verify_reproducibility(
                order_count=orders,
                request_count=requests,
                return_line_count=lines,
                seed=arguments.seed,
            )
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0 if summary.get("status", "passed") == "passed" or summary.get("reproducible") else 1
    except (OSError, ValueError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
