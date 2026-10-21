"""Deterministic counts and common denominators, never a score or automatic ranking."""
from __future__ import annotations

from collections import Counter
import csv
from pathlib import Path

from .common import COMPLETE, L2Error, canonical, digest, write_json
from .evaluation import VALID_STATUSES

ARTIFACT_COLUMNS = (
    "artifact_id", "task_id", "target_id", "source_project", "project_id", "cohort_group",
    "method", "input_hash", "run_id", "data_origin", "evaluation_mode", "research_data_status",
    "input_status", "status", "parse_status", "confirmed_conflict_count", "unresolved_count",
    "assertion_count", "assertion_count_is_complete", "empty_slot_count", "empty_slots",
    "omitted_slots", "validation_errors", "model_config_id", "protocol_version", "prompt_version",
    "cache_key", "conflicts", "unresolved", "attempt", "error_code",
    "real_model_calls", "new_real_model_calls", "runtime_request_id",
    "response_model", "finish_reason", "usage", "cost_cny", "cached_response",
)


def csv_table(path, rows, columns=None):
    columns = list(columns or (list(rows[0]) if rows else []))
    with Path(path).open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: canonical(row.get(k)) if isinstance(row.get(k), (dict, list))
                             else row.get(k) for k in columns})


def _validate_records(records, config):
    expected = {(t["task_id"], m) for t in config["targets"] for m in config["methods"]}
    pairs = [(r["task_id"], r["method"]) for r in records]
    if len(set(pairs)) != len(pairs) or set(pairs) != expected:
        raise L2Error("Every frozen target/method pair needs exactly one record, including missing inputs")
    ids = [r["artifact_id"] for r in records]
    if len(set(ids)) != len(ids):
        raise L2Error("Duplicate artifact ID")
    targets = {t["task_id"]: t for t in config["targets"]}
    for r in records:
        if r["status"] not in VALID_STATUSES:
            raise L2Error("Unknown processing status")
        if r["input_batch_id"] != config["input_batch_id"]:
            raise L2Error("Cannot combine different frozen input batches")
        if r["model_config_id"] != digest(config["model"]):
            raise L2Error("Cannot combine different model bindings")
        if r["protocol_version"] != config["protocol_version"] or r["prompt_version"] != config["prompt_version"]:
            raise L2Error("Cannot combine different protocols or prompts")
        if r["target_id"] != r["task_id"] or r["project_id"] != r["source_project"]:
            raise L2Error("Invalid cross-layer join aliases")
        if r["cohort_group"] != targets[r["task_id"]].get("cohort_group", "UNSPECIFIED"):
            raise L2Error("Cohort group mismatch")
        if r["status"] == "COMPLETE_WITH_CONFLICTS" and not r["conflicts"]:
            raise L2Error("Completed-with-conflicts requires a concrete conflict")
        if r["status"] == "COMPLETE_NO_CONFLICTS" and r["conflicts"]:
            raise L2Error("No-conflict state cannot contain confirmed conflicts")
        if r["status"] in COMPLETE and r["input_status"] != "PRESENT":
            raise L2Error("Empty, refused or broken input cannot be a completed semantic check")
        if config["data_origin"] == "REAL" and r["input_status"] not in {"MISSING_FILE", "READ_FAILED"}:
            bound = next(b for b in config["input_bindings"]
                         if (b["task_id"], b["method"]) == (r["task_id"], r["method"]))
            if bound["sha256"] != r["input_hash"]:
                raise L2Error("Record is from a different frozen input file")
        if r["source_project"] != targets[r["task_id"]]["source_project"]:
            raise L2Error("Cross-document record mismatch")
        if r["data_origin"] != config["data_origin"]:
            raise L2Error("Cannot mix real and fixture data")
        if r["data_origin"] == "FIXTURE" and r["research_data_status"] != "NOT_RESEARCH_DATA":
            raise L2Error("Fixture results require NOT_RESEARCH_DATA")
        if r["unresolved_count"] != len(r["unresolved"]):
            raise L2Error("Unresolved count mismatch")
        count = len(r["conflicts"]) if r["status"] in COMPLETE | {"PARTIAL_UNRESOLVED"} else None
        if r["confirmed_conflict_count"] != count:
            raise L2Error("Conflict count mismatch")
        if r["status"] in COMPLETE and (r["unresolved"] or r["validation_errors"] or r["parse_status"] != "VALID"):
            raise L2Error("Incomplete result cannot count as a completed check")


