"""Command line entry point: ``veriops-eval`` / ``python -m veriops.evaluation.cli``.

Exits non-zero when any evaluation gate fails, so CI can block merges.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from veriops.evaluation.harness import DEFAULT_DATASET, load_dataset, run_evaluation
from veriops.shared.config import get_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the VeriOps agent evaluation suite")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=Path("reports/evaluation.json"))
    parser.add_argument("--case", action="append", help="run only these incident ids")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)

    cases = load_dataset(args.dataset)
    if args.case:
        cases = [c for c in cases if c.incident_id in set(args.case)]
    result = run_evaluation(cases, get_settings())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, default=str))

    print(f"{'case':10} {'scenario':34} {'status':22} {'root cause':22} {'action':22} result")
    for r in result["results"]:
        print(
            f"{r['incident_id']:10} {r['scenario']:34} {r['final_status']:22} {r['root_cause_category']!s:22} "
            f"{r['remediation_action']!s:22} {'PASS' if r['passed'] else 'FAIL: ' + '; '.join(r['failures'])}"
        )
    print("\nmetrics:")
    for key, value in result["metrics"].items():
        print(f"  {key:40} {value}")
    print("\ngates:")
    for key, ok in result["gates"].items():
        print(f"  {key:40} {'PASS' if ok else 'FAIL'}")
    print(f"\nprovider={result['provider']}  overall={'PASS' if result['passed'] else 'FAIL'}  report={args.output}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
