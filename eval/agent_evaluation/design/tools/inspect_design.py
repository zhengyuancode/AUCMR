"""Read-only input inventory and static design checks. No model execution."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from zipfile import ZipFile
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
REPO = Path("D:/Research/02_Code/AUCMR/AUCMR")
PAPER = Path("C:/Users/JunxiHu/Downloads/软件学报2026 (20).zip")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(name: str, data) -> None:
    (ROOT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    config = read_json(ROOT / "config.json")
    cohort = read_json(ROOT / "cohort.json")
    schema = read_json(ROOT / "records.schema.json")
    fixtures = read_json(ROOT / "fixtures.json")
    checks = []

    def check(name, passed, detail=None):
        checks.append({"name": name, "status": "PASS" if passed else "FAIL", "detail": detail})

    required = ["README.md", "PROTOCOL.md", "PROMPTS.md", "CONTRACTS.md", "config.json", "cohort.json",
                "records.schema.json", "fixtures.json", "ACCEPTANCE.md", "IMPLEMENTATION_TASK.md",
                "PAPER_EXPORT.md", "paper_methods_template.tex", "REVIEW_DECISIONS.md"]
    for name in required:
        check("file_exists:" + name, (ROOT / name).is_file())
    check("seed_is_frozen_random_value", config["seed"] == 297154423)
    check("no_live_authorization", config["scope"]["live_model_calls"] is False)
    check("18_candidates", len(cohort["targets"]) == 18)
    check("14_main_targets", sum(t["planned_group"] == "MAIN" for t in cohort["targets"]) == 14)
    check("4_diagnostic_targets", sum(t["planned_group"] == "DIAGNOSTIC" for t in cohort["targets"]) == 4)
    check("7_methods", len(set(cohort["methods"])) == 7)
    check("2_pilot_targets", config["pilot_targets"] == ["JTV-01", "S16-02"])
    check("16_unique_fixtures", len(fixtures["fixtures"]) == len({f["id"] for f in fixtures["fixtures"]}) == 16)
    check("8_content_8_behavior_fixtures", Counter(f["family"] for f in fixtures["fixtures"]) ==
          {"CONTENT_CLAIM": 8, "BEHAVIOR_CHECKPOINT": 8})
    check("7_generated_slots_no_actor", len(config["generated_slots"]) == 7 and "primary_actor" not in config["generated_slots"])
    check("versions_agree", config["protocol_version"] == cohort["protocol_version"] == fixtures["protocol_version"])
    check("no_inferential_claim_defaults", config["aggregation"]["p_values"] is False and
          config["aggregation"]["confidence_intervals"] is False)

    local_refs = []
    def walk_schema(value):
        if isinstance(value, dict):
            if "$ref" in value:
                local_refs.append(value["$ref"])
            for child in value.values():
                walk_schema(child)
        elif isinstance(value, list):
            for child in value:
                walk_schema(child)
    walk_schema(schema)
    check("all_local_schema_refs_resolve", all(r.startswith("#/$defs/") and
          r.rsplit("/", 1)[1] in schema["$defs"] for r in local_refs))
    check("all_eight_record_families_declared", len(schema["oneOf"]) == 8 and
          {r["properties"]["record_kind"]["const"] for r in schema["oneOf"]} ==
          {"scope", "scene_proposal", "source_review", "content", "behavior", "review", "artifact_state", "call"})

    schema_status = "NOT_RUN_DEPENDENCY_UNAVAILABLE"
    if importlib.util.find_spec("jsonschema"):
        from jsonschema import Draft202012Validator
        Draft202012Validator.check_schema(schema)
        check("schema_meta_validation", True)
        validator = Draft202012Validator(schema)

        def example(spec):
            if "$ref" in spec:
                return example(schema["$defs"][spec["$ref"].rsplit("/", 1)[1]])
            if "const" in spec:
                return spec["const"]
            if "enum" in spec:
                return spec["enum"][0]
            if "anyOf" in spec:
                return example(spec["anyOf"][0])
            kind = spec.get("type")
            if isinstance(kind, list):
                kind = next(k for k in kind if k != "null")
            if kind == "object":
                return {k: example(v) for k, v in spec["properties"].items()}
            if kind == "array":
                return [example(spec["items"]) for _ in range(spec.get("minItems", 0))]
            if kind == "string":
                return "0" * 64 if "pattern" in spec else "FIXTURE_ONLY"
            if kind in ("integer", "number"):
                return spec.get("minimum", 0)
            if kind == "boolean":
                return False
            return None

        for record_spec in schema["oneOf"]:
            sample = example(record_spec)
            kind = sample["record_kind"]
            errors = [e.message for e in validator.iter_errors(sample)]
            check("schema_accepts_structural_fixture:" + kind, not errors, errors)
            bad = copy.deepcopy(sample)
            bad["protocol_version"] = "INVALID_PROTOCOL"
            check("schema_rejects_wrong_version:" + kind, bool(list(validator.iter_errors(bad))))
        schema_status = "STRUCTURAL_EXAMPLES_ONLY"

    target_map = {(t["project_id"], t["actor"], t["name"]): t for t in cohort["targets"]}
    aliases = {"AUCMR (CREST full)": "AUCMR", "CREST w/o cross-slot": "CREST w/o cross-slot check"}
    rows = []
    for group in ["aucmr_full", "crest_without_dra", "baselines"]:
        for path in sorted((REPO / "outputs" / group).glob("*/*.json")):
            if path.name.endswith("-evidence.json") or path.parent.name.startswith("_"):
                continue
            row = {"path": str(path), "sha256": digest(path), "size_bytes": path.stat().st_size,
                   "group": group, "project_id": path.parent.name}
            try:
                data = read_json(path)
                actor = data.get("actor")
                name = data.get("use_case_name")
                target = target_map.get((path.parent.name, actor, name))
                method_original = data.get("method")
                method = aliases.get(method_original, method_original)
                if group == "aucmr_full":
                    expected_method = "AUCMR"
                elif group == "crest_without_dra":
                    expected_method = "CREST w/o cross-slot check"
                else:
                    expected_method = next((m for m in cohort["methods"] if path.stem.endswith("_" + m)), None)
                row.update({"actor": actor, "name": name, "target_id": target["target_id"] if target else None,
                            "planned_group": target["planned_group"] if target else None,
                            "method_original": method_original, "method": method,
                            "path_expected_method": expected_method, "method_matches_path": method == expected_method,
                            "generation_version": data.get("generation_version"),
                            "artifact_revision_id": data.get("artifact_revision_id"),
                            "generation_api_provenance_status": "NOT_VERIFIED",
                            "read_status": "OK"})
                issues, empty, count = [], [], 0
                slots = data.get("slots", {})
                for slot_name in config["generated_slots"]:
                    slot = slots.get(slot_name)
                    if not isinstance(slot, dict):
                        issues.append(slot_name + ":MISSING_OR_INVALID_SLOT")
                        continue
                    claims = slot.get("claims")
                    if not isinstance(claims, list):
                        issues.append(slot_name + ":MISSING_OR_INVALID_CLAIMS")
                        continue
                    count += len(claims)
                    if not claims:
                        if str(slot.get("raw_text") or "").strip():
                            issues.append(slot_name + ":RAW_TEXT_WITHOUT_CLAIMS")
                        else:
                            empty.append(slot_name)
                    for index, claim in enumerate(claims):
                        if not isinstance(claim, dict) or not str(claim.get("content") or "").strip():
                            issues.append(f"{slot_name}:{index}:INVALID_CLAIM")
                row.update({"n_final": count, "empty_slots": empty, "structure_issues": issues})
            except (ValueError, TypeError, AttributeError) as exc:
                row.update({"read_status": "PARSE_FAILED", "error": str(exc)})
            rows.append(row)

    docs = [{**doc, "sha256": digest(Path(doc["pdf"])), "page_count_status": "DECLARED_NOT_RECOUNTED"}
            for doc in cohort["source_documents"]]
    with ZipFile(PAPER) as archive:
        entries = [n for n in archive.namelist() if Path(n).name == "main-zh.tex"]
        if len(entries) != 1:
            raise ValueError("main-zh.tex member is not unique")
        member = entries[0]
        tex_hash = hashlib.sha256(archive.read(member)).hexdigest()
    draft_path = Path(config["source_seed_units"])
    draft = [json.loads(line) for line in draft_path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    inventory = {
        "protocol_version": config["protocol_version"],
        "inventory_status": "CANDIDATE_SNAPSHOT_NOT_FORMAL_COHORT_FREEZE",
        "created_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "semantic_evaluation_status": "NOT_PERFORMED", "model_calls": 0,
        "source_documents": docs,
        "paper": {"zip": str(PAPER), "zip_sha256": digest(PAPER), "member": member, "tex_sha256": tex_hash},
        "draft_inventory": {"path": str(draft_path), "sha256": digest(draft_path), "unit_count": len(draft),
                            "draft_status": dict(Counter(d.get("draft_status") for d in draft)),
                            "human_review_status": dict(Counter(d.get("human_review_status") for d in draft)),
                            "interpretation": "AI_DRAFT_NOT_EXPERT_GOLD"},
        "artifacts": rows,
        "summary": {"artifact_files": len(rows), "main_artifact_files": sum(r.get("planned_group") == "MAIN" for r in rows),
                    "diagnostic_artifact_files": sum(r.get("planned_group") == "DIAGNOSTIC" for r in rows),
                    "main_final_claims": sum(r.get("n_final", 0) for r in rows if r.get("planned_group") == "MAIN"),
                    "all_final_claims": sum(r.get("n_final", 0) for r in rows),
                    "files_with_structure_issues": sum(bool(r.get("structure_issues")) for r in rows),
                    "main_files_with_structure_issues": sum(bool(r.get("structure_issues")) for r in rows if r.get("planned_group") == "MAIN"),
                    "main_empty_outputs": sum(r.get("n_final") == 0 and not r.get("structure_issues") for r in rows if r.get("planned_group") == "MAIN"),
                    "main_content_batches_per_judge": sum((r.get("n_final", 0) + 5) // 6 for r in rows if r.get("planned_group") == "MAIN"),
                    "unmatched_target_files": sum(r.get("target_id") is None for r in rows),
                    "method_path_mismatch_files": sum(not r.get("method_matches_path", False) for r in rows)},
        "excluded_roots": ["outputs/history", "results", "results_baselines", "outputs/agent_evaluation"],
        "exclusion_reason": "No mixing batches or historical/evaluator artifacts without an explicit new input binding."
    }
    check("candidate_snapshot_126_files", len(rows) == 126)
    check("candidate_snapshot_98_main", inventory["summary"]["main_artifact_files"] == 98)
    check("all_candidate_targets_matched", inventory["summary"]["unmatched_target_files"] == 0)
    check("all_candidate_methods_match_paths", inventory["summary"]["method_path_mismatch_files"] == 0)
    pairs = [(r.get("target_id"), r.get("method")) for r in rows]
    check("unique_target_method_pairs", len(pairs) == len(set(pairs)))
    check("draft_never_promoted_to_expert_gold", all(d.get("human_review_status") == "NOT_REVIEWED" for d in draft))
    save("input_inventory.json", inventory)
    report = {"protocol_version": config["protocol_version"], "scope": "STATIC_DESIGN_AND_INPUT_STRUCTURE_ONLY",
              "checks": checks, "passed": sum(c["status"] == "PASS" for c in checks),
              "failed": sum(c["status"] == "FAIL" for c in checks), "schema_check": schema_status,
              "implementation_status": "NOT_IMPLEMENTED", "offline_runtime_O01_O24": "NOT_RUN",
              "model_fixture_tests": "NOT_RUN", "formal_semantic_evaluation": "NOT_RUN",
              "model_calls": 0, "model_cost_cny": 0, "scientific_results_status": "NOT_ESTABLISHED",
              "warnings": ["Structural checks do not validate Agent judgments or scholarly claims.",
                           "Scenario records and provider bindings have not been created by a live evaluation.",
                           "The 500 CNY cap is not an estimate of full formal cost.",
                           "Original generation API provenance has not been verified."]}
    save("design_validation.json", report)
    print(json.dumps({"checks_passed": report["passed"], "checks_failed": report["failed"],
                      "schema_check": schema_status, "inventory": inventory["summary"],
                      "model_calls": 0, "outputs": ["input_inventory.json", "design_validation.json"]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
