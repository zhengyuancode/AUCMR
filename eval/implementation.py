"""Freeze the actual evaluator implementation, independently of protocol labels."""
import hashlib
from pathlib import Path


def implementation_manifest(layer: str) -> dict[str, str]:
    root = Path(__file__).resolve().parents[1]
    packages = {"l2": "l2_independent", "l3": "agent_evaluation", "l4": "l4_agent"}
    common = {"eval/implementation.py", "eval/evaluation_runtime.py", "eval/serialized_slots.py",
              "eval/agent_evaluation/__init__.py", "eval/agent_evaluation/common.py",
              "eval/agent_evaluation/budget.py", "eval/agent_evaluation/source_access.py"}
    if layer == "legacy":
        paths = {"eval/implementation.py", "eval/legacy_contracts.py", "eval/compute_metrics.py",
                 "eval/l2_judge.py", "eval/run_experiments.py", "config/settings.py", "run.py"}
    else:
        package = root / "eval" / packages[layer]
        paths = common | {p.relative_to(root).as_posix() for p in package.rglob("*.py")}
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sorted(paths)}
