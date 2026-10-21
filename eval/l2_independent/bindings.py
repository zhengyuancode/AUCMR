"""Explicit server-local input binding, independent of L3 model and machine paths."""
import copy
from pathlib import Path

from .common import (L2Error, REPO, confined, digest, file_hash, new_run,
                     read_json, write_json)
from .inputs import collect, load_config

CONTRACT_VERSION = "L2_LOCAL_INPUT_CONTRACT_1"


def input_contract(config):
    return {
        "version": CONTRACT_VERSION,
        "input_batch_id": config["input_batch_id"],
        "targets": copy.deepcopy(config["targets"]),
        "methods": list(config["methods"]),
        "bindings": copy.deepcopy(config["input_bindings"]),
    }


def _imported_pairs(manifest):
    rows = manifest.get("artifacts", manifest.get("bindings"))
    if not isinstance(rows, list):
        raise L2Error("Imported manifest must contain explicit artifacts or bindings")
    pairs = {}
    for row in rows:
        task = row.get("task_id", row.get("target_id"))
        method = row.get("method")
        path = row.get("relative_path", row.get("input_path", row.get("path")))
        if not isinstance(path, str):
            raise L2Error("Imported manifest lacks a relative input path")
        path = path.replace("\\", "/")
        if not path.startswith("outputs/"):
            marker = "/outputs/"
            if path.count(marker) != 1:
                raise L2Error("Imported path cannot be matched to the outputs directory")
            path = "outputs/" + path.split(marker, 1)[1]
        key = (task, method)
        if key in pairs:
            raise L2Error("Imported manifest has duplicate task/method pairs")
        pairs[key] = (path, row.get("sha256", row.get("input_hash")))
    return pairs


def prepare_inputs(config_path, *, repo=REPO, manifest_path=None, allow_fixture=False):
    repo = Path(repo).resolve()
    config = load_config(config_path, allow_fixture=allow_fixture)
    expected = {(b["task_id"], b["method"]): (b["path"], b["sha256"])
                for b in config["input_bindings"]}
    imported = None
    if manifest_path is not None:
        manifest_path = Path(manifest_path).resolve()
        if _imported_pairs(read_json(manifest_path)) != expected:
            raise L2Error("Imported targets, methods, paths or content hashes changed; bind a new batch explicitly")
        imported = {"sha256": file_hash(manifest_path), "filename": manifest_path.name}
    artifacts = collect(config, repo, allow_fixture=allow_fixture)
    if any(not a["hash_matches_binding"] for a in artifacts):
        raise L2Error("Local input content or availability differs from the frozen batch")
    run_dir = new_run("binding", repo / "outputs/agent_l2_independent")
    manifest = {
        "contract": input_contract(config),
        "contract_hash": digest(input_contract(config)),
        "data_origin": config["data_origin"],
        "semantic_status": "NOT_RUN",
        "real_model_calls": 0,
        "imported_manifest": imported,
        "artifacts": [
            {"task_id": a["task_id"], "source_project": a["source_project"], "method": a["method"],
             "path": a["input_path"], "sha256": a["input_hash"], "input_status": a["input_status"]}
            for a in artifacts
        ],
    }
    manifest_file = run_dir / "input_manifest.json"
    write_json(manifest_file, manifest)
    # Keep the old l3_alignment byte-level declarations as historical comparison.
    config["local_input_manifest"] = {
        "path": manifest_file.relative_to(repo).as_posix(), "sha256": file_hash(manifest_file)}
    config["input_binding_mode"] = "EXPLICIT_LOCAL_MANIFEST"
    config["live_authorized"] = False
    write_json(run_dir / "config.bound.json", config)
    return {"status": "PASS", "run_dir": str(run_dir),
            "config": str(run_dir / "config.bound.json"),
            "input_manifest": str(manifest_file), "registered_pairs": len(artifacts),
            "real_model_calls": 0, "semantic_status": "NOT_RUN"}


def check_local_alignment(config, repo):
    spec = config.get("local_input_manifest")
    if config.get("input_binding_mode") != "EXPLICIT_LOCAL_MANIFEST" or not isinstance(spec, dict):
        raise L2Error("Local input binding must be explicitly selected")
    path = confined(repo, spec.get("path", ""))
    if not path.is_file() or file_hash(path) != spec.get("sha256"):
        raise L2Error("Local input manifest missing or changed")
    manifest = read_json(path)
    expected = input_contract(config)
    if manifest.get("contract") != expected or manifest.get("contract_hash") != digest(expected):
        raise L2Error("Local manifest does not match the frozen input contract")
    if _imported_pairs(manifest) != {(b["task_id"], b["method"]): (b["path"], b["sha256"])
                                     for b in config["input_bindings"]}:
        raise L2Error("Local manifest artifact mapping changed")
    targets = {t["task_id"]: t for t in config["targets"]}
    joins = [{"task_id": b["task_id"], "target_id": b["task_id"],
              "project_id": targets[b["task_id"]]["source_project"],
              "cohort_group": targets[b["task_id"]].get("cohort_group", "UNSPECIFIED"),
              "method": b["method"], "input_hash": b["sha256"], "input_path": b["path"],
              "l3_artifact_id": None} for b in config["input_bindings"]]
    return {"status": "PASS", "binding_mode": "EXPLICIT_LOCAL_MANIFEST",
            "checks": {"same_frozen_input_contract": True}, "joins": joins,
            "join_keys": ["project_id", "target_id", "method", "input_hash"],
            "semantic_result_sharing": "NONE", "l3_code_imported": False,
            "historical_batch_provenance": config["batch_provenance_status"]}
