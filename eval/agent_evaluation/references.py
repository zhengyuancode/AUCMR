"""Freeze independently verified source objects; invalidate every method on correction."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from .common import EvaluationError, append_jsonl, digest, read_json, write_json
from .schemas import validate_payload, check_locators


def freeze(scope: dict, proposal: dict, reviews: list[dict], sources: list[dict], *, revision=1) -> dict:
    validate_payload("scope", scope)
    validate_payload("scene_proposal", proposal)
    if proposal["scope_id"] != scope["scope_id"]:
        raise EvaluationError("Wrong scope binding")
    scenarios = proposal["scenarios"]
    kinds = [s["kind"] for s in scenarios]
    if kinds.count("CORE") > 1 or kinds.count("BRANCH") > 1:
        raise EvaluationError("At most one core and one branch")
    if ("CORE" in kinds) != (proposal["core_status"] == "PROPOSED"):
        raise EvaluationError("Core status inconsistent")
    if ("BRANCH" in kinds) != (proposal["branch_status"] == "PROPOSED"):
        raise EvaluationError("Branch status inconsistent")
    objects = {scope["scope_id"]: scope["locators"]}
    for scenario in scenarios:
        validate_payload("scenario", scenario)
        if not scenario["selection_reason"].strip():
            raise EvaluationError("Source-based selection reason required")
        objects[scenario["scenario_id"]] = [loc for cp in scenario["checkpoints"] for loc in cp["locators"]]
        for cp in scenario["checkpoints"]:
            if cp["checkpoint_id"] in objects:
                raise EvaluationError("Duplicate checkpoint ID")
            objects[cp["checkpoint_id"]] = cp["locators"]
    accepted = set()
    for review in reviews:
        validate_payload("source_review", review)
        if review["object_id"] not in objects:
            raise EvaluationError("Source review refers to another proposal")
        check_locators(review["locators"], sources)
        if review["decision"] == "ACCEPT":
            if not review["locators"]:
                raise EvaluationError("Source acceptance without evidence")
            accepted.add(review["object_id"])
    for oid, locators in objects.items():
        check_locators(locators, sources)
    if scenarios and not set(objects) <= accepted:
        raise EvaluationError("Cannot freeze unverified source objects")
    # A scope unresolved after source verification can be frozen as unresolved for content audit.
    if scope["status"] != "SCOPE_UNRESOLVED" and scope["scope_id"] not in accepted:
        raise EvaluationError("Unverified scope")
    frozen = {"scope_card": deepcopy(scope), "scenarios": deepcopy(scenarios),
              "core_status": "ESTABLISHED" if "CORE" in kinds else "CORE_UNESTABLISHED",
              "branch_status": "ESTABLISHED" if "BRANCH" in kinds else proposal["branch_status"],
              "unresolved": proposal["unresolved"], "source_reviews": deepcopy(reviews),
              "proposal": deepcopy(proposal), "revision": revision}
    frozen["scenario_version"] = f"v{revision}-" + digest(frozen)
    return frozen


def require_version(reference: dict, version: str | None) -> None:
    if not version or reference["scenario_version"] != version:
        raise EvaluationError("Frozen reference version mismatch")


def verify_frozen(reference: dict) -> None:
    body = {k: v for k, v in reference.items() if k not in {
        "scenario_version", "original_proposal_record_ids", "review_record_ids"}}
    if reference["scenario_version"] != f"v{reference['revision']}-" + digest(body):
        raise EvaluationError("Frozen reference was altered without a versioned correction")


def correct_reference(run_dir: Path, target_id: str, new_reference: dict, methods: list[str]) -> None:
    path = run_dir / "scenario_freeze.json"
    refs = read_json(path)
    old = refs[target_id]
    if new_reference["revision"] != old["revision"] + 1 or new_reference["scenario_version"] == old["scenario_version"]:
        raise EvaluationError("Reference correction requires incremented version and new content hash")
    append_jsonl(run_dir / "reference_history.jsonl", {"target_id": target_id, "reference": old})
    for method in methods:
        append_jsonl(run_dir / "invalidations.jsonl", {"target_id": target_id, "method": method,
            "old_version": old["scenario_version"], "new_version": new_reference["scenario_version"],
            "status": "REEVALUATION_REQUIRED", "includes_mechanism_variants": True})
    refs[target_id] = new_reference
    write_json(path, refs)
