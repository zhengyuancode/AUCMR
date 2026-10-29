"""Fixed per-slot PCS and independent PRC, immutable component checkpoints."""
from copy import deepcopy
import json

from eval.evaluation_runtime import RuntimeBlocked, RuntimeDeferred, RuntimeResponseError
from .contract import (CONTRACT_VERSION, INSTRUCTIONS, CORRECTION_INSTRUCTIONS, ASSESSMENT_INSTRUCTIONS, FINAL_INSTRUCTIONS, schema, shape_errors,
                       expand, evidence_catalog, check_coverage)
from .protocol import L4Error, SLOTS, RUBRIC, digest, read_json, save_new
from .records import (parse_response, validate_pcs, validate_prc, evaluate_response,
                      seal, absent, empty_score)
from .sources import SourceTools
from .tool_contract import tool_schema, validate_tool_request, ToolContractError
from .inputs import anonymous
from .corrections import (plan_correction, correction_schema, merge_correction,
                          discovery_guidance, acquire_correction_evidence, retained_checks)

MAX_ROUNDS = 4
MAX_CORRECTIONS = 1
MAX_INCOMPLETE_RECOVERIES = 1
JUDGMENT_ROUND = MAX_ROUNDS - MAX_CORRECTIONS


def stage_request(stage, artifact, task, sources, policy, accesses, round_number=1):
    if type(round_number) is not int or not 1 <= round_number <= MAX_ROUNDS:
        raise ValueError("INVALID_STAGE_ROUND")
    catalog = evidence_catalog(accesses)
    request = {"system": INSTRUCTIONS, "rubric": RUBRIC,
            "artifact": anonymous(artifact, task, sources),
            "stage_id": stage, "request_organization": CONTRACT_VERSION,
            "required_claim_ids": [c["claim_id"] for c in artifact["slots"][stage]["claims"]] if stage != "prc" else [],
            "program_handled_empty_slots": artifact["empty_slots"],
            "output_schema": schema(stage, artifact, catalog),
            "legal_output_modes": {"tool_request": "One object matching tool_response_schema, with only tool_calls at top level.",
                                   "evaluation_result": "One complete object matching output_schema, without tool_calls or any wrapper."},
            "tool_response_schema": tool_schema(sources, policy),
            "tool_request_example": {"tool_calls": [{"name": "source_page", "arguments": {
                "source_id": sources[0]["source_id"], "pdf_page": 1, "image": False}}]} if sources else None,
            "evidence_catalog": catalog, "source_policy": policy,
            "discovery_only_pages": discovery_guidance(accesses),
            "stage_limits": {"max_requests": MAX_ROUNDS, "max_corrections": MAX_CORRECTIONS,
                             "max_incomplete_recoveries": MAX_INCOMPLETE_RECOVERIES,
                             "judgment_required_from_request": JUDGMENT_ROUND,
                             "current_request": round_number, "requests_remaining_after_this": MAX_ROUNDS-round_number},
            "tools": {"source_index": {"required": ["source_id"]},
                      "source_search": {"required": ["source_id", "query"]},
                      "source_page": {"required": ["source_id", "pdf_page"], "optional": ["image", "dpi", "bbox"]}}}
    if round_number >= JUDGMENT_ROUND:
        request.update(system=FINAL_INSTRUCTIONS, judgment_only=True, tools={}, discovery_only_pages=[],
                       legal_output_modes={"evaluation_result": "Only the complete object in output_schema; no tools."})
        request.pop("tool_response_schema")
        request.pop("tool_request_example")
    return request


def validate_stage(raw, stage, artifact, accesses):
    value = parse_response(raw)
    catalog = evidence_catalog(accesses)
    errors = shape_errors(value, schema(stage, artifact, catalog))
    if stage != "prc":
        coverage = check_coverage(value, [c["claim_id"] for c in artifact["slots"][stage]["claims"]])
        if any(coverage.values()):
            errors.append(coverage)
    if errors:
        raise ValueError(json.dumps(errors, ensure_ascii=False))
    value = expand(value, catalog)
    checked = validate_prc(value, artifact, accesses) if stage == "prc" else validate_pcs(value, stage, artifact, accesses)
    checked.pop("validation_errors", None)
    return checked


