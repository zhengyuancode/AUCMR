"""Prepare file-driven evaluation inputs. Never imports generation or calls models."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def relative(repo, value):
    path = (repo / value).resolve()
    if not path.is_relative_to(repo):
        raise ValueError("Path must remain inside repository: " + str(value))
    return path.relative_to(repo).as_posix()


def output_path(root, case, method):
    raw = case["actor"] + "_" + case["use_case"]
    if method in {"AUCMR", "CREST w/o cross-slot check"}:
        stem = re.sub(r"[^\w\-.]+", "", raw.replace(" ", "_")) or "unnamed"
        directory = "aucmr_full" if method == "AUCMR" else "crest_without_dra"
        return f"{root}/{directory}/{case['project']}/{stem}.json"
    stem = raw.replace(" ", "_").replace("/", "_")
    return f"{root}/baselines/{case['project']}/{stem}_{method}.json"


def prepare(repo, generation_config, destination, *, draft=False, models_file=None, allow_missing=False):
    repo = Path(repo).resolve()
    destination = repo / relative(repo, destination)
    if destination.exists():
        raise ValueError("Use a new destination; frozen batches are never overwritten")
    generation_config = repo / relative(repo, generation_config)
    generation = read(generation_config)
    root = relative(repo, generation["paths"]["output"])
    if not root.startswith("outputs/") or root == "outputs":
        raise ValueError("Generation output must be an explicit directory beneath outputs")
    templates = {layer: read(repo / f"config/agent_evaluation_{layer}.json")
                 for layer in ("l2_independent", "l3", "l4")}
    methods = templates["l4"]["methods"]
    cases = generation["experiment"]["use_cases"]
    if not cases:
        raise ValueError("No registered use cases")
    identities = [(c["project"], c["actor"], c["use_case"]) for c in cases]
    if len(set(identities)) != len(identities):
        raise ValueError("Duplicate project/actor/use-case identity")
    if any(not isinstance(v, str) or not v.strip() for row in identities for v in row):
        raise ValueError("Empty or invalid task identity")
    if any(not re.fullmatch(r"[\w.-]+", c["project"]) or c["project"] in {".", ".."} for c in cases):
        raise ValueError("Invalid project directory name")
    # Preserve established IDs when identity matches, never preserve historical inclusion decisions.
    old = read(repo / "eval/agent_evaluation/design/cohort.json")
    known = {(t["project_id"], t["actor"], t["name"]): t["target_id"] for t in old["targets"]}
    targets = []
    for case, identity in zip(cases, identities):
        stable = "UC-" + hashlib.sha256(encoded(identity)).hexdigest()[:16]
        targets.append({"target_id": known.get(identity, stable), "project_id": case["project"],
                        "actor": case["actor"], "name": case["use_case"], "planned_group": "MAIN",
                        "source_review_status": "NOT_REVIEWED"})
    if len({t["target_id"] for t in targets}) != len(targets):
        raise ValueError("Target ID collision")
    projects = {c["project"] for c in cases}
    documents = [d for d in generation["text_pipeline"]["documents"] if d["project"] in projects]
    if projects != {d["project"] for d in documents}:
        raise ValueError("Missing source document mapping")
    # Respect per-case file_names; do not accidentally authorize unrelated documents in a collection.
    for case in cases:
        supplied = {d["file_name"] for d in documents if d["project"] == case["project"]}
        if set(case.get("file_names") or supplied) != supplied:
            raise ValueError("Per-case document subsets need an explicit separate project mapping")
    import fitz
    sources = []
    for i, document in enumerate(documents):
        path = relative(repo, document["pdf"])
        with fitz.open(repo / path) as pdf:
            pages = len(pdf)
        sources.append({"source_id": "SRC-" + hashlib.sha256(path.encode()).hexdigest()[:12],
                        "project_id": document["project"], "pdf": path, "pages": pages,
                        "sha256": sha(repo / path)})
    bindings, missing, evidence_missing = [], [], []
    for target, case in zip(targets, cases):
        for method in methods:
            path = relative(repo, output_path(root, case, method))
            exists = (repo / path).is_file()
            if not exists:
                missing.append(path)
            else:
                data = read(repo / path)
                if data.get("actor") != case["actor"] or data.get("use_case_name") != case["use_case"]:
                    raise ValueError("Result identity mismatch: " + path)
                if method != "LVLM-Direct" and not (repo / path.replace(".json", "-evidence.json")).is_file():
                    evidence_missing.append(path)
            bindings.append({"target_id": target["target_id"], "method": method, "path": path,
                             "sha256": sha(repo / path) if exists else None})
    if len({b["path"] for b in bindings}) != len(bindings):
        raise ValueError("Output filename collision")
    if (missing or evidence_missing) and not (draft or allow_missing):
        raise ValueError(f"Incomplete batch: {len(missing)} results and {len(evidence_missing)} evidence pairs missing; use --draft only for preparation")
    batch = {"status": "DRAFT_NOT_FROZEN" if draft else "FROZEN_INPUTS", "real_model_calls": 0,
             "generation_config": relative(repo, generation_config), "generation_config_sha256": sha(generation_config),
             "output_roots": [root + "/" + d for d in ("aucmr_full", "crest_without_dra", "baselines")],
             "cohort": {"protocol_version": templates["l3"]["protocol_version"], "targets": targets,
                        "methods": methods, "source_documents": sources}, "input_bindings": bindings}
    batch_hash = hashlib.sha256(encoded(batch)).hexdigest()
    batch_id = "BATCH-" + batch_hash[:16]
    folder = relative(repo, destination)
    l2, l3, l4 = (deepcopy(templates[k]) for k in ("l2_independent", "l3", "l4"))
    l2.pop("l3_alignment", None)
    l2.pop("local_input_manifest", None)
    l2.update(input_batch_id=batch_id, batch_provenance_status="EXPLICIT_GENERATION_CONFIG_INPUTS",
              live_authorized=False, input_binding_mode="REQUIRES_PREPARE_INPUTS",
              targets=[{"task_id": t["target_id"], "source_project": t["project_id"], "name": t["name"],
                        "primary_actor": t["actor"], "cohort_group": t["planned_group"]} for t in targets],
              methods=methods, input_bindings=[{"task_id": b["target_id"], **{k: b[k] for k in ("method", "path", "sha256")}} for b in bindings])
    pilot = [t["target_id"] for t in targets if t["target_id"] in l3.get("pilot_targets", [])]
    if not pilot:
        pilot = [targets[0]["target_id"]]
    l3.update(batch_file=folder + "/batch.json", batch_sha256=batch_hash, input_bindings=bindings,
              pilot_targets=pilot, input_batch_status="CANDIDATE_REQUIRES_CONFIRMATION" if draft else "CONFIRMED",
              candidate_output_root=root, campaign_ledger="outputs/evaluation_campaign/budget.sqlite")
    l3["budgets"]["live_authorized"] = False
    l4.update(batch_id=batch_id, data_class="REAL_INPUT_STRUCTURE_ONLY", live_authorized=False,
              targets=[{"task_id": t["target_id"], "project_id": t["project_id"], "name": t["name"],
                        "actor": t["actor"], "comparison_group": t["planned_group"]} for t in targets],
              sources=[{"source_id": s["source_id"], "project_id": s["project_id"], "path": s["pdf"],
                        "expected_sha256": s["sha256"]} for s in sources],
              input_bindings=[{"task_id": b["target_id"], "method": b["method"], "path": b["path"],
                               "sha256": b["sha256"]} for b in bindings])
    if models_file:
        profile = read(repo / relative(repo, models_file))
        from eval.l2_independent.common import reject_secrets
        reject_secrets(profile)
        l3["models"] = deepcopy(profile["l3"])
        l2["model"] = deepcopy(profile["l2"])
        l4["model"] = deepcopy(profile["l4"])
    from eval.l2_independent.inputs import validate_config as validate_l2
    from eval.l4_agent.inputs import validate_config as validate_l4
    validate_l2(l2)
    validate_l4(l4)
    summary = {"status": batch["status"], "targets": len(targets), "methods": len(methods),
               "registered_pairs": len(bindings), "sources": len(sources),
               "by_project": dict(Counter(t["project_id"] for t in targets)),
               "missing_results": missing, "missing_evidence": evidence_missing,
               "missing_explicitly_registered": allow_missing,
               "real_model_calls": 0, "semantic_evaluation": "NOT_RUN",
               "l0": "SEPARATE_BENCHMARK_ENTRY_NOT_LOCATED",
               "l1_and_rule_l2": "Use run.py evaluate with the original generation config; no evaluator changes",
               "next": "After generation, run without --draft into a NEW destination; then prepare-inputs for L2 and inventory for L3/L4"}
    files = {"batch.json": batch, "l2.json": l2, "l3.json": l3, "l4.json": l4, "preparation.json": summary}
    destination.mkdir(parents=True, exist_ok=False)
    for name, value in files.items():
        with (destination / name).open("xb") as stream:
            stream.write(encoded(value))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generation-config", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--models-file", type=Path)
    parser.add_argument("--draft", action="store_true", help="Prepare planned mappings while generation is running; not usable for L3 scoring")
    parser.add_argument("--allow-missing-results", action="store_true", help="Explicitly freeze absent/failed outputs as missing; never omit their task/method pairs")
    args = parser.parse_args()
    try:
        result = prepare(REPO, args.generation_config, args.destination, draft=args.draft,
                         models_file=args.models_file, allow_missing=args.allow_missing_results)
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(2, str(exc) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k not in {"missing_results", "missing_evidence"}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
