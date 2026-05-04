#!/usr/bin/env python
"""Convert our MBPP JSONL records into AFlow's expected schema.

Our `data/raw/mbpp/{train,test}.jsonl` ship with:
    {task_id, prompt, code, test_list, test_setup_code}

AFlow's `MBPPBenchmark.evaluate_problem` reads:
    {task_id, prompt, code, entry_point, test}

where `entry_point` is the function name being tested and `test` is a
single string with all asserts joined by newlines. This script extracts
`entry_point` from the first `assert <name>(` token and joins
`test_list` into a `test` string.

Usage:
    python scripts/prepare_aflow_mbpp_data.py \
        --input data/raw/mbpp/test.jsonl \
        --output external/AFlow/data/datasets/mbpp_test.jsonl

Reusable for any MBPP-format JSONL → AFlow conversion.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


_ENTRY_POINT_RE = re.compile(r"assert\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")


def extract_entry_point(test_list: list[str], code: str) -> str:
    for assertion in test_list or []:
        m = _ENTRY_POINT_RE.search(assertion)
        if m:
            return m.group(1)
    # Fallback: parse the first `def name(` from the reference solution.
    m = re.search(r"def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", code or "")
    if m:
        return m.group(1)
    raise ValueError(f"could not extract entry point; test_list={test_list!r}")


def convert(record: dict) -> dict:
    test_list = record.get("test_list") or []
    test_setup = record.get("test_setup_code") or ""
    entry = extract_entry_point(test_list, record.get("code", ""))
    # AFlow's MBPPBenchmark.check_solution does:
    #     exec(test, global_dict); check = global_dict["check"]; check()
    # so `test` must define a zero-arg `check()` that exec's its asserts
    # against the entry-point function (already in the same global_dict).
    indent = "    "
    body_lines = []
    if test_setup:
        for line in test_setup.splitlines():
            body_lines.append(indent + line)
    for assertion in test_list:
        body_lines.append(indent + assertion)
    body = "\n".join(body_lines) if body_lines else indent + "pass"
    test_str = "def check():\n" + body
    out = {
        "task_id": record["task_id"],
        "prompt": record["prompt"],
        "code": record.get("code", ""),
        "entry_point": entry,
        "test": test_str,
    }
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    n_in = 0
    n_out = 0
    n_skipped = 0
    with args.input.open("r", encoding="utf-8") as fin, args.output.open("w", encoding="utf-8") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            n_in += 1
            try:
                rec = json.loads(line)
                conv = convert(rec)
            except Exception as exc:
                n_skipped += 1
                print(f"skip {n_in}: {exc}", file=sys.stderr)
                continue
            fout.write(json.dumps(conv) + "\n")
            n_out += 1
    print(f"{args.input} -> {args.output}: in={n_in} out={n_out} skipped={n_skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