def correction_request(request, plan, stage, artifact, accesses):
    request = deepcopy(request)
    request.pop("tool_response_schema", None)
    request.pop("tool_request_example", None)
    request["legal_output_modes"] = {"evaluation_patch": "Only the replacements and assessment object in output_schema."}
    request.update(system=CORRECTION_INSTRUCTIONS, correction_mode="REPLACE_NAMED_CHECKS",
                   target_claim_ids=plan["target_claim_ids"],
                   retained_checks=retained_checks(plan),
                   retained_checks_instruction="Immutable checks still count in the whole-slot assessment. If any is UNRESOLVED, return score=null, status=UNRESOLVED and nonempty reasons. Do not rescore or omit retained checks.",
                   original_stage_response=parse_response(plan["base_response"]),
                   output_schema=correction_schema(plan, stage, artifact, accesses),
                   tools={}, discovery_only_pages=[],
                   evidence_acquisition="COMPLETE_FOR_THIS_PATCH; unavailable evidence remains UNRESOLVED")
    if not plan["target_claim_ids"]:
        request.update(system=ASSESSMENT_INSTRUCTIONS, correction_mode="ASSESSMENT_ONLY",
                       required_claim_ids=[],
                       legal_output_modes={"assessment_only": "Return the flat score/status/reason/unresolved object in output_schema. No assessment wrapper, replacements or checks. All retained checks are immutable."})
        request.pop("original_stage_response")
    return request


def access_history(entries):
    return [{"origin": "RUNTIME_SOURCE_BOOTSTRAP",
             "tool_calls": [{"name": e["operation"], "arguments": e["arguments"]}],
             "tool_results": [{"call": {"name": e["operation"], "arguments": e["arguments"]}, "result": e["result"]}]}
            for e in entries]


def validated_component_value(saved, stage, artifact):
    raw = saved["raw_response"]
    if saved.get("correction_plan"):
        plan = saved["correction_plan"]
        if plan_correction(plan["base_response"], stage, artifact, plan["base_accesses"]) != plan:
            raise L4Error("CORRECTION_PLAN_MISMATCH")
        merged = merge_correction(raw, plan, stage, artifact, saved["source_access"])
        if merged != saved.get("merged_response"):
            raise L4Error("CORRECTION_MERGE_MISMATCH")
        raw = json.dumps(merged, ensure_ascii=False)
    return validate_stage(raw, stage, artifact, saved["source_access"])


