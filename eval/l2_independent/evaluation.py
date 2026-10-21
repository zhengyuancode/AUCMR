"""Injectable clients, authorization gates, independent records and strict response validation."""
from __future__ import annotations

import copy
import json
from typing import Protocol

from .common import (COMPLETE, L2Error, PROMPT_VERSION, PROTOCOL_VERSION, RECORD_VERSION,
                     SLOTS, canonical, digest, reject_secrets)
from .protocol import prompt_hash, request_for

ADAPTER_VERSION = "final_slots_lossless_1"
VALID_STATUSES = COMPLETE | {"PARTIAL_UNRESOLVED", "NO_EVALUABLE_CONTENT", "TECHNICAL_FAILURE"}


class ModelClient(Protocol):
    is_live: bool
    model_config_hash: str

    def complete(self, messages: list[dict]) -> str:
        """Return one raw JSON response. A caller must not reuse conversation state."""
        ...


class MockClient:
    is_live = False

    def __init__(self, response, model_config):
        self.response = response
        self.model_config_hash = digest(model_config)
        self.calls = []

    def complete(self, messages):
        self.calls.append(copy.deepcopy(messages))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response if isinstance(self.response, str) else json.dumps(self.response, ensure_ascii=False)


def require_live_authorization(config, explicit_authorization=False):
    """Checks precede calling an injected transport, even if environment keys exist."""
    reject_secrets(config)
    if config.get("live_authorized") is not True or explicit_authorization is not True:
        raise L2Error("Live use requires both configuration and explicit per-run authorization")
    model = config.get("model", {})
    if model.get("binding_status") != "BOUND":
        raise L2Error("The independent L2 model is not bound")
    for key in ("provider", "model_id", "model_revision", "base_url", "api_key_env"):
        if not isinstance(model.get(key), str) or not model[key].strip():
            raise L2Error("Missing independent model binding: " + key)
    if not isinstance(model.get("parameters"), dict):
        raise L2Error("Bind model parameters explicitly")


def cache_identity(a, config):
    """No implicit resume. Every factor defining a judgment is part of the identity."""
    return digest({
        "artifact_id": a["artifact_id"], "input_hash": a["input_hash"],
        "input_batch_id": a["input_batch_id"], "task_input": a["task_input"],
        "data_origin": a["data_origin"], "assertions": a["assertions"],
        "protocol_version": config["protocol_version"], "prompt_version": config["prompt_version"],
        "prompt_hash": prompt_hash(), "model_config_hash": digest(config["model"]),
        "adapter_version": ADAPTER_VERSION,
    })


def assert_reusable(record, artifact, config):
    if record.get("cache_key") != cache_identity(artifact, config):
        raise L2Error("Input, prompt, protocol, task, model or adapter changed; cached result cannot be reused")
    if record.get("status") not in COMPLETE or record.get("parse_status") != "VALID":
        raise L2Error("Incomplete or failed results are not reusable as completed checks")
    return True


def _issue(item, kind, valid_ids):
    fields = {"refs", "condition_or_path", "incompatible_content", "rationale"} if kind == "conflict" else {
        "refs", "condition_or_path", "question", "reason"}
    if not isinstance(item, dict) or set(item) != fields:
        raise L2Error("Wrong issue fields")
    if any(not isinstance(item[k], str) or not item[k].strip() for k in fields - {"refs"}):
        raise L2Error("Empty/non-text issue explanation")
    refs = item["refs"]
    if not isinstance(refs, list) or (kind == "conflict" and not refs):
        raise L2Error("Conflict needs valid assertion references")
    for ref in refs:
        if not isinstance(ref, dict) or set(ref) != {"slot", "assertion_id"}:
            raise L2Error("Wrong reference fields")
        cid = ref["assertion_id"]
        if not isinstance(cid, str) or cid not in valid_ids or valid_ids[cid] != ref["slot"]:
            raise L2Error("Unknown/out-of-range assertion ID or wrong slot")
    return copy.deepcopy(item)


def parse_response(raw, artifact):
    result = {"conflicts": [], "unresolved": [], "validation_errors": [], "duplicates_removed": 0}
    try:
        body = json.loads(raw)
    except (ValueError, TypeError):
        return result | {"status": "TECHNICAL_FAILURE", "parse_status": "INVALID_JSON"}
    if (not isinstance(body, dict) or set(body) != {"review_status", "conflicts", "unresolved"}
            or body["review_status"] not in ("COMPLETE", "PARTIAL")
            or not isinstance(body["conflicts"], list) or not isinstance(body["unresolved"], list)):
        return result | {"status": "TECHNICAL_FAILURE", "parse_status": "INVALID_SCHEMA"}
    valid_ids = {c["assertion_id"]: c["slot"] for c in artifact["assertions"]}
    for key, kind, prefix in (("conflicts", "conflict", "C"), ("unresolved", "unresolved", "U")):
        seen = set()
        for index, item in enumerate(body[key]):
            try:
                issue = _issue(item, kind, valid_ids)
            except L2Error as exc:
                result["validation_errors"].append({"list": key, "index": index, "error": str(exc)})
                continue
            identity = canonical(issue)
            if identity in seen:
                result["duplicates_removed"] += 1
                continue
            seen.add(identity)
            result[key].append({"issue_id": f"{prefix}{len(result[key]) + 1:03d}", **issue})
    if body["review_status"] == "PARTIAL" and not result["unresolved"]:
        result["unresolved"].append({
            "issue_id": f"U{len(result['unresolved']) + 1:03d}", "refs": [],
            "condition_or_path": "whole use case",
            "question": "The judge declared a partial review without a valid unresolved explanation.",
            "reason": "Review is incomplete; no complete-check denominator credit.",
            "origin": "PROTOCOL_VALIDATOR",
        })
    if result["validation_errors"]:
        state = "PARTIAL_UNRESOLVED" if result["conflicts"] or result["unresolved"] else "TECHNICAL_FAILURE"
        return result | {"status": state, "parse_status": "INVALID_LOCATIONS_OR_ISSUES"}
    state = ("PARTIAL_UNRESOLVED" if result["unresolved"] or body["review_status"] == "PARTIAL" else
             "COMPLETE_WITH_CONFLICTS" if result["conflicts"] else "COMPLETE_NO_CONFLICTS")
    return result | {"status": state, "parse_status": "VALID"}


