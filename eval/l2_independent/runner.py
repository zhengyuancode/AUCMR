"""Offline entry operations. All runs are new, separate directories."""
from collections import Counter
from pathlib import Path

from .common import (L2Error, OUTPUT_ROOT, REPO, PROTOCOL_VERSION, PROMPT_VERSION,
                     confined, digest, file_hash, new_run, read_json, write_json)
from .evaluation import cache_identity, evaluate
from .fixtures import fixture_cases, fixture_config, client_for
from .inputs import collect, load_config, structure_row
from .integration import check_l3_alignment
from .offline import offline_guard
from .protocol import prompt_hash
from .reporting import csv_table, export_summary


def _seal(run_dir, kind, **extra):
    hashes = {p.relative_to(run_dir).as_posix(): file_hash(p)
              for p in sorted(run_dir.rglob("*")) if p.is_file()}
    manifest = {
        "run_id": run_dir.name, "kind": kind, "protocol_version": PROTOCOL_VERSION,
        "prompt_version": PROMPT_VERSION, "prompt_hash": prompt_hash(),
        "real_model_calls": 0, "real_semantic_evaluation": "NOT_RUN", "formal_experiment": "NOT_RUN",
        "files": hashes, **extra,
    }
    write_json(run_dir / "run_manifest.json", manifest)
    return manifest


def inventory(config_path, *, repo=REPO):
    with offline_guard() as audit:
        config = load_config(config_path)
        alignment = check_l3_alignment(config, repo)
        artifacts = collect(config, repo)
        rows = [structure_row(a) for a in artifacts]
        for row in rows:
            row.update(target_id=row["task_id"], project_id=row["source_project"])
        by_pair = {(a["task_id"], a["method"]): a for a in artifacts}
        for row in alignment["joins"]:
            row["l2_artifact_id"] = by_pair[row["task_id"], row["method"]]["artifact_id"]
        states = dict(Counter(a["input_status"] for a in artifacts))
        hash_drift = sum(not a["hash_matches_binding"] for a in artifacts)
        summary = {
            "data_origin": "REAL_INPUT_STRUCTURE_ONLY", "semantic_status": "NOT_RUN",
            "registered_targets": len(config["targets"]), "methods": len(config["methods"]),
            "registered_pairs": len(artifacts), "input_states": states,
            "assertion_count_observed": sum(len(a["assertions"]) for a in artifacts),
            "empty_slots_observed": sum(len(a["empty_slots"]) for a in artifacts),
            "hash_drift_count": hash_drift, "l3_alignment": alignment["status"],
            "binding_validation": "PASS" if not hash_drift and alignment["status"] == "PASS" else "DRIFT",
            "real_model_calls": 0, "structural_errors_are_preserved": True,
        }
        groups = []
        for project in sorted({t["source_project"] for t in config["targets"]}):
            for method in config["methods"]:
                selected = [a for a in artifacts if a["source_project"] == project and a["method"] == method]
                groups.append({"project_id": project, "method": method,
                    "expected_target_count": len(selected),
                    "input_states": dict(Counter(a["input_status"] for a in selected)),
                    "assertions_observed": sum(len(a["assertions"]) for a in selected),
                    "empty_slots_observed": sum(len(a["empty_slots"]) for a in selected),
                    "semantic_status": "NOT_RUN", "completed_semantic_checks": 0})
        run_dir = new_run("inventory", Path(repo) / "outputs/agent_l2_independent")
        write_json(run_dir / "config.snapshot.json", config)
        write_json(run_dir / "input_inventory.json", {
            "data_origin": "REAL_INPUT_STRUCTURE_ONLY", "semantic_status": "NOT_RUN",
            "summary": summary, "artifacts": rows})
        csv_table(run_dir / "input_inventory.csv", rows)
        write_json(run_dir / "structural_summary.json", groups)
        csv_table(run_dir / "structural_summary.csv", groups)
        write_json(run_dir / "l3_alignment.json", alignment)
        csv_table(run_dir / "l3_join_map.csv", alignment["joins"])
        _seal(run_dir, "INVENTORY", data_origin="REAL_INPUT_STRUCTURE_ONLY", network_audit=audit)
        return {"run_dir": str(run_dir), **summary}


