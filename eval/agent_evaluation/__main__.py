"""L3 command line. Offline commands never instantiate a live transport."""
from __future__ import annotations

from eval.implementation import implementation_manifest

import argparse
import math
from pathlib import Path

from .common import (DESIGN, PACKAGE, REPO, PROTOCOL_VERSION, Blocked, EvaluationError, digest,
                     file_hash, read_json, verify_design, write_json)


def cost_plan(run_dir: Path, use_pilot=False) -> dict:
    manifest = read_json(run_dir / "input_manifest.json")
    config = read_json(run_dir / "config.snapshot.json")
    main = [a for a in manifest["artifacts"] if a["planned_group"] == "MAIN"]
    targets = [t for t in manifest["cohort"]["targets"] if t["planned_group"] == "MAIN"]
    tasks = sum(math.ceil(len(a["claims"]) / config["judging"]["claims_per_batch"]) for a in main)
    result = {"content_batches_A": tasks, "content_batches_B": tasks, "main_targets": len(targets),
        "source_build_verify_targets": len(targets), "behavior_pairs_max_per_judge": len(main)*2,
        "additional_work": ["source adjudication", "all disagreements", "stratified 10% agreement audit",
                            "stratified 10% stability for A/B", "tool loops", "bounded retries"],
        "api_requests": None, "estimated_cost_cny": None, "campaign_cap_cny": config["budgets"]["campaign_hard_cap"],
        "price_status": "NOT_BOUND", "projection_status": "BLOCKED", "blockers": [
            "Verified provider bindings and all billing dimensions required", "Frozen source scenes not established",
            "Pilot family-level ledger and conservative upper-quantile projection required"],
        "cap_is_not_cost_estimate": True, "real_model_calls": 0}
    if use_pilot:
        from .common import read_jsonl
        from .transport import binding_blockers
        calls = read_jsonl(run_dir / "call_ledger.jsonl")
        pilot_calls = [r for r in calls if r["target_id"] in config["pilot_targets"]]
        blockers = binding_blockers(config)
        if not pilot_calls:
            blockers.append("NO_PILOT_LEDGER")
        if any(r["payload"]["provider"] == "MOCK" or r["payload"]["cost_cny"] is None for r in pilot_calls):
            blockers.append("MOCK_OR_INCOMPLETE_BILLING")
        if not blockers:
            from .agents.roles import role_binding
            from .budget import Budget
            from .common import confined
            per_task = {}
            for r in pilot_calls:
                key = (r["role_id"], r["payload"]["task_id"])
                per_task[key] = per_task.get(key, 0) + r["payload"]["cost_cny"]
            families = {}
            for (role, _), cost in per_task.items():
                families.setdefault(role, []).append(cost)
            claims = sum(len(a["claims"]) for a in main)
            scenarios = len(main)*2
            # Worst case all content assertions/scenarios disagree. Unknown reference
            # complexity is represented by per-task request caps, not invented checkpoint counts.
            reviews = claims + scenarios + len(targets)
            strata_units = {}
            for a in main:
                strata_units[a["project_id"], "CONTENT"] = strata_units.get((a["project_id"], "CONTENT"), 0) + len(a["claims"])
                strata_units[a["project_id"], "BEHAVIOR"] = strata_units.get((a["project_id"], "BEHAVIOR"), 0) + 2
            stable_tasks = sum(math.ceil(n*.1) for n in strata_units.values())
            counts = {"SCENE_BUILD_A": len(targets), "SCENE_VERIFY_B": len(targets), "CONTENT_A": tasks, "CONTENT_B": tasks,
                      "BEHAVIOR_A": scenarios, "BEHAVIOR_B": scenarios, "REVIEW_C_BLIND": reviews,
                      "REVIEW_C_FINAL": reviews, "STABILITY_A": stable_tasks, "STABILITY_B": stable_tasks}
            estimates = {}
            for role, count in counts.items():
                values = sorted(families.get(role, []))
                binding = config["models"][role_binding(role)]
                fallback = sum(binding["per_request_upper_bound"].values()) * config["judging"]["max_requests_per_task"]
                per_unit = values[max(0, math.ceil(.9*len(values))-1)] if values else fallback
                estimates[role] = {"workload_upper_tasks": count, "per_task_cny": per_unit,
                    "basis": "PILOT_P90" if values else "VERIFIED_REQUEST_CAP_FALLBACK", "projected_cny": count*per_unit*1.2}
            ledger = Budget(confined(REPO, config["campaign_ledger"]), config["budgets"]["campaign_hard_cap"])
            remaining = ledger.summary()["cap_cny"] - ledger.summary()["accounted_cny"]
            estimate = sum(e["projected_cny"] for e in estimates.values())
            result.update(family_cost_projection=estimates, estimated_cost_cny=estimate, remaining_campaign_cny=remaining,
                          price_status="BOUND", projection_runtime_hash=frozen_runtime_hash(config, manifest),
                          projection_status="VERIFIED_WITHIN_REMAINING_CAMPAIGN_CAP" if estimate <= remaining else "EXCEEDS_REMAINING_CAMPAIGN_CAP",
                          blockers=[] if estimate <= remaining else ["Budget insufficient; do not reduce methods or targets automatically"],
                          conservative_note="Includes pilot tasks again in workload upper count; actual resume skips successful tasks. P90 is a projection, not a hard cap guarantee.")
        else:
            result["blockers"] = blockers
    write_json(run_dir / "cost_plan.json", result)
    return result


