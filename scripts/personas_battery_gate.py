#!/usr/bin/env python3
"""HU-2774 battery aggregate gate.

Runs AFTER the N conversation tasks of a battery flow. Each conversation task
runs with ``allowFailed: true`` so a failing run never aborts the remaining
runs (r8 finding: the 00:30Z battery stopped after run 1 because the evaluator
exit code failed the flow before runs 2-3 could execute).

This gate then enforces the DONE bar in one place: every run JSON must exist
(missing = infra failure) and carry ``verdict.pass == true``. Exit 0 iff the
whole battery passed; exit 1 with a per-run summary otherwise.
"""
import argparse
import json
import sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out-dir', default='/root/repos/huible/runs/hu2774')
    ap.add_argument('run_ids', nargs='+', help='run ids, in battery order')
    args = ap.parse_args()

    rows, all_pass = [], True
    for rid in args.run_ids:
        scenario_dir = Path(args.out_dir)
        # run ids are hu2774-<round>-<scenario>-<n>; scenario dirs group them
        matches = list(scenario_dir.glob(f'*/{rid}.json'))
        path = matches[0] if matches else None
        if path is None:
            rows.append((rid, 'MISSING', 'no result JSON (infra failure)'))
            all_pass = False
            continue
        try:
            data = json.loads(path.read_text())
        except Exception as e:
            rows.append((rid, 'CORRUPT', str(e)))
            all_pass = False
            continue
        verdict = data.get('verdict') or {}
        failed = verdict.get('failed') or []
        if verdict.get('pass') is True:
            rows.append((rid, 'PASS', f"{verdict.get('turns')} turns"))
        else:
            rows.append((rid, 'FAIL', ','.join(failed) or 'unknown'))
            all_pass = False

    width = max(len(r[0]) for r in rows) if rows else 0
    for rid, state, note in rows:
        print(f"{rid:<{width}}  {state:<8}  {note}")
    print(f"BATTERY: {'PASS' if all_pass else 'FAIL'} "
          f"({sum(1 for r in rows if r[1] == 'PASS')}/{len(rows)} runs passed)")
    return 0 if all_pass else 1


if __name__ == '__main__':
    sys.exit(main())
