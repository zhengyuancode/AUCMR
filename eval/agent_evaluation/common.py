"""Deterministic identifiers, safe local persistence and protocol constants."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import PROTOCOL_VERSION

PACKAGE = Path(__file__).resolve().parent
REPO = PACKAGE.parents[1]
DESIGN = PACKAGE / "design"
SLOTS = ("goal", "preconditions", "trigger", "main_flow", "alternative_flow",
         "exception_flow", "postconditions")
VALID = {"PRESENT", "VALID_EMPTY", "ABSTAINED"}
CONTENT_LABELS = {"KEEP", "DELETE", "UNRESOLVED"}
BEHAVIOR_LABELS = {"RETAINED", "PARTIAL", "MISSING", "DISTORTED", "UNRESOLVED"}
DELETE_REASONS = {"SOURCE_CONTRADICTION", "RELATION_ERROR", "OUT_OF_SCOPE", "REDUNDANT"}
SEED = 297154423


class EvaluationError(ValueError):
    """A recorded protocol/structure/permission failure, never a semantic label."""


class Blocked(EvaluationError):
    """A prerequisite or budget is not satisfied; do not issue a request."""


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # A unique temporary file also permits separate unrelated tasks to persist safely.
    import tempfile
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                     delete=False, suffix=".tmp") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(canonical(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line] if path.exists() else []


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def confined(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise EvaluationError("Path escapes authorized root")
    return path


def ordered(ids: list[str], purpose: str, seed: int = SEED) -> list[str]:
    return sorted(ids, key=lambda item: hashlib.sha256(f"{seed}|{purpose}|{item}".encode()).hexdigest())


def sample_strata(items: list[dict], purpose: str, fraction: float = .1) -> dict:
    import math
    groups: dict[tuple, list[str]] = {}
    for item in items:
        groups.setdefault((item["project_id"], item["decision_family"]), []).append(item["stable_id"])
    rows = []
    for key, ids in sorted(groups.items()):
        if len(ids) != len(set(ids)):
            raise EvaluationError("Duplicate sampling unit")
        chosen = ordered(ids, purpose)[:max(1, math.ceil(len(ids) * fraction))]
        rows.append({"project_id": key[0], "decision_family": key[1], "population": sorted(ids),
                     "selected": chosen, "count": len(chosen), "fraction": len(chosen) / len(ids)})
    return {"seed": SEED, "purpose": purpose, "algorithm": "SHA256(seed|stream|stable_id)", "strata": rows}


def verify_design() -> dict:
    manifest = read_json(DESIGN / "design_manifest.json")
    checks = []
    for entry in manifest["files"]:
        path = confined(DESIGN, entry["path"])
        ok = path.is_file() and path.stat().st_size == entry["bytes"] and file_hash(path) == entry["sha256"]
        checks.append({"file": entry["path"], "ok": ok})
    if not all(item["ok"] for item in checks):
        raise EvaluationError("Design hash mismatch")
    return {"protocol_version": PROTOCOL_VERSION, "manifest_sha256": file_hash(DESIGN / "design_manifest.json"),
            "checks": checks}
