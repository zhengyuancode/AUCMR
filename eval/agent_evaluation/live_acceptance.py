"""Future live calibration and pilot acceptance. Never invoked by offline execution."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from .common import DESIGN, Blocked, digest, read_json, read_jsonl, write_json
from .fixtures import fixture_manifest
from .orchestrator import Orchestrator
from .agents.roles import materials
from .transport import LiveTransport


def run_model_fixtures(run_dir: Path, config: dict) -> dict:
    """Test A/B/C against all 16 frozen expectations; reuse no scripted labels as responses."""
    from .__main__ import frozen_runtime_hash
    from .inventory import assert_inputs_current
    real_manifest = assert_inputs_current(run_dir)
    transport = LiveTransport(config)
    root = run_dir / "fixtures/live"
    if (root/"fixture_manifest.json").exists():
        manifest = read_json(root/"fixture_manifest.json")
        specs = {s["id"]: s for s in read_json(DESIGN/"fixtures.json")["fixtures"]}
    else:
        manifest, specs = fixture_manifest(root, methods=["AUCMR"])
        write_json(root/"fixture_manifest.json", manifest)
    runner = Orchestrator(root, config, manifest, transport)
    source_checks = []
    for target in manifest["cohort"]["targets"]:
        tid = target["target_id"]
        if tid not in runner.references:
            runner.references[tid] = runner.source_stage(target)
            write_json(root/"scenario_freeze.json", runner.references)
        source_checks.append({"fixture": tid, "core_established": runner.references[tid]["core_status"] == "ESTABLISHED"})
        if not source_checks[-1]["core_established"]:
            # The final predicate already requires every source check to pass.
            # Preserve that failure immediately rather than pay for doomed work.
            report = {"data_type": "CONSTRUCTED_MODEL_CALIBRATION_NOT_RESEARCH_DATA", "status": "FAIL",
                      "source_checks": source_checks, "results": [], "per_model": {},
                      "reason": "SOURCE_CORE_UNESTABLISHED", "stopped_at": tid,
                      "runtime_hash": frozen_runtime_hash(config, real_manifest)}
            write_json(root/"model_fixture_report.json", report)
            write_json(run_dir/"model_fixture_acceptance.json", report)
            return report
    results = []
    for a in manifest["artifacts"]:
        tid = a["target_id"]
        spec, target, reference = specs[tid], runner.targets[tid], runner.references[tid]
        family = "CONTENT" if tid.startswith("C") else "BEHAVIOR"
        # Behavioral calibration uses the frozen constructed checkpoint, not a model-chosen easier one.
        scene = deepcopy(reference["scenarios"][0]) if reference["scenarios"] else None
        if family == "BEHAVIOR":
            if scene is None:
                raise Blocked("Constructed source build/verify failed to establish core")
            scene["checkpoints"] = [{**scene["checkpoints"][0], **spec["checkpoint"],
                                    "checkpoint_id": tid + ".gold.checkpoint"}]
            # The constructed gold specifies actual page/image access, not a summary substitute.
            source = next(s for s in manifest["sources"] if s["project_id"] == tid)
            scene["checkpoints"][0]["locators"] = [{"source_id": source["source_id"], "source_sha256": source["sha256"],
                "pdf_page": i+1, "bbox": None, "quote": None if spec.get("image_only_table") else text,
                "region_description": "Constructed image table" if spec.get("image_only_table") else "Constructed source page",
                "modality": "TABLE" if spec.get("image_only_table") else "TEXT"} for i,text in enumerate(spec["source_pages"])]
        requested = [a["claims"][spec["judge_index"]]["claim_id"]] if family == "CONTENT" else [tid + ".gold.checkpoint"]
        base = materials(family, target, manifest["sources"], artifact=a, reference=reference,
                         requested_ids=requested, scenario=scene)
        for judge in ("A", "B", "C"):
            role = family + "_" + judge if judge != "C" else "REVIEW_C_BLIND"
            task = base if judge != "C" else {**base, "original_role": family+"_A"}
            rows = runner.executor.execute(task_id=f"calibration/{tid}/{judge}", role=role,
                view=judge+"_FIRST" if judge != "C" else "C_BLIND", kinds=[family.lower()], materials=task,
                source=runner.access(target, role, family=family, scenario=scene),
                validate=runner.validators(family, a, scene, requested), target_id=tid,
                artifact_id=a["artifact_id"], version=reference["scenario_version"])
            label = rows[0]["payload"]["label"]
            results.append({"fixture": tid, "judge": judge, "label": label, "expected": spec["expected"],
                "correct": label == spec["expected"], "critical_false_accept": spec["critical_false_accept"] and label in {"KEEP", "RETAINED"}})
    per_model = {j: {"correct": sum(r["correct"] for r in results if r["judge"] == j),
                     "critical_false_acceptances": sum(r["critical_false_accept"] for r in results if r["judge"] == j)} for j in ("A", "B", "C")}
    passed = all(r["correct"] >= config["acceptance"]["fixture_min_correct"] and r["critical_false_acceptances"] == 0 for r in per_model.values()) and all(s["core_established"] for s in source_checks)
    report = {"data_type": "CONSTRUCTED_MODEL_CALIBRATION_NOT_RESEARCH_DATA", "status": "PASS" if passed else "FAIL",
              "per_model": per_model, "source_checks": source_checks, "results": results,
              "runtime_hash": frozen_runtime_hash(config, real_manifest)}
    write_json(root/"model_fixture_report.json", report)
    write_json(run_dir/"model_fixture_acceptance.json", report)
    return report


def assess_pilot(run_dir: Path, config: dict, manifest: dict) -> dict:
    """Engineering gate only; never uses method rank or favorable result direction."""
    from .__main__ import frozen_runtime_hash
    pilot = set(config["pilot_targets"])
    aggregate = read_json(run_dir/"aggregate_RECONCILED.json")
    references = read_json(run_dir/"scenario_freeze.json")
    artifacts = [r for r in aggregate["per_artifact"] if r["target_id"] in pilot]
    scenarios = [r for r in aggregate["per_scenario"] if r["target_id"] in pilot and r["established"]]
    source_complete = all(references.get(t, {}).get("core_status") == "ESTABLISHED" for t in pilot)
    technical = all(r["state"] != "TECHNICAL_INCOMPLETE" for r in artifacts) and all(r["scenario_state"] != "TECHNICAL_INCOMPLETE" for r in scenarios)
    total = sum(r["n_final"] for r in artifacts)
    unresolved = sum(r["u_unresolved"] for r in artifacts)/total if total else None
    report = {"status": "PASS" if source_complete and technical else "FAIL", "source_complete": source_complete,
              "technical_complete": technical, "unresolved_fraction": unresolved,
              "warnings": ["UNRESOLVED_RATE_REQUIRES_CAUSE_ANALYSIS"] if unresolved is not None and unresolved > .1 else [],
              "runtime_hash": frozen_runtime_hash(config, manifest), "rank_used_for_acceptance": False}
    write_json(run_dir/"pilot_acceptance.json", report)
    return report