def validate_run(run_dir: Path):
    from .schemas import schema
    from .inventory import assert_inputs_current
    verify_design()
    schema()
    manifest = assert_inputs_current(run_dir)
    result = {"schema": "DRAFT_2020_12_PASS", "input_hashes": "PASS", "design_hashes": "PASS",
              "summary": manifest["summary"], "semantic_status": "NOT_RUN", "real_model_calls": 0}
    write_json(run_dir / "validation.json", result)
    return result


def frozen_runtime_hash(config, manifest):
    return digest({"config": config, "manifest": manifest, "prompts": file_hash(PACKAGE / "prompts/PROMPTS.md"),
                   "schema": file_hash(DESIGN / "records.schema.json"), "protocol": PROTOCOL_VERSION,
                   "implementation": implementation_manifest("l3")})


def live_run(args, config):
    from .inventory import assert_inputs_current
    from .transport import LiveTransport
    from .orchestrator import Orchestrator
    manifest = assert_inputs_current(args.run_dir)
    # Constructing this object only validates prerequisites. No probe requests.
    transport = LiveTransport(config)
    if config.get("input_batch_status") != "CONFIRMED":
        raise Blocked("Selected historical input batch has not been confirmed")
    calibration = args.run_dir / "model_fixture_acceptance.json"
    if not calibration.exists() or read_json(calibration).get("status") != "PASS":
        raise Blocked("Live constructed-model fixtures must pass first")
    acceptance = read_json(calibration)
    if acceptance.get("runtime_hash") != frozen_runtime_hash(config, manifest):
        raise Blocked("Calibration binding/input/prompt drift")
    if args.phase == "formal":
        gate = args.run_dir / "pilot_acceptance.json"
        if not gate.exists() or read_json(gate).get("status") != "PASS":
            raise Blocked("Pilot acceptance and budget projection required")
        projection = read_json(args.run_dir / "cost_plan.json")
        if projection.get("projection_status") != "VERIFIED_WITHIN_REMAINING_CAMPAIGN_CAP":
            raise Blocked("Formal remaining workload cost not verified")
        if projection.get("projection_runtime_hash") != frozen_runtime_hash(config, manifest):
            raise Blocked("Cost projection binding drift")
    ids = config["pilot_targets"] if args.phase == "pilot" else [t["target_id"] for t in manifest["cohort"]["targets"] if t["planned_group"] == "MAIN"]
    runner = Orchestrator(args.run_dir, config, manifest, transport)
    if args.phase == "formal":
        from .common import append_jsonl
        for tid in config["pilot_targets"]:
            if tid in runner.references:
                append_jsonl(args.run_dir / "carry_forward.jsonl", {"target_id": tid, "status": "PILOT_CARRY_FORWARD",
                    "runtime_hash": frozen_runtime_hash(config, manifest), "scenario_version": runner.references[tid]["scenario_version"]})
    runner.run(ids)
    if args.phase == "pilot":
        from .live_acceptance import assess_pilot
        assess_pilot(args.run_dir, config, manifest)
    return {"phase": args.phase, "status": "RECORDS_AVAILABLE_REQUIRES_ACCEPTANCE"}


