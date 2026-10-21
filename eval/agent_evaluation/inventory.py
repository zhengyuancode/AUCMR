"""Freeze explicit input batches and detect drift, without semantic evaluation."""
from __future__ import annotations

from pathlib import Path
from collections import Counter
import math

from .adapters import adapt
from .common import DESIGN, REPO, PROTOCOL_VERSION, EvaluationError, confined, digest, file_hash, read_json, verify_design, write_json


def collect(config: dict) -> dict:
    import fitz
    cohort = read_json(DESIGN / "cohort.json")
    old = read_json(DESIGN / "input_inventory.json")
    # All files in the registered batch are inspected, including unexpected additions.
    roots = [REPO / "outputs" / name for name in ("aucmr_full", "crest_without_dra", "baselines")]
    discovered = sorted(p for root in roots for p in root.rglob("*.json") if not p.name.endswith("-evidence.json"))
    old_map = {Path(e["path"].replace("\\", "/")).as_posix().split("/outputs/", 1)[1]: e for e in old["artifacts"]}
    bindings = config["input_bindings"]
    targets = {t["target_id"]: t for t in cohort["targets"]}
    seen, artifacts = set(), []
    for binding in bindings:
        key = (binding["target_id"], binding["method"])
        if key in seen:
            raise EvaluationError("Duplicate target/method input binding")
        seen.add(key)
        path = confined(REPO, binding["path"])
        if not any(path.is_relative_to(root.resolve()) for root in roots):
            raise EvaluationError("Input outside registered outputs batch")
        artifacts.append(adapt(path, targets[key[0]], key[1]))
    expected = {(t, m) for t in targets for m in cohort["methods"]}
    if seen != expected:
        raise EvaluationError("Missing or extra target/method bindings")
    bound_paths = {Path(a["path"]) for a in artifacts}
    extra = [{"path": str(p), "sha256": file_hash(p)} for p in discovered if p not in bound_paths]
    sources = []
    for src in cohort["source_documents"]:
        path = REPO / "data" / Path(src["pdf"]).name
        with fitz.open(path) as pdf:
            sources.append({**src, "pdf": str(path), "sha256": file_hash(path), "pages": len(pdf)})
    main = [a for a in artifacts if a["planned_group"] == "MAIN"]
    summary = {"artifact_files": sum(a["sha256"] is not None for a in artifacts), "registered_pairs": len(artifacts),
               "main_artifact_files": sum(a["sha256"] is not None for a in main),
               "main_final_claims": sum(len(a["claims"]) for a in main),
               "main_content_batches_per_judge": sum(math.ceil(len(a["claims"]) / 6) for a in main),
               "main_states": dict(Counter(a["state"] for a in main)),
               "all_states": dict(Counter(a["state"] for a in artifacts)), "unexpected_files": len(extra)}
    drift = []
    for a in artifacts:
        rel = Path(a["path"]).relative_to(REPO / "outputs").as_posix()
        prior = old_map.get(rel)
        if prior is None or prior["sha256"] != a["sha256"]:
            drift.append({"path": rel, "old": prior["sha256"] if prior else None, "new": a["sha256"]})
    for src in sources:
        prior = next(x for x in old["source_documents"] if x["source_id"] == src["source_id"])
        if src["sha256"] != prior["sha256"]:
            drift.append({"source_id": src["source_id"], "old": prior["sha256"], "new": src["sha256"]})
    return {"protocol_version": PROTOCOL_VERSION, "data_type": "REAL_INPUT_STRUCTURE_ONLY",
            "source_semantics": "NOT_RUN", "config_hash": digest(config), "cohort_hash": file_hash(DESIGN / "cohort.json"),
            "sources": sources, "cohort": cohort, "artifacts": artifacts, "summary": summary,
            "input_drift": drift, "unexpected_files": extra,
            "historical_model_provenance": "NOT_VERIFIED", "input_batch_status": "CANDIDATE_REQUIRES_CONFIRMATION"}


def inventory(config_path: Path, run_dir: Path) -> dict:
    config = read_json(config_path)
    def reject_credentials(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key.lower() in {"api_key", "authorization", "password", "secret", "access_token"}:
                    raise EvaluationError("Literal credentials are forbidden in evaluator config; use api_key_env")
                reject_credentials(child)
        elif isinstance(value, list):
            for child in value:
                reject_credentials(child)
    reject_credentials(config)
    if config["protocol_version"] != PROTOCOL_VERSION:
        raise EvaluationError("Wrong protocol")
    design = verify_design()
    manifest = collect(config)
    if (run_dir / "input_manifest.json").exists():
        if digest(read_json(run_dir / "input_manifest.json")) != digest(manifest):
            raise EvaluationError("Run already frozen; changed inputs require a new run directory")
    write_json(run_dir / "config.snapshot.json", config)
    write_json(run_dir / "design_check.json", design)
    write_json(run_dir / "input_manifest.json", manifest)
    if not (run_dir / "source_status.json").exists():
        write_json(run_dir / "source_status.json", {t["target_id"]: {"status": "NOT_REVIEWED", "core_status": "NOT_RUN"}
                                                    for t in manifest["cohort"]["targets"]})
    if not (run_dir / "stage_state.json").exists():
        write_json(run_dir / "stage_state.json", {"state": "INVENTORIED", "semantic_status": "NOT_RUN"})
    return manifest["summary"]


def assert_inputs_current(run_dir: Path) -> dict:
    config = read_json(run_dir / "config.snapshot.json")
    expected = read_json(run_dir / "input_manifest.json")
    current = collect(config)
    if digest(expected) != digest(current):
        raise EvaluationError("Input/config/cohort drift; refusing silent resume")
    return expected