def _group(rows, targets, methods, project, method, group):
    ids = {t["task_id"] for t in targets}
    by_pair = {(r["task_id"], r["method"]): r for r in rows}
    selected = [by_pair[t, method] for t in sorted(ids)]
    common = sorted(t for t in ids if all(by_pair[t, m]["status"] in COMPLETE for m in methods))
    completed = [r for r in selected if r["status"] in COMPLETE]
    partial = [r for r in selected if r["status"] == "PARTIAL_UNRESOLVED"]
    common_rows = [by_pair[t, method] for t in common]
    counts = Counter(r["status"] for r in selected)
    return {
        "source_project": project, "project_id": project, "method": method, "cohort_group": group,
        "expected_target_count": len(ids), "registered_record_count": len(selected),
        "completed_checks": len(completed),
        "complete_with_conflicts": counts["COMPLETE_WITH_CONFLICTS"],
        "complete_no_conflicts": counts["COMPLETE_NO_CONFLICTS"],
        "partial_or_unresolved": counts["PARTIAL_UNRESOLVED"],
        "technical_failures": counts["TECHNICAL_FAILURE"],
        "no_evaluable_content": counts["NO_EVALUABLE_CONTENT"],
        "missing_files": sum(r["input_status"] == "MISSING_FILE" for r in selected),
        "valid_empty_outputs": sum(r["input_status"] == "VALID_EMPTY" for r in selected),
        "explicit_refusals": sum(r["input_status"] == "EXPLICIT_REFUSAL" for r in selected),
        "parse_failed_inputs": sum(r["input_status"] == "PARSE_FAILED" for r in selected),
        "structure_errors": sum(r["input_status"] == "STRUCTURE_ERROR" for r in selected),
        "unresolved_issue_count": sum(r["unresolved_count"] for r in selected),
        "response_validation_error_count": sum(len(r["validation_errors"]) for r in selected),
        "confirmed_conflicts_completed": sum(len(r["conflicts"]) for r in completed),
        "confirmed_conflicts_partial": sum(len(r["conflicts"]) for r in partial),
        "completed_check_denominator": len(completed),
        "common_completed_target_count": len(common),
        "common_completed_target_ids": common,
        "common_confirmed_conflicts": sum(len(r["conflicts"]) for r in common_rows),
        "assertion_count_observed": sum(r["assertion_count"] for r in selected),
        "empty_slot_count_observed": sum(r["empty_slot_count"] for r in selected),
        "structural_count_complete_artifacts": sum(r["assertion_count_is_complete"] for r in selected),
        "comparison_status": "ALL_TARGETS_COMPLETE" if len(common) == len(ids) else "INCOMPLETE_NO_RANKING",
        "data_origin": selected[0]["data_origin"], "research_data_status": selected[0]["research_data_status"],
    }


def summarize(records, config):
    _validate_records(records, config)
    artifact_rows = [{k: r.get(k) for k in ARTIFACT_COLUMNS} for r in records]
    grouped, stratified = [], []
    for project in sorted({t["source_project"] for t in config["targets"]}):
        targets = [t for t in config["targets"] if t["source_project"] == project]
        for method in config["methods"]:
            grouped.append(_group(records, targets, config["methods"], project, method, "ALL_REGISTERED"))
            for group in sorted({t.get("cohort_group", "UNSPECIFIED") for t in targets}):
                subset = [t for t in targets if t.get("cohort_group", "UNSPECIFIED") == group]
                stratified.append(_group(records, subset, config["methods"], project, method, group))
    return {
        "data_origin": config["data_origin"],
        "research_data_status": "NOT_RESEARCH_DATA" if config["data_origin"] == "FIXTURE" else "LIVE_UNVALIDATED",
        "per_artifact": artifact_rows, "per_document_method": grouped, "per_document_method_group": stratified,
        "interpretation": [
            "Confirmed conflict counts are findings, not a consistency/full-quality score.",
            "Empty, refusal and failures have null conflict counts, never a perfect score.",
            "Partial confirmed findings are reported separately from completed-check counts.",
            "Method comparisons share frozen targets and explicitly report the common completed subset.",
            "No ranking, significance claim, weighted score or addition to historical R1-R7 counts.",
        ],
    }


def export_summary(destination, records, config):
    destination = Path(destination)
    if config["data_origin"] == "FIXTURE" and "fixtures" not in destination.parts:
        raise L2Error("Mock exports must stay in an explicitly named fixtures directory")
    result = summarize(records, config)
    destination.mkdir(parents=True, exist_ok=False)
    write_json(destination / "summary.json", result)
    for name in ("per_artifact", "per_document_method", "per_document_method_group"):
        rows = result[name]
        write_json(destination / (name + ".json"), rows)
        csv_table(destination / (name + ".csv"), rows, ARTIFACT_COLUMNS if name == "per_artifact" else None)
    return result