def main(argv=None):
    parser = argparse.ArgumentParser(description="AUCMR L3 source content and selected behavior evaluation")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("inventory", "validate", "fixtures", "plan-cost", "run", "export"):
        child = sub.add_parser(command)
        child.add_argument("--run-dir", type=Path, required=True)
        if command == "inventory":
            child.add_argument("--config", type=Path, required=True)
        if command == "validate":
            child.add_argument("--offline", action="store_true", required=True)
        if command in {"fixtures", "run"}:
            child.add_argument("--transport", choices=("mock", "live"), required=True)
        if command == "run":
            child.add_argument("--phase", choices=("pilot", "formal"), required=True)
            child.add_argument("--resume", action="store_true")
        if command == "plan-cost":
            child.add_argument("--use-pilot-ledger", action="store_true")
        if command == "export":
            child.add_argument("--view", choices=("A_FIRST", "B_FIRST", "RECONCILED"), default="RECONCILED")
            child.add_argument("--paper", action="store_true")
    args = parser.parse_args(argv)
    args.run_dir = args.run_dir.resolve()
    if not args.run_dir.is_relative_to((REPO / "outputs/agent_evaluation").resolve()):
        parser.error("run-dir must be under this repository outputs/agent_evaluation")
    try:
        if args.command == "inventory":
            from .inventory import inventory
            from .transport import deny_network
            with deny_network():
                result = inventory(args.config, args.run_dir)
        elif args.command == "validate":
            from .transport import deny_network
            with deny_network():
                result = validate_run(args.run_dir)
        elif args.command == "plan-cost":
            result = cost_plan(args.run_dir, args.use_pilot_ledger)
        elif args.command == "fixtures":
            config = read_json(args.run_dir / "config.snapshot.json")
            if args.transport == "live":
                from .transport import LiveTransport
                LiveTransport(config)  # NOT_BOUND stops before any real network operation.
                from .live_acceptance import run_model_fixtures
                result = run_model_fixtures(args.run_dir, config)
            else:
                from .fixtures import execute_fixtures
                result = execute_fixtures(args.run_dir, config)
        elif args.command == "run":
            if args.transport != "live":
                raise Blocked("Use fixtures --transport mock; mock cannot score registered real outputs")
            result = live_run(args, read_json(args.run_dir / "config.snapshot.json"))
        else:
            from .export import export_tables
            manifest = read_json(args.run_dir / "input_manifest.json")
            config = read_json(args.run_dir / "config.snapshot.json")
            state = read_json(args.run_dir / "stage_state.json")
            if state.get("state") != "RECONCILED" or state.get("semantic_status") == "MOCK_ONLY_NOT_RESEARCH":
                raise Blocked("No validated live decisions to export")
            from .inventory import assert_inputs_current
            assert_inputs_current(args.run_dir)
            references = read_json(args.run_dir / "scenario_freeze.json")
            if state.get("reference_versions") != {k: v["scenario_version"] for k, v in references.items()}:
                raise Blocked("Reference corrected after scoring; all affected methods require re-evaluation")
            runtime_hash = digest({"config": config, "manifest": manifest,
                                   "prompt": file_hash(PACKAGE / "prompts/PROMPTS.md"),
                                   "implementation": implementation_manifest("l3")})
            if state.get("runtime_hash") != runtime_hash:
                raise Blocked("Scoring binding changed; export refused")
            provenance = {"protocol_version": PROTOCOL_VERSION, "run_id": args.run_dir.name,
                "input_manifest_hash": digest(manifest), "cohort_hash": manifest["cohort_hash"],
                "source_hashes": {s["source_id"]: s["sha256"] for s in manifest["sources"]},
                "output_hashes": {a["artifact_id"]: a["sha256"] for a in manifest["artifacts"]},
                "prompt_hash": file_hash(PACKAGE / "prompts/PROMPTS.md"), "model_binding_hash": digest(config["models"]),
                "reference_versions": {k: v["scenario_version"] for k,v in references.items()},
                "report_scope": "FORMAL" if set(state["target_ids"]) == {t["target_id"] for t in manifest["cohort"]["targets"] if t["planned_group"] == "MAIN"} else "PILOT_ONLY",
                "semantic_status": state["semantic_status"], "transport": "live"}
            export_tables(args.run_dir / "paper", read_json(args.run_dir / f"aggregate_{args.view}.json"), provenance)
            result = {"export": str(args.run_dir / "paper"), "report_scope": provenance["report_scope"]}
        import json
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (EvaluationError, OSError) as exc:
        parser.exit(2, f"BLOCKED: {exc}\n")


if __name__ == "__main__":
    main()
