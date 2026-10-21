"""Small, standard-library-only helpers for the independent L2 module."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

SLOTS = ("goal", "preconditions", "trigger", "main_flow",
         "alternative_flow", "exception_flow", "postconditions")
PROTOCOL_VERSION = "AUCMR_L2_INDEPENDENT_1.0.0"
PROMPT_VERSION = "whole_uc_relations_1.0.0"
RECORD_VERSION = "L2_INDEPENDENT_RECORD_1"
REPO = Path(__file__).resolve().parents[2]
OUTPUT_ROOT = REPO / "outputs" / "agent_l2_independent"
COMPLETE = {"COMPLETE_WITH_CONFLICTS", "COMPLETE_NO_CONFLICTS"}


class L2Error(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    """Exclusive creation. A run can never silently replace an existing artifact."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def confined(root, relative):
    root = Path(root).resolve()
    value = Path(relative)
    if value.is_absolute() or ".." in value.parts:
        raise L2Error("Expected a repository-relative path without traversal")
    path = (root / value).resolve()
    if not path.is_relative_to(root):
        raise L2Error("Path escapes repository")
    return path


def new_run(kind, output_root=None):
    if kind not in {"inventory", "mock", "export", "verify", "run", "binding"}:
        raise L2Error("Unknown run kind")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + kind + "-" + uuid4().hex[:8]
    output_root = Path(output_root) if output_root is not None else OUTPUT_ROOT
    root = output_root.resolve()
    if root != output_root.absolute():
        raise L2Error("Output root must not redirect through a symlink")
    path = root / run_id
    path.mkdir(parents=True, exist_ok=False)
    return path


def reject_secrets(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in {"api_key", "authorization", "password", "secret", "access_token"}:
                raise L2Error("Literal credentials are forbidden; bind an environment variable later")
            reject_secrets(child)
    elif isinstance(value, list):
        for child in value:
            reject_secrets(child)
