"""Standard Draft 2020-12 validation plus cross-record identity/evidence checks."""
from __future__ import annotations

from functools import lru_cache

from jsonschema import Draft202012Validator

from .common import DESIGN, PROTOCOL_VERSION, CONTENT_LABELS, BEHAVIOR_LABELS, DELETE_REASONS, EvaluationError, digest, read_json


@lru_cache(maxsize=1)
def schema() -> dict:
    value = read_json(DESIGN / "records.schema.json")
    Draft202012Validator.check_schema(value)
    return value


@lru_cache(maxsize=16)
def payload_schema(kind: str) -> dict:
    definitions = schema()["$defs"]
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "$defs": definitions,
            "$ref": f"#/$defs/{kind}"}


def validate_payload(kind: str, payload: dict) -> None:
    Draft202012Validator(payload_schema(kind)).validate(payload)


def record(kind: str, payload: dict | None, *, run_id: str, target_id: str | None,
           artifact_id: str | None, role: str, view: str, input_hash: str,
           version: str | None, status: str = "OK") -> dict:
    item = {"record_kind": kind, "protocol_version": PROTOCOL_VERSION, "run_id": run_id,
            "target_id": target_id, "artifact_id": artifact_id, "role_id": role, "view": view,
            "input_hash": input_hash, "scenario_version": version,
            "technical_status": status, "payload": payload}
    item["record_id"] = "R-" + digest(item)
    Draft202012Validator(schema()).validate(item)
    return item


def exact_ids(requested: list[str], returned: list[str]) -> None:
    if len(returned) != len(set(returned)) or len(requested) != len(set(requested)) or set(requested) != set(returned):
        raise EvaluationError("Missing, duplicated or unauthorized response IDs: " +
            str({"missing": sorted(set(requested)-set(returned)),
                 "unexpected": sorted(set(returned)-set(requested)),
                 "duplicated": sorted({x for x in returned if returned.count(x)>1})}))


def check_locators(locators: list[dict], sources: list[dict], access: list[dict] | None = None) -> None:
    source_map = {s["source_id"]: s for s in sources}
    for loc in locators:
        Draft202012Validator(payload_schema("locator")).validate(loc)
        source = source_map.get(loc["source_id"])
        if not source or loc["source_sha256"] != source["sha256"] or loc["pdf_page"] > source["pages"]:
            raise EvaluationError("Source/page/hash outside frozen source")
        if not loc["quote"] and not loc["region_description"].strip():
            raise EvaluationError("Locator needs quote or visual region")
        if loc["modality"] == "TEXT" and not loc["quote"]:
            raise EvaluationError("Text evidence requires a quotation")
        if access is not None:
            entries = [a for a in access if a.get("source_id") == loc["source_id"] and a.get("pdf_page") == loc["pdf_page"]]
            if not entries:
                raise EvaluationError("Evidence page was never accessed by this session")
            if loc["modality"] != "TEXT" and not any(a.get("image_sha256") for a in entries):
                raise EvaluationError("Visual evidence needs actual image access; text is insufficient")


def check_content(payload: dict, claims: list[dict], sources: list[dict], access: list[dict] | None = None) -> None:
    validate_payload("content", payload)
    ids = [c["claim_id"] for c in claims]
    cid = payload["claim_id"]
    if cid not in ids or any(x not in ids for x in payload["context_claim_ids"]):
        raise EvaluationError("Foreign claim ID")
    check_locators(payload["locators"], sources, access)
    label, reasons = payload["label"], set(payload["reasons"])
    if not payload["rationale"].strip():
        raise EvaluationError("Missing rationale")
    if label == "KEEP" and (not payload["locators"] or reasons != {"NONE"}):
        raise EvaluationError("KEEP requires support, not deletion/unresolved reasons")
    if label == "DELETE":
        if not reasons or not reasons <= DELETE_REASONS:
            raise EvaluationError("DELETE requires explicit permitted reasons; no-hit is unresolved")
        if not payload["locators"]:
            raise EvaluationError("DELETE requires source/scope evidence")
    if "NO_SUPPORT_FOUND" in reasons and label != "UNRESOLVED":
        raise EvaluationError("No support found is not confirmed error")
    if "REDUNDANT" in reasons:
        other = payload["duplicate_of"]
        if other not in ids or ids.index(other) >= ids.index(cid):
            raise EvaluationError("Redundancy must point to an earlier assertion in this artifact")
    elif payload["duplicate_of"] is not None:
        raise EvaluationError("Unexpected redundancy link")


def check_behavior(payload: dict, claims: list[dict], scenario: dict, sources: list[dict], access=None) -> None:
    validate_payload("behavior", payload)
    if payload["scenario_id"] != scenario["scenario_id"] or payload["checkpoint_id"] not in {
            c["checkpoint_id"] for c in scenario["checkpoints"]}:
        raise EvaluationError("Foreign checkpoint/scenario")
    ids = {c["claim_id"] for c in claims}
    if not set(payload["matched_claim_ids"]) <= ids:
        raise EvaluationError("Foreign matched claim")
    if payload["label"] == "RETAINED" and not payload["matched_claim_ids"]:
        raise EvaluationError("Given name/actor alone cannot retain behavior")
    if payload["label"] == "PARTIAL" and not payload["missing_elements"]:
        raise EvaluationError("PARTIAL needs missing elements")
    if payload["label"] == "DISTORTED" and not payload["distorted_relations"]:
        raise EvaluationError("DISTORTED needs an explicit error")
    if payload["distorted_relations"] and payload["label"] not in {"DISTORTED", "UNRESOLVED"}:
        raise EvaluationError("Known wrong relations cannot be hidden by matching a correct sentence")
    check_locators(payload["locators"], sources, access)


def check_review(payload: dict, source_records: list[dict]) -> None:
    validate_payload("review", payload)
    labels = {"SOURCE_REVIEW": {"ACCEPT", "REVISE", "UNRESOLVED"},
              "CONTENT_CLAIM": CONTENT_LABELS, "BEHAVIOR_CHECKPOINT": BEHAVIOR_LABELS}[payload["decision_family"]]
    if payload["blind_label"] not in labels or payload["final_label"] not in labels:
        raise EvaluationError("Wrong decision family label")
    if set(payload["source_record_ids"]) != {r["record_id"] for r in source_records}:
        raise EvaluationError("Review must link exactly its first decisions")
