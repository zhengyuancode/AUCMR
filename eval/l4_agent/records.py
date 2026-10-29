"""Strict response validation; absent or invalid judgments never become scores."""
from __future__ import annotations

import json
from copy import deepcopy
from .protocol import SLOTS, L4Error, digest
from eval.evaluation_response_format import normalize_json_envelope
from .contract import PCS_FIELDS, CHECK_FIELDS, PRC_FIELDS, MODIFICATION_FIELDS, FINDINGS, KINDS


class InvalidResponse(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise InvalidResponse(message)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def validate_refs(refs: list, accesses: list[dict], *, required=True) -> None:
    require(isinstance(refs, list) and (bool(refs) or not required), "MISSING_SOURCE_REFERENCE")
    for ref in refs:
        require(isinstance(ref, dict), "INVALID_SOURCE_REFERENCE")
        access = next((a for a in accesses if a["access_id"] == ref.get("access_id")), None)
        require(access is not None and access["operation"] == "source_page" and access["result"]["status"] == "OK", "UNACCESSED_PAGE")
        args, result = access["arguments"], access["result"]
        require(ref.get("source_id") == args.get("source_id") and type(ref.get("pdf_page")) is int and
                ref["pdf_page"] == args.get("pdf_page"), "SOURCE_LOCATION_MISMATCH")
        if ref.get("kind") == "TEXT":
            # PDF line wrapping is layout, not a different quotation. Preserve
            # words, punctuation, case and order; never use fuzzy matching.
            require(not args.get("image", False) and text(ref.get("quote")) and
                    " ".join(ref["quote"].split()) in " ".join(result.get("text", "").split()),
                    "QUOTE_NOT_ON_ACCESSED_PAGE")
        elif ref.get("kind") == "IMAGE":
            require(args.get("image") is True and bool(result.get("image_base64")) and text(ref.get("description")), "IMAGE_NOT_ACCESSED")
        else:
            raise InvalidResponse("INVALID_REFERENCE_KIND")


def no_duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidResponse("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def parse_response(raw: str) -> dict:
    raw, _ = normalize_json_envelope(raw)
    try:
        data = json.loads(raw, object_pairs_hook=no_duplicate_pairs,
                          parse_constant=lambda s: (_ for _ in ()).throw(InvalidResponse("NONFINITE_NUMBER")))
    except (ValueError, TypeError) as exc:
        raise InvalidResponse("INVALID_JSON_RESPONSE") from exc
    require(isinstance(data, dict), "RESPONSE_NOT_OBJECT")
    return data


def empty_score() -> dict:
    return {"score": 0, "status": "EMPTY_SLOT", "reason": "有效空槽按固定 PCS 口径记 0。",
            "checks": [], "unresolved": [], "validation_errors": []}


def absent(status: str, reason: str) -> dict:
    return {"score": None, "status": status, "reason": reason, "checks": [], "unresolved": [], "validation_errors": []}


def validate_pcs(value, slot: str, artifact: dict, accesses: list) -> dict:
    require(isinstance(value, dict), "MISSING_SLOT_RESPONSE")
    require(set(value) == PCS_FIELDS, "INVALID_PCS_FIELDS")
    require(text(value["reason"]), "MISSING_SPECIFIC_REASON")
    require(isinstance(value["unresolved"], list) and all(text(x) for x in value["unresolved"]), "INVALID_UNRESOLVED")
    require(isinstance(value["checks"], list), "INVALID_CHECKS")
    expected = {c["claim_id"] for c in artifact["slots"][slot]["claims"]}
    found = []
    for check in value["checks"]:
        require(isinstance(check, dict) and set(check) == CHECK_FIELDS, "INVALID_CHECK_FIELDS")
        require(check["claim_id"] in expected, "INVALID_ASSERTION_ID")
        found.append(check["claim_id"])
        require(text(check["rationale"]), "MISSING_ASSERTION_REASON")
        require(check["finding"] in FINDINGS, "INVALID_FINDING")
        validate_refs(check["source_refs"], accesses, required=check["finding"] != "UNRESOLVED")
    require(set(found) == expected and len(found) == len(expected), "MISSING_OR_DUPLICATE_ASSERTION_CHECK")
    score = value["score"]
    if value["status"] == "UNRESOLVED":
        require(score is None and value["unresolved"], "UNRESOLVED_MUST_BE_NULL_WITH_REASON")
    else:
        require(value["status"] == "SCORED" and type(score) is int and 0 <= score <= 3, "PCS_OUT_OF_RANGE_OR_STATUS")
        require(not value["unresolved"] and all(c["finding"] != "UNRESOLVED" for c in value["checks"]), "UNRESOLVED_CANNOT_HAVE_SCORE")
        if score == 3:
            require(all(c["finding"] == "SUPPORTED" for c in value["checks"]), "PCS3_WITH_REPORTED_ERROR")
    return {**deepcopy(value), "validation_errors": []}


def validate_prc(value, artifact: dict, accesses: list) -> dict:
    require(isinstance(value, dict) and set(value) == PRC_FIELDS, "MISSING_OR_INVALID_PRC")
    require(text(value["reason"]) and isinstance(value["unresolved"], list) and all(text(x) for x in value["unresolved"]), "INVALID_PRC_REASON")
    if value["status"] == "UNRESOLVED":
        require(value["score"] is None and value["unresolved"], "UNRESOLVED_PRC_MUST_BE_NULL")
    else:
        require(value["status"] == "SCORED" and type(value["score"]) is int and 1 <= value["score"] <= 5, "PRC_OUT_OF_RANGE_OR_STATUS")
        require(not value["unresolved"], "UNRESOLVED_PRC_CANNOT_HAVE_SCORE")
    require(isinstance(value["modifications"], list) and value["modifications"], "CONCRETE_MODIFICATIONS_REQUIRED")
    claims = {c["claim_id"] for s in artifact["slots"].values() for c in s["claims"]}
    for change in value["modifications"]:
        require(isinstance(change, dict) and set(change) == MODIFICATION_FIELDS, "INVALID_MODIFICATION_FIELDS")
        require(change["location"] in claims | set(SLOTS) | {"ARTIFACT"} and text(change["need"]), "INVALID_OUTPUT_LOCATION")
        require(change["kind"] in KINDS, "INVALID_MODIFICATION_KIND")
        if change["kind"] == "FACTUAL":
            require(change["location"] in claims, "FACTUAL_ERROR_NEEDS_ASSERTION_ID")
        validate_refs(change["source_refs"], accesses, required=change["kind"] == "FACTUAL")
    if value["score"] == 5:
        require(all(c["kind"] == "NONE" for c in value["modifications"]), "PRC5_WITH_REPORTED_REQUIRED_EDIT")
    if value["score"] is not None and value["score"] < 5:
        require(any(c["kind"] != "NONE" for c in value["modifications"]), "LOW_PRC_NEEDS_CONCRETE_EDIT")
    return {**deepcopy(value), "validation_errors": []}


def evaluate_response(raw, artifact, accesses) -> dict:
    pcs = {s: empty_score() if s in artifact["empty_slots"] else absent("NOT_RETURNED", "模型未返回有效评分。") for s in SLOTS}
    prc = {"score": None, "status": "NOT_RETURNED", "reason": "模型未返回有效评分。", "modifications": [], "unresolved": [], "validation_errors": []}
    errors = []
    try:
        data = parse_response(raw)
        require(set(data) == {"pcs", "prc"} and isinstance(data["pcs"], dict), "INVALID_TOP_LEVEL_FIELDS")
        require(not set(data["pcs"]) - set(SLOTS), "EXTRA_SCORED_SLOT")
    except InvalidResponse as exc:
        return {"pcs": pcs, "prc": prc, "parse_status": "PARSE_FAILED", "validation_errors": [str(exc)]}
    for slot in SLOTS:
        if slot in artifact["empty_slots"]:
            # Missing empty-slot responses are okay only because this is a deterministic rule.
            supplied = data["pcs"].get(slot)
            if supplied is not None and (not isinstance(supplied, dict) or type(supplied.get("score")) is not int or supplied.get("score") != 0 or supplied.get("status") != "EMPTY_SLOT"):
                errors.append(slot + ":EMPTY_SLOT_OVERRIDE_REJECTED")
            continue
        try:
            pcs[slot] = validate_pcs(data["pcs"].get(slot), slot, artifact, accesses)
        except (InvalidResponse, TypeError, KeyError) as exc:
            pcs[slot] = {**absent("INVALID_RESPONSE", "评分校验失败。"), "validation_errors": [str(exc)]}
            errors.append(slot + ":" + str(exc))
    if artifact.get("explicit_refusal", False):
        prc.update(status="NO_EVALUABLE_CONTENT", reason="显式拒答不计 PRC，已有正文仍按槽位评价。")
    else:
        try:
            prc = validate_prc(data.get("prc"), artifact, accesses)
        except (InvalidResponse, TypeError, KeyError) as exc:
            prc["status"] = "INVALID_RESPONSE"
            prc["validation_errors"] = [str(exc)]
            errors.append("prc:" + str(exc))
    return {"pcs": pcs, "prc": prc, "parse_status": "INVALID_RESPONSE" if errors else "VALID", "validation_errors": errors}


def derive(record: dict) -> dict:
    scores = [record["pcs"][s]["score"] for s in SLOTS]
    pcs_mean = sum(scores) / 7 if all(type(x) is int and 0 <= x <= 3 for x in scores) else None
    if record["artifact_state"] in {"EMPTY_OUTPUT", "REFUSED"}:
        status = record["artifact_state"]
    elif record["parse_status"] not in {"VALID", "RULE_ONLY"}:
        status = "TECHNICAL_FAILURE"
    elif any(v["status"] == "UNRESOLVED" for v in record["pcs"].values()) or record["prc"]["status"] == "UNRESOLVED":
        status = "UNRESOLVED"
    elif pcs_mean is not None and (record["prc"]["score"] is not None or record.get("explicit_refusal", False)):
        status = "COMPLETED"
    else:
        status = "TECHNICAL_FAILURE"
    receipts = record.get("runtime_receipts", [])
    if record["model_requests"] == 0:
        runtime_cost, real_calls = 0.0, 0
    elif receipts and len(receipts) == record["model_requests"]:
        costs = [r.get("cost_cny") for r in receipts]
        calls = [r.get("real_model_calls") for r in receipts]
        runtime_cost = sum(costs) if all(type(c) in {int, float} and c >= 0 for c in costs) else None
        real_calls = sum(calls) if all(type(c) is int and c >= 0 for c in calls) else None
    elif record["data_class"] == "MOCK_ONLY" and not receipts:
        runtime_cost, real_calls = 0.0, 0
    else:
        runtime_cost, real_calls = None, None
    return {"pcs_mean": pcs_mean, "processing_status": status,
            "runtime_cost_cny": runtime_cost, "runtime_cost_basis": "USAGE_LIST_PRICE_OR_CONSERVATIVE_UPPER_ESTIMATE_NOT_INVOICE", "real_model_calls": real_calls}


def seal(record: dict) -> dict:
    record.update(derive(record))
    record["record_sha256"] = digest(record)
    return record


def validate_record(record: dict, artifact: dict, manifest: dict, config: dict) -> None:
    if config.get('request_organization') == 'L4_SIMULATED_EXPERT_3.0.6':
        from .expert import validate_record as validate_expert_record
        return validate_expert_record(record, artifact, manifest, config)
    if record.get("request_organization"):
        from .staged import validate_components
        validate_components(record, artifact)
    payload = {k: v for k, v in record.items() if k != "record_sha256"}
    require(record.get("record_sha256") == digest(payload), "RECORD_HASH_MISMATCH")
    require(record.get("manifest_sha256") == manifest["manifest_sha256"] and record.get("versions") == manifest["versions"], "STALE_RECORD")
    require(record.get("artifact_id") == artifact["artifact_id"] and record.get("input_sha256") == artifact["input_sha256"], "WRONG_ARTIFACT")
    require(record.get("task_id") == artifact["task_id"] and record.get("method") == artifact["method"] and record.get("project_id") == artifact["project_id"], "IDENTITY_MISMATCH")
    require(record.get("comparison_group") == artifact["comparison_group"], "GROUP_MISMATCH")
    require(record.get("data_class") == config["data_class"] and record.get("model_config_sha256") == digest(config["model"]), "CONFIG_MISMATCH")
    require(record.get("artifact_state") == artifact["state"] and record.get("explicit_refusal") == artifact.get("explicit_refusal", False), "ARTIFACT_STATE_MISMATCH")
    require(record.get("judge_id") == config["judge"]["judge_id"] and record.get("judge_version") == config["judge"]["version"] and record.get("model_config_id") == config["model"]["config_id"], "JUDGE_MODEL_MISMATCH")
    expected_sources = [{k: s[k] for k in ("source_id", "sha256", "state")} for s in manifest["sources"] if s["project_id"] == artifact["project_id"]]
    require(record.get("sources") == expected_sources, "SOURCE_VERSION_MISMATCH")
    authorized_sources = {s["source_id"]: s["sha256"] for s in expected_sources}
    for i, access in enumerate(record["source_access"]):
        require(access["access_id"] == "L4-ACCESS-" + str(i + 1), "INVALID_ACCESS_SEQUENCE")
        if access["result"]["status"] in {"OK", "NO_MATCH"}:
            sid = access["arguments"].get("source_id")
            require(sid in authorized_sources and access["source_sha256"] == authorized_sources[sid], "UNAUTHORIZED_ACCESS_RECORD")
    require(set(record["pcs"]) == set(SLOTS), "SEVEN_SLOTS_REQUIRED")
    if record["parse_status"] in {"VALID", "INVALID_RESPONSE", "PARSE_FAILED"}:
        checked = evaluate_response(record["raw_response"], artifact, record["source_access"])
        require(all(record[k] == checked[k] for k in ("pcs", "prc", "parse_status", "validation_errors")), "RECORD_RESPONSE_MISMATCH")
    else:
        require(record["parse_status"] in {"RULE_ONLY", "NOT_RUN", "SOURCE_UNREADABLE", "INVALID_TOOL_REQUEST", "CLIENT_OR_TOOL_ERROR", "REQUEST_LIMIT", "SOURCE_CHANGED"}, "UNKNOWN_PARSE_STATE")
        require(record["prc"]["score"] is None, "NO_RESPONSE_CANNOT_HAVE_PRC")
        for slot, value in record["pcs"].items():
            require(value["score"] is None or (slot in artifact["empty_slots"] and value == empty_score()), "NO_RESPONSE_CANNOT_HAVE_PCS")
        if record["parse_status"] == "RULE_ONLY":
            require(artifact["state"] in {"EMPTY_OUTPUT", "REFUSED"} and record["raw_response"] is None, "INVALID_RULE_ONLY_STATE")
    for slot, score in record["pcs"].items():
        if score["score"] is not None:
            require(type(score["score"]) is int and 0 <= score["score"] <= 3 and score["status"] in {"SCORED", "EMPTY_SLOT"}, "INVALID_SAVED_PCS")
        if score["status"] == "EMPTY_SLOT":
            require(artifact["state"] in {"PRESENT", "EMPTY_OUTPUT", "REFUSED"} and slot in artifact["empty_slots"] and score["score"] == 0, "INVALID_EMPTY_SLOT")
    p = record["prc"]
    require(not record.get("explicit_refusal", False) or p["score"] is None, "REFUSAL_CANNOT_HAVE_PRC")
    require(p["score"] is None or (type(p["score"]) is int and 1 <= p["score"] <= 5 and p["status"] == "SCORED"), "INVALID_SAVED_PRC")
    require(all(record[k] == v for k, v in derive(record).items()), "AGGREGATE_MISMATCH")
