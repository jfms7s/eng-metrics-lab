"""quality-report command line: one subcommand per task."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from quality_report import ratchet
from quality_report import report as report_run

BASELINE_SCHEMA = 1


def _load_json(path: str) -> dict:
    return json.loads(Path(path).read_text())


def _cmd_ratchet(args: argparse.Namespace) -> int:
    try:
        report = _load_json(args.report)
    except (OSError, ValueError) as exc:
        print(f"error: cannot read report {args.report}: {exc}", file=sys.stderr)
        return 2

    baseline = None
    if Path(args.baseline).is_file():
        try:
            baseline = _load_json(args.baseline)
        except (OSError, ValueError) as exc:
            print(f"error: cannot read baseline {args.baseline}: {exc}", file=sys.stderr)
            return 2
        if baseline.get("schema") != BASELINE_SCHEMA:
            print(f"error: unsupported baseline schema {baseline.get('schema')!r} "
                  f"(expected {BASELINE_SCHEMA})", file=sys.stderr)
            return 2

    outcome = ratchet.check(report, baseline)
    for v in outcome["violations"]:
        print(f"{v['field']}: {v['current']} exceeds baseline {v['baseline']} "
              f"(tolerance {v['tolerance']})")
    print(f"ratchet: {outcome['result']}")
    if (outcome["result"] == "pass" and report.get("branch") == "main"
            and ratchet.can_tighten(report, baseline)):
        print(f"baseline can be tightened: quality-report baseline --from {args.report} "
              "> .quality-baseline.json")
    return 1 if outcome["result"] == "fail" else 0


def _cmd_baseline(args: argparse.Namespace) -> int:
    try:
        report = _load_json(args.source)
    except (OSError, ValueError) as exc:
        print(f"error: cannot read report {args.source}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(ratchet.make_baseline(report, datetime.now(UTC)), indent=2))
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    config = Path(args.config) if args.config else None
    report_run.run_and_print(Path.cwd(), Path(args.out), config)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quality-report")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ratchet", help="fail if the report is worse than the baseline")
    p.add_argument("--baseline", required=True)
    p.add_argument("--report", required=True)
    p.set_defaults(func=_cmd_ratchet)

    p = sub.add_parser("baseline", help="print a baseline from a report")
    p.add_argument("--from", dest="source", required=True, metavar="PATH")
    p.set_defaults(func=_cmd_baseline)

    p = sub.add_parser("run", help="measure the current directory and write the report")
    p.add_argument("--config", metavar="PATH")
    p.add_argument("--out", required=True, metavar="PATH")
    p.set_defaults(func=_cmd_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
