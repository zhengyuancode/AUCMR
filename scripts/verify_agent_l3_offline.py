"""Run reproducible offline acceptance and save actual command output. No live option."""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from eval.agent_evaluation.common import digest, file_hash, read_json, read_jsonl, write_json
from eval.agent_evaluation.transport import deny_network


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, default=REPO/"outputs/agent_evaluation/l3-offline-20261021")
    args = parser.parse_args()
    run = args.run_dir.resolve()
    if not run.is_relative_to((REPO/"outputs/agent_evaluation").resolve()):
        parser.error("Invalid run directory")
    evidence = run/"evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONUTF8="1")
    commands = [
        ["inventory", "--config", "config/agent_evaluation_l3.json", "--run-dir", str(run)],
        ["validate", "--run-dir", str(run), "--offline"],
        ["fixtures", "--run-dir", str(run), "--transport", "mock"],
        ["plan-cost", "--run-dir", str(run)],
    ]
    results = []
    for arguments in commands:
        command = [sys.executable, "-B", "-m", "eval.agent_evaluation", *arguments]
        result = subprocess.run(command, cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8")
        (evidence/(arguments[0]+".txt")).write_text(result.stdout + result.stderr, encoding="utf-8")
        results.append({"command": command, "returncode": result.returncode, "output": str(evidence/(arguments[0]+".txt"))})
        print(arguments[0], "PASS" if result.returncode == 0 else "FAIL", flush=True)
    stream = io.StringIO()
    seen = []
    class Captured(unittest.TextTestResult):
        def addSuccess(self, test):
            super().addSuccess(test)
            seen.append({"test": test.id(), "status": "PASS"})
        def addFailure(self, test, err):
            super().addFailure(test, err)
            seen.append({"test": test.id(), "status": "FAIL"})
        def addError(self, test, err):
            super().addError(test, err)
            seen.append({"test": test.id(), "status": "ERROR"})
    with deny_network():
        suite = unittest.defaultTestLoader.discover(str(REPO/"tests/agent_evaluation"))
        outcome = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=Captured).run(suite)
    (evidence/"unittest.txt").write_text(stream.getvalue(), encoding="utf-8")
    codes = {}
    for row in seen:
        name = row["test"].split(".")[-1]
        if name.startswith("test_O"):
            codes[name[5:8]] = row
    schema_records = 0
    from jsonschema import Draft202012Validator
    from eval.agent_evaluation.schemas import schema
    validator = Draft202012Validator(schema())
    for name in ("records.jsonl", "call_ledger.jsonl", "reviews_C.jsonl"):
        for r in read_jsonl(run/"fixtures/mock"/name):
            validator.validate(r)
            schema_records += 1
    before_path = REPO/".local-checks/l3-protected-before.json"
    protected = []
    if before_path.exists():
        protected = [{"file": p, "unchanged": file_hash(REPO/p) == h} for p,h in read_json(before_path).items()]
    # This audit reads only structural/hash fields from real inputs.
    manifest = read_json(run/"input_manifest.json")
    issues = [{k: a[k] for k in ("path", "target_id", "method", "state", "issues", "omitted_slots")}
              for a in manifest["artifacts"] if a["state"] not in {"PRESENT", "VALID_EMPTY"}]
    write_json(run/"input_issues.json", issues)
    fixture_report = read_json(run/"fixtures/mock/fixture_report.json")
    passed = outcome.wasSuccessful() and len(codes) == 24 and all(r["returncode"] == 0 for r in results) and fixture_report["failed"] == 0 and all(p["unchanged"] for p in protected)
    report = {"status": "PASS" if passed else "FAIL", "acceptance": codes, "tests": seen,
        "tests_run": outcome.testsRun, "commands": results, "standard_schema_records_validated": schema_records,
        "protected_files": protected, "input_summary": manifest["summary"], "input_drift": manifest["input_drift"],
        "fixtures_passed": fixture_report["passed"], "fixtures_failed": fixture_report["failed"],
        "real_model_calls": 0, "model_cost_cny": 0, "source_semantic_verification": "NOT_RUN",
        "model_fixture_tests": "NOT_RUN", "pilot": "NOT_RUN", "formal_experiment": "NOT_RUN", "scientific_results": "NOT_ESTABLISHED"}
    write_json(run/"offline_acceptance.json", report)
    print(json.dumps({k:v for k,v in report.items() if k not in {"acceptance", "tests", "commands", "protected_files"}}, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
