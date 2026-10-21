"""Read-only compatibility with L3's frozen input contract, never its scores or judge."""
from .common import L2Error, confined, digest, file_hash, read_json


def check_l3_alignment(config, repo):
    if "local_input_manifest" in config:
        from .bindings import check_local_alignment
        return check_local_alignment(config, repo)
    binding = config.get("l3_alignment")
    if not isinstance(binding, dict):
        raise L2Error("Missing explicit L3 alignment sources")
    loaded = {}
    checks = {}
    for key in ("config", "cohort", "manifest"):
        spec = binding.get(key, {})
        path = confined(repo, spec.get("path", ""))
        if not path.is_file():
            raise L2Error("Missing declared L3 alignment file: " + key)
        checks[key + "_hash_matches"] = file_hash(path) == spec.get("sha256")
        loaded[key] = read_json(path)
    cohort = loaded["cohort"]
    l3_targets = {(t["target_id"], t["project_id"], t["name"], t["actor"], t["planned_group"])
                  for t in cohort["targets"]}
    l2_targets = {(t["task_id"], t["source_project"], t["name"], t["primary_actor"], t["cohort_group"])
                  for t in config["targets"]}
    checks["same_tasks_and_external_inputs"] = l2_targets == l3_targets
    checks["same_methods"] = set(config["methods"]) == set(cohort["methods"])
    expected = {(b["task_id"], b["method"]): b for b in config["input_bindings"]}
    l3_bound = {(b["target_id"], b["method"]): b for b in loaded["config"]["input_bindings"]}
    checks["same_explicit_file_bindings"] = (
        set(expected) == set(l3_bound) and
        all(expected[k]["path"] == l3_bound[k]["path"] for k in expected))
    l3_artifacts = {(a["target_id"], a["method"]): a for a in loaded["manifest"]["artifacts"]}
    checks["same_frozen_input_hashes"] = (
        set(expected) == set(l3_artifacts) and
        all(expected[k]["sha256"] == l3_artifacts[k]["sha256"] for k in expected))
    checks["manifest_matches_l3_config"] = loaded["manifest"]["config_hash"] == digest(loaded["config"])
    checks["manifest_matches_cohort"] = loaded["manifest"]["cohort_hash"] == binding["cohort"]["sha256"]
    joins = []
    for pair, item in expected.items():
        prior = l3_artifacts.get(pair)
        target = next(t for t in config["targets"] if t["task_id"] == pair[0])
        joins.append({"task_id": pair[0], "target_id": pair[0], "project_id": target["source_project"],
                      "cohort_group": target["cohort_group"], "method": pair[1],
                      "input_hash": item["sha256"], "input_path": item["path"],
                      "l3_artifact_id": prior["artifact_id"] if prior else None})
    return {"status": "PASS" if all(checks.values()) else "DRIFT_REQUIRES_REVIEW",
            "checks": checks, "joins": joins,
            "join_keys": ["project_id", "target_id", "method", "input_hash"],
            "semantic_result_sharing": "NONE", "l3_code_imported": False,
            "historical_batch_provenance": config["batch_provenance_status"]}
