"""Auditable JSON/CSV/TeX export, with fixture data physically separated."""
from __future__ import annotations

import csv
from pathlib import Path

from .common import EvaluationError, canonical, write_json


def csv_table(path: Path, rows: list[dict]) -> None:
    keys = list(dict.fromkeys(k for row in rows for k in row))
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, keys)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: canonical(v) if isinstance(v, (dict, list)) else v for k, v in row.items()})


def tex_cell(value, percent=False):
    if value is None:
        return "--"
    if percent:
        return f"{100 * value:.1f}"
    text = canonical(value) if isinstance(value, (dict, list)) else str(value)
    return text.replace("\\", r"\textbackslash{}").replace("_", r"\_").replace("&", r"\&").replace("%", r"\%")


def tex_table(rows: list[dict], columns: list[str], marker: str) -> str:
    out = [f"% {marker}", r"\begin{tabular}{" + "l" * len(columns) + "}",
           " & ".join(tex_cell(c) for c in columns) + r" \\"]
    for row in rows:
        out.append(" & ".join(tex_cell(row.get(c), c.startswith("cp_")) for c in columns) + r" \\")
    return "\n".join(out + [r"\end{tabular}", "% Null means unavailable, never zero. Unresolved bounds are not confidence intervals."])+"\n"


def export_tables(destination: Path, result: dict, provenance: dict, *, fixture=False) -> None:
    if fixture and "fixtures" not in destination.parts:
        raise EvaluationError("Mock export must be inside a fixtures directory")
    if not fixture and provenance.get("transport") != "live":
        raise EvaluationError("Mock cannot write formal paper_values")
    required = {"protocol_version", "run_id", "input_manifest_hash", "cohort_hash", "source_hashes", "output_hashes",
                "prompt_hash", "model_binding_hash", "reference_versions", "report_scope", "semantic_status"}
    if not required <= provenance.keys():
        raise EvaluationError("Missing export provenance")
    destination.mkdir(parents=True, exist_ok=True)
    marker = "FIXTURE / NOT_RESEARCH_DATA" if fixture else "RESEARCH_RECORDS_WITH_COMPLETENESS_STATUS"
    write_json(destination / "paper_values.json", {"data_type": marker, "provenance": provenance, **result})
    for name in ("per_artifact", "per_scenario", "paired_comparisons", "content_table", "behavior_table", "errors_table"):
        csv_table(destination / f"{name}.csv", result[name])
        write_json(destination / f"{name}.json", result[name])
    checkpoints = [{k: r[k] for k in ("target_id", "artifact_id", "method", "scenario_id")} | cp
                   for r in result["per_scenario"] for cp in r["checkpoints"]]
    csv_table(destination / "per_checkpoint.csv", checkpoints)
    tables = {
        "content": ("content_table", ["project_id", "method", "main_targets", "valid_nonempty", "empty_outputs",
            "technical_failed_artifacts", "n_final", "k_keep", "d_delete", "u_unresolved", "f_failed",
            "common_cp_target_count", "cp_confirmed", "cp_lower", "cp_upper"]),
        "behavior": ("behavior_table", ["project_id", "method", "scenario_kind", "frozen_scenarios", "RETAINED", "PARTIAL",
            "MISSING", "DISTORTED", "UNRESOLVED", "TECHNICAL_INCOMPLETE", "unestablished_targets"]),
        "errors": ("errors_table", ["project_id", "method", "delete_reasons", "unresolved_reasons"])}
    for name, (key, columns) in tables.items():
        (destination / f"table_l3_{name}.tex").write_text(tex_table(result[key], columns, marker), encoding="utf-8")
    report = {"data_type": marker, "report_scope": provenance["report_scope"],
              "interpretations": [r["interpretation"] for r in result["paired_comparisons"]],
              "scientific_results": "NOT_ESTABLISHED" if fixture else provenance["semantic_status"],
              "negative_results_retained": True}
    write_json(destination / "reportability.json", report)
    (destination / "limitations_generated.md").write_text(marker + "\n\n结果按文档分别描述。未决界限不是置信区间。场景检查不证明全文完整。\n"
        "没有相同修订前制品和控制条件时，只报告最终系统比较，不报告 DRA 净修复因果收益。\n"
        "L4 PCmpS 和论文旧 RCR 表述需要后续单独同步，本实现不修改论文。\n", encoding="utf-8")