def component(stage, artifact, task, sources, config, manifest, run_dir, client, tools):
    path = run_dir / "components" / artifact["artifact_id"] / (stage + ".json")
    identity = {"artifact_id": artifact["artifact_id"], "input_sha256": artifact["input_sha256"],
                "stage": stage, "manifest_sha256": manifest["manifest_sha256"],
                "organization": CONTRACT_VERSION}
    if path.exists():
        saved = read_json(path)
        if saved.get("sha256") != digest({k: v for k, v in saved.items() if k != "sha256"}) or saved["identity"] != identity:
            raise L4Error("STAGE_CHECKPOINT_IDENTITY_MISMATCH")
        if saved["source_access_before"] != tools.entries:
            raise L4Error("STAGE_SOURCE_PREFIX_MISMATCH")
        for entry in saved["source_access"][len(tools.entries):]:
            tools.invoke(entry["operation"], entry["arguments"])
            if tools.entries[-1] != entry:
                raise L4Error("STAGE_SOURCE_REPLAY_MISMATCH")
        if saved["status"] == "VALID" and validated_component_value(saved, stage, artifact) != saved["value"]:
            raise L4Error("STAGE_RESPONSE_MISMATCH")
        return saved
    saved = {"identity": identity, "source_access_before": deepcopy(tools.entries),
             "transcript": [], "receipts": [], "status": "FAILED", "error": "REQUEST_LIMIT",
             "raw_response": None, "value": None, "stop_run": False}
    history = access_history(tools.entries)
    corrections = 0
    incomplete_recoveries = 0
    correction_plan = None
    for round_number in range(1, MAX_ROUNDS + 1):
        request = stage_request(stage, artifact, task, sources, config["source_policy"], tools.entries, round_number)
        if correction_plan:
            request = correction_request(request, correction_plan, stage, artifact, tools.entries)
        attempt = {"request": deepcopy(request), "history": deepcopy(history), "raw_response": None}
        try:
            raw = client.respond(request, history)
        except RuntimeDeferred:
            # Runtime's immutable request cache replays any paid partial rounds.
            raise
        except RuntimeResponseError as exc:
            receipt = getattr(client, "last_receipt", None)
            raw = receipt.get("body") if isinstance(receipt, dict) else None
            attempt["runtime_error"] = str(exc)
        except RuntimeBlocked as exc:
            attempt["runtime_error"] = str(exc)
            saved["error"], saved["stop_run"] = str(exc), True
            saved["transcript"].append(attempt)
            receipt = getattr(client, "last_receipt", None)
            if isinstance(receipt, dict): saved["receipts"].append(deepcopy(receipt))
            break
        attempt["raw_response"] = deepcopy(raw)
        saved["transcript"].append(attempt)
        receipt = getattr(client, "last_receipt", None)
        if isinstance(receipt, dict): saved["receipts"].append(deepcopy(receipt))
        saved["raw_response"] = raw
        if isinstance(receipt, dict) and receipt.get("finish_reason") == "length":
            # A truncated response is never a judgment, even if its visible
            # prefix happens to parse. Preserve its receipt, but do not spend
            # the separate allowance for correcting a completed judgment.
            saved["error"] = "INCOMPLETE_FINISH_REASON"
            if receipt.get("cost_cny") is None:
                saved["error"], saved["stop_run"] = "UNKNOWN_COST_STOP", True
                break
            if round_number == MAX_ROUNDS or incomplete_recoveries >= MAX_INCOMPLETE_RECOVERIES:
                break
            incomplete_recoveries += 1
            history.append({"validation_feedback": {
                "errors": ["TRUNCATED_RESPONSE"], "rejected_response": raw,
                "incomplete_response": True}})
            continue
        try:
            parsed = parse_response(raw)
            if "tool_calls" in parsed:
                if request.get("judgment_only"):
                    raise ValueError("JUDGMENT_PHASE_REQUIRES_RESULT; no tools dispatched")
                if correction_plan:
                    raise ValueError("PATCH_ONLY_NO_TOOL_CALLS")
                calls = validate_tool_request(parsed, sources, config["source_policy"])
                results = [{"call": c, "result": tools.invoke(c["name"], c["arguments"])} for c in calls]
                history.append({"tool_calls": calls, "tool_results": results})
                if tools.integrity_failure:
                    saved["error"], saved["stop_run"] = "SOURCE_CHANGED", True
                    break
                continue
            if correction_plan:
                merged = merge_correction(raw, correction_plan, stage, artifact, tools.entries)
                saved["merged_response"] = merged
                saved["value"] = validate_stage(json.dumps(merged, ensure_ascii=False), stage, artifact, tools.entries)
            else:
                saved["value"] = validate_stage(raw, stage, artifact, tools.entries)
            saved["status"], saved["error"] = "VALID", None
            break
        except (ValueError, KeyError, TypeError) as exc:
            saved["error"] = str(exc)
            if round_number == MAX_ROUNDS or corrections >= MAX_CORRECTIONS or not isinstance(raw, str):
                break
            corrections += 1
            correction_plan = plan_correction(raw, stage, artifact, tools.entries)
            if correction_plan:
                saved["correction_plan"] = deepcopy(correction_plan)
                saved["correction_source_reads"] = acquire_correction_evidence(correction_plan, tools)
                if tools.integrity_failure:
                    saved["error"], saved["stop_run"] = "SOURCE_CHANGED", True
                    break
                history = access_history(tools.entries)
            feedback = {"errors": [str(exc)], "rejected_response": raw}
            if isinstance(exc, ToolContractError):
                feedback["tool_correction"] = deepcopy(exc.details)
            history.append({"validation_feedback": feedback})
    saved["recovery_counters"] = {"incomplete_recoveries": incomplete_recoveries,
                                  "validation_corrections": corrections}
    saved["source_access"] = deepcopy(tools.entries)
    saved["sha256"] = digest(saved)
    save_new(path, saved)
    return saved