def evaluate(artifact, config, client, run_id, *, explicit_live_authorization=False):
    """Fixture mocks only by default. Real scoring requires a separately supplied live client."""
    reject_secrets(config)
    if config.get("protocol_version") != PROTOCOL_VERSION or config.get("prompt_version") != PROMPT_VERSION:
        raise L2Error("Unsupported protocol/prompt version")
    fixture = artifact["data_origin"] == "FIXTURE"
    if fixture:
        if client.is_live:
            require_live_authorization(config, explicit_live_authorization)
    else:
        if not client.is_live:
            raise L2Error("Mock answers must never be assigned to real artifacts")
        require_live_authorization(config, explicit_live_authorization)
    if client.model_config_hash != digest(config["model"]):
        raise L2Error("Client does not match the isolated model configuration")
    rec = {
        "record_version": RECORD_VERSION, "artifact_id": artifact["artifact_id"],
        "task_id": artifact["task_id"], "target_id": artifact["task_id"],
        "source_project": artifact["source_project"], "project_id": artifact["source_project"],
        "cohort_group": artifact.get("cohort_group", "UNSPECIFIED"),
        "method": artifact["method"], "input_hash": artifact["input_hash"],
        "input_batch_id": artifact["input_batch_id"], "run_id": run_id,
        "protocol_version": PROTOCOL_VERSION, "prompt_version": PROMPT_VERSION,
        "prompt_hash": prompt_hash(), "adapter_version": ADAPTER_VERSION,
        "model_config_id": digest(config["model"]), "cache_key": cache_identity(artifact, config),
        "data_origin": artifact["data_origin"],
        "evaluation_mode": "LIVE" if client.is_live else "MOCK_ONLY",
        "research_data_status": "NOT_RESEARCH_DATA" if fixture else "LIVE_UNVALIDATED",
        "input_status": artifact["input_status"], "assertion_count": len(artifact["assertions"]),
        "assertion_count_is_complete": artifact["input_status"] in {"PRESENT", "VALID_EMPTY", "EXPLICIT_REFUSAL"},
        "empty_slot_count": len(artifact["empty_slots"]), "empty_slots": artifact["empty_slots"],
        "omitted_slots": artifact["omitted_slots"], "status": "TECHNICAL_FAILURE",
        "conflicts": [], "unresolved": [], "validation_errors": [], "duplicates_removed": 0,
        "raw_response": None, "parse_status": "NOT_CALLED", "request_hash": None,
        "error_code": None, "model_call_count": 0,
        # Local locator map, never identity/quality/history. Enough to resolve each reported ID.
        "assertion_locations": [{k: c[k] for k in ("assertion_id", "slot", "original_index", "step_number") if k in c}
                                for c in artifact["assertions"]],
    }
    state = artifact["input_status"]
    if state in {"VALID_EMPTY", "EXPLICIT_REFUSAL"}:
        rec["status"] = "NO_EVALUABLE_CONTENT"
    elif state != "PRESENT":
        rec["error_code"] = state
    else:
        try:
            request = request_for(artifact)
        except L2Error:
            rec["error_code"] = "INPUT_NOT_READY_OR_NOT_ANONYMOUS"
        else:
            rec["request_hash"] = digest(request)
            rec["model_call_count"] = 1
            try:
                raw = client.complete(request)
            except Exception as exc:
                # Avoid logging exception messages that may contain headers or secrets.
                rec.update(parse_status="CALL_FAILED", error_code=type(exc).__name__)
            else:
                rec["raw_response"] = raw if isinstance(raw, str) else None
                if not isinstance(raw, str):
                    rec.update(parse_status="INVALID_RESPONSE_TYPE", error_code="CLIENT_MUST_RETURN_TEXT")
                else:
                    rec.update(parse_response(raw, artifact))
    rec["confirmed_conflict_count"] = (
        len(rec["conflicts"]) if rec["status"] in COMPLETE | {"PARTIAL_UNRESOLVED"} else None)
    rec["unresolved_count"] = len(rec["unresolved"])
    return rec
