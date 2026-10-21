"""Focused, network-blocked verification. Never runs repository-wide tests or real judges."""
from __future__ import annotations

import ast
import io
import json
from pathlib import Path
import socket
import sys
import unittest

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from eval.l2_independent.common import file_hash, new_run, read_json, write_json
from eval.l2_independent.inputs import load_config
from eval.l2_independent.bindings import prepare_inputs
from eval.l2_independent.offline import (ALLOWED_INFRASTRUCTURE, FORBIDDEN_IMPORTS,
                                         OfflineViolation, offline_guard)
from eval.l2_independent.runner import inventory, reexport, run_mock


def protected_hashes(config):
    paths = [REPO / "eval/l2_judge.py", REPO / "eval/compute_metrics.py",
             REPO / "config/agent_evaluation_l3.json"]
    paths.extend(p for p in (REPO / "eval/agent_evaluation").rglob("*")
                 if p.is_file() and "__pycache__" not in p.parts)
    paths.extend(REPO / b["path"] for b in config["input_bindings"])
    return {p.relative_to(REPO).as_posix(): file_hash(p) if p.is_file() else None for p in paths}


def static_checks():
    sources = sorted((REPO / "eval/l2_independent").rglob("*.py"))
    sources += sorted((REPO / "tests/agent_l2_independent").rglob("*.py"))
    sources.append(Path(__file__).resolve())
    imported_forbidden = []
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 10))
        if "eval/l2_independent" not in path.as_posix():
            continue
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            for name in names:
                if name not in ALLOWED_INFRASTRUCTURE and any(
                    name == forbidden or name.startswith(forbidden + ".") for forbidden in FORBIDDEN_IMPORTS):
                    imported_forbidden.append({"file": str(path), "import": name})
    return {"python_310_syntax_files": len(sources), "forbidden_imports": imported_forbidden,
            "status": "PASS" if not imported_forbidden else "FAIL"}


def main():
    result_dir = new_run("verify")
    config = load_config(REPO / "config/agent_evaluation_l2_independent.json")
    before = protected_hashes(config)
    stream = io.StringIO()
    failure = None
    inv = mock = exported = None
    tests = None
    checks = None
    with offline_guard() as audit:
        try:
            blocked_probe = False
            try:
                socket.create_connection(("127.0.0.1", 9))
            except OfflineViolation:
                blocked_probe = True
            if not blocked_probe:
                raise AssertionError("Offline guard did not block its network self-test")
            checks = static_checks()
            suite = unittest.defaultTestLoader.discover(
                str(REPO / "tests/agent_l2_independent"), pattern="test_*.py")
            tests = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
            if tests.wasSuccessful() and checks["status"] == "PASS":
                local_binding = prepare_inputs(REPO / "config/agent_evaluation_l2_independent.json", repo=REPO)
                inv = inventory(Path(local_binding["config"]))
                mock = run_mock()
                exported = reexport(mock["run_dir"])
        except Exception as exc:
            failure = {"type": type(exc).__name__, "message": str(exc)}
    after = protected_hashes(config)
    unchanged = before == after
    passed = (failure is None and tests is not None and tests.wasSuccessful()
              and checks["status"] == "PASS" and unchanged and inv
              and inv["binding_validation"] == "PASS" and mock["status"] == "PASS"
              and exported["status"] == "PASS")
    report = {
        "status": "PASS" if passed else "FAIL",
        "verification_run": str(result_dir), "python": sys.version, "interpreter": sys.executable,
        "tests_run": tests.testsRun if tests else 0,
        "test_failures": len(tests.failures) if tests else None,
        "test_errors": len(tests.errors) if tests else None,
        "test_skips": len(tests.skipped) if tests else None,
        "static_checks": checks, "failure": failure,
        "network_guard": {"self_test_blocked": blocked_probe, **audit,
                          "note": "The deliberate self-test was blocked before any connection; focused tests also use guards."},
        "protected_files_unchanged": unchanged, "protected_file_count": len(before),
        "inventory": inv, "mock": mock, "reexport": exported,
        "real_model_calls": 0, "real_semantic_evaluation": "NOT_RUN", "formal_experiment": "NOT_RUN",
        "judge_validity": "NOT_ESTABLISHED", "aucmr_effectiveness": "NOT_ESTABLISHED",
        "research_data_status": "NOT_RESEARCH_DATA",
    }
    with (result_dir / "tests.log").open("x", encoding="utf-8") as handle:
        handle.write(stream.getvalue())
    write_json(result_dir / "protected_hashes.json", {"before": before, "after": after})
    sources = list((REPO / "eval/l2_independent").rglob("*.py"))
    sources += list((REPO / "tests/agent_l2_independent").rglob("*.py"))
    sources += [Path(__file__).resolve(), REPO / "config/agent_evaluation_l2_independent.json",
                REPO / "eval/evaluation_runtime.py"]
    doc = REPO / "docs/AGENT_L2_INDEPENDENT_RUNBOOK.md"
    if doc.is_file():
        sources.append(doc)
    write_json(result_dir / "code_manifest.json",
               {p.relative_to(REPO).as_posix(): file_hash(p) for p in sorted(sources)})
    write_json(result_dir / "report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
