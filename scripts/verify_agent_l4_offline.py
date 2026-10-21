"""Run ONLY inspected L4 synthetic tests with networking blocked; no model calls."""
from pathlib import Path
import io
import json
import sys
import unittest

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from eval.l4_agent.protocol import network_blocked, new_run, save_new, versions


def main():
    run = new_run("offline-verification")
    stream = io.StringIO()
    with network_blocked():
        suite = unittest.TestLoader().discover(str(REPO / "tests/agent_l4"), pattern="test_*.py")
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    report = {"status": "PASS" if result.wasSuccessful() else "FAIL", "tests_run": result.testsRun,
              "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped),
              "network": "BLOCKED", "data_class": "MOCK_ONLY", "research_status": "NOT_RESEARCH_DATA",
              "real_model_calls": 0, "real_semantic_evaluation": "NOT_RUN", "formal_evaluation": "NOT_RUN",
              "versions": versions(), "test_log": stream.getvalue()}
    save_new(run / "verification.json", report)
    with (run / "verification.txt").open("x", encoding="utf-8") as f:
        f.write(stream.getvalue())
    print(stream.getvalue())
    print(json.dumps({k:v for k,v in report.items() if k not in {"test_log", "versions"}}, ensure_ascii=False, indent=2))
    print("REPORT", run / "verification.json")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
