"""Separate PCS/PRC denominators and explicit matched-target comparisons."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from .protocol import SLOTS, save_new


def metric(rows, key):
    usable = [r for r in rows if (r["pcs_mean"] if key == "pcs" else r["prc"]["score"]) is not None]
    values = [r["pcs_mean"] if key == "pcs" else r["prc"]["score"] for r in usable]
    return {"mean": sum(values) / len(values) if values else None,
            "denominator": len(values), "task_ids": sorted(r["task_id"] for r in usable)}


def summarize(records: list[dict], config: dict) -> dict:
    groups = []
    for project, comparison_group in sorted({(t["project_id"], t["comparison_group"]) for t in config["targets"]}):
        planned = sorted(t["task_id"] for t in config["targets"] if t["project_id"] == project and t["comparison_group"] == comparison_group)
        rows = [r for r in records if r["project_id"] == project and r["comparison_group"] == comparison_group]
        common = {key: sorted(set.intersection(*[
            set(metric([r for r in rows if r["method"] == m], key)["task_ids"])
            for m in config["methods"]])) for key in ("pcs", "prc")}
        for method in config["methods"]:
            subset = [r for r in rows if r["method"] == method]
            groups.append({"project_id": project, "comparison_group": comparison_group, "method": method, "planned_task_ids": planned,
                           "counts": {"planned": len(planned), "completed": sum(r["processing_status"] == "COMPLETED" for r in subset),
                                      "empty_output": sum(r["artifact_state"] == "EMPTY_OUTPUT" for r in subset),
                                      "refusal": sum(r["explicit_refusal"] for r in subset),
                                      "unresolved": sum(any(s["status"] == "UNRESOLVED" for s in r["pcs"].values()) or r["prc"]["status"] == "UNRESOLVED" for r in subset),
                                      "technical_failure": sum(r["processing_status"] == "TECHNICAL_FAILURE" for r in subset)},
                           "available_pcs": metric(subset, "pcs"), "available_prc": metric(subset, "prc"),
                           "common_pcs": metric([r for r in subset if r["task_id"] in common["pcs"]], "pcs"),
                           "common_prc": metric([r for r in subset if r["task_id"] in common["prc"]], "prc"),
                           "all_target_statuses": [{"task_id": r["task_id"], "status": r["processing_status"],
                                                    "artifact_state": r["artifact_state"], "pcs_mean": r["pcs_mean"],
                                                    "prc": r["prc"]["score"],
                                                    "in_common_pcs": r["task_id"] in common["pcs"],
                                                    "in_common_prc": r["task_id"] in common["prc"]} for r in subset]})
    return {"data_class": config["data_class"], "research_status": "NOT_RESEARCH_DATA" if config["data_class"] == "MOCK_ONLY" else "UNVALIDATED_AGENT_ESTIMATE",
            "comparison_rule": "Use metric-specific common task IDs per document and predeclared comparison group. Available means are descriptive, not fair rankings.",
            "counts_note": "Declared refusal, unresolved and technical failure can overlap; each retains its evidence. Empty PCS=0 is included when all seven valid; empty/refusal PRC is excluded.",
            "planned": len(config["input_bindings"]), "groups": groups}


def write_csv_new(path: Path, rows: list[dict], fields: list[str]):
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False, allow_nan=False) if isinstance(v, (dict, list)) else v for k, v in row.items()})


def export_records(records: list[dict], config: dict, directory: Path) -> dict:
    directory.mkdir(parents=True, exist_ok=False)
    summary = summarize(records, config)
    save_new(directory / "artifacts.json", records)
    save_new(directory / "summary.json", summary)
    artifact_rows, slot_rows = [], []
    for r in records:
        artifact_rows.append({k: r[k] for k in ("artifact_id", "task_id", "project_id", "comparison_group", "method", "data_class", "research_status", "run_id", "artifact_state", "processing_status", "pcs_mean", "pcs", "prc", "parse_status", "input_sha256", "manifest_sha256", "sources", "versions", "judge_id", "model_config_id", "runtime_cost_cny", "real_model_calls", "runtime_receipts")})
        for slot in SLOTS:
            slot_rows.append({"artifact_id": r["artifact_id"], "task_id": r["task_id"], "method": r["method"],
                              "data_class": r["data_class"], "research_status": r["research_status"], "slot": slot, **r["pcs"][slot]})
    write_csv_new(directory / "artifacts.csv", artifact_rows, list(artifact_rows[0]))
    write_csv_new(directory / "slots.csv", slot_rows, list(slot_rows[0]))
    groups = summary["groups"]
    write_csv_new(directory / "summary.csv", [{"data_class": summary["data_class"], "research_status": summary["research_status"], **g} for g in groups],
                  ["data_class", "research_status"] + list(groups[0]))
    return summary