def run_mock():
    with offline_guard() as audit:
        config, cases = fixture_cases()
        run_dir = new_run("mock")
        write_json(run_dir / "config.snapshot.json", config)
        records = []
        for case in cases:
            client = client_for(case, config)
            record = evaluate(case["artifact"], config, client, run_dir.name)
            records.append(record)
            write_json(run_dir / "fixtures" / "records" / (record["artifact_id"] + ".json"), record)
        artifacts = [c["artifact"] for c in cases]
        write_json(run_dir / "fixtures" / "artifacts.json", artifacts)
        expectations = [{"task_id": c["artifact"]["task_id"], "expected_status": c["expected_status"],
                         "actual_status": r["status"], "pass": c["expected_status"] == r["status"],
                         "research_data_status": "NOT_RESEARCH_DATA"}
                        for c, r in zip(cases, records)]
        write_json(run_dir / "fixtures" / "fixture_checks.json", {
            "checks": expectations, "meaning": "MOCK_PIPELINE_ONLY_NOT_MODEL_ACCURACY"})
        export_summary(run_dir / "fixtures" / "export", records, config)
        passed = all(e["pass"] for e in expectations)
        _seal(run_dir, "MOCK_ONLY", data_origin="FIXTURE", research_data_status="NOT_RESEARCH_DATA",
              fixture_plumbing_status="PASS" if passed else "FAIL", network_audit=audit,
              mock_calls=sum(r["model_call_count"] for r in records))
        return {"run_dir": str(run_dir), "status": "PASS" if passed else "FAIL",
                "fixture_records": len(records), "real_model_calls": 0,
                "research_data_status": "NOT_RESEARCH_DATA",
                "real_semantic_evaluation": "NOT_RUN"}


def reexport(source_run):
    with offline_guard() as audit:
        source = Path(source_run).resolve()
        if not source.is_relative_to(OUTPUT_ROOT.resolve()) or source == OUTPUT_ROOT.resolve():
            raise L2Error("Source must be an explicit run under outputs/agent_l2_independent")
        manifest = read_json(source / "run_manifest.json")
        if manifest.get("kind") != "MOCK_ONLY" or manifest.get("data_origin") != "FIXTURE":
            raise L2Error("This offline CLI exports saved fixture evaluations only; real inventories are not scores")
        if manifest["prompt_hash"] != prompt_hash() or manifest["protocol_version"] != PROTOCOL_VERSION:
            raise L2Error("Protocol or prompt drift; refuse silent reuse")
        for rel, expected in manifest["files"].items():
            path = confined(source, rel)
            if not path.is_file() or file_hash(path) != expected:
                raise L2Error("Saved run integrity mismatch: " + rel)
        config = read_json(source / "config.snapshot.json")
        if digest(config) != digest(fixture_config()):
            raise L2Error("Fixture model/config changed; create a new run")
        artifacts = read_json(source / "fixtures/artifacts.json")
        current_artifacts = [c["artifact"] for c in fixture_cases()[1]]
        if digest(artifacts) != digest(current_artifacts):
            raise L2Error("Fixture input changed; create a new run")
        records = []
        for a in artifacts:
            r = read_json(source / "fixtures/records" / (a["artifact_id"] + ".json"))
            if r["cache_key"] != cache_identity(a, config):
                raise L2Error("Input/model/prompt identity mismatch")
            records.append(r)
        destination = new_run("export")
        write_json(destination / "source_run.json", {
            "source_run": source.name, "source_manifest_hash": file_hash(source / "run_manifest.json")})
        export_summary(destination / "fixtures" / "export", records, config)
        _seal(destination, "MOCK_EXPORT_ONLY", data_origin="FIXTURE",
              research_data_status="NOT_RESEARCH_DATA", network_audit=audit)
        return {"run_dir": str(destination), "source_run": source.name, "status": "PASS",
                "real_model_calls": 0, "research_data_status": "NOT_RESEARCH_DATA"}