def judge_staged(artifact, task, sources, config, manifest, run_dir, client):
    from .engine import initial_record, bootstrap_sources
    record = initial_record(artifact, config, manifest, run_dir.name)
    tools = SourceTools(sources, artifact["project_id"], config["source_policy"],
                        run_dir / "source_backend" / (artifact["artifact_id"] + ".jsonl"), artifact["artifact_id"])
    bootstrap_sources(tools, task, sources)
    record["request_organization"] = CONTRACT_VERSION
    record["components"] = []
    combined = {"pcs": {}, "prc": {}}
    stages = [s for s in SLOTS if s not in artifact["empty_slots"]]
    if not artifact.get("explicit_refusal", False): stages.append("prc")
    for stage in stages:
        part = component(stage, artifact, task, sources, config, manifest, run_dir, client, tools)
        record["components"].append(deepcopy(part))
        record["request_transcript"].extend(deepcopy(part["transcript"]))
        for receipt in part["receipts"]:
            record["runtime_receipts"].append({**deepcopy(receipt), "model_request": len(record["runtime_receipts"]) + 1})
        record["model_requests"] += len(part["transcript"])
        if part["status"] != "VALID":
            record["parse_status"] = "CLIENT_OR_TOOL_ERROR"
            record["validation_errors"] = [stage + ":" + part["error"]]
            record["runtime_failure"] = "STAGE_FAILED"
            record["stop_run"] = part["stop_run"]
            record["pcs"] = {s: empty_score() if s in artifact["empty_slots"] else absent("STAGE_FAILED", "组件失败，未形成完整评价。") for s in SLOTS}
            record["prc"].update(score=None, status="STAGE_FAILED")
            break
        if stage == "prc": combined["prc"] = part["value"]
        else: combined["pcs"][stage] = part["value"]
    else:
        # This is an explicitly labelled assembly of model judgments, not a raw
        # model response. Empty slots are omitted and handled by the fixed rule.
        record["raw_response"] = json.dumps(combined, ensure_ascii=False)
        record["raw_response_origin"] = "DETERMINISTIC_ASSEMBLY_OF_STAGE_RESPONSES"
        record.update(evaluate_response(record["raw_response"], artifact, tools.entries))
    record["source_access"] = tools.entries
    return seal(record)


def validate_components(record, artifact):
    """Reject edited assemblies; rederive judgments from original component text."""
    combined = {"pcs": {}, "prc": {}}
    expected = [s for s in SLOTS if s not in artifact["empty_slots"]]
    if not artifact.get("explicit_refusal", False): expected.append("prc")
    parts = record["components"]
    stages = [p["identity"]["stage"] for p in parts]
    if stages != expected[:len(stages)] or (record["parse_status"] == "VALID" and stages != expected):
        raise L4Error("STAGE_SET_MISMATCH")
    prefix = None
    for part in parts:
        if part["sha256"] != digest({k: v for k, v in part.items() if k != "sha256"}):
            raise L4Error("STAGE_HASH_MISMATCH")
        if part["identity"]["manifest_sha256"] != record["manifest_sha256"] or part["identity"]["artifact_id"] != record["artifact_id"]:
            raise L4Error("STAGE_IDENTITY_MISMATCH")
        if prefix is not None and prefix != part["source_access_before"]:
            raise L4Error("STAGE_SOURCE_CHAIN_MISMATCH")
        prefix = part["source_access"]
        stage = part["identity"]["stage"]
        if part["status"] == "VALID":
            value = validated_component_value(part, stage, artifact)
            if value != part["value"]: raise L4Error("STAGE_JUDGMENT_MISMATCH")
            if stage == "prc": combined["prc"] = value
            else: combined["pcs"][stage] = value
    if prefix != record["source_access"]:
        raise L4Error("STAGE_ACCESS_MISMATCH")
    if record["parse_status"] == "VALID" and parse_response(record["raw_response"]) != combined:
        raise L4Error("ASSEMBLED_JUDGMENT_MISMATCH")
