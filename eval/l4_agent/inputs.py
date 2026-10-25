"""Explicit frozen bindings and structural-only final artifact adaptation."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from eval.serialized_slots import omitted_claims_are_empty

from .protocol import (L4Error, REPO, SLOTS, PROTOCOL_VERSION, digest, file_hash,
                       read_json, save_new, versions)

CONTENT_FIELDS = ("content", "step_number")


def local_path(value: str) -> Path:
    path = (REPO / value).resolve()
    if not path.is_relative_to(REPO):
        raise L4Error("Input path outside the registered repository")
    return path


def validate_config(config: dict) -> None:
    def no_secrets(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key.lower() in {"api_key", "password", "secret", "authorization", "access_token"}:
                    raise L4Error("Literal credentials forbidden; use an environment variable name")
                no_secrets(value)
        elif isinstance(obj, list):
            for value in obj:
                no_secrets(value)
    no_secrets(config)
    if config.get("data_class") not in {"MOCK_ONLY", "REAL_INPUT_STRUCTURE_ONLY", "REAL_AGENT_EVALUATION"}:
        raise L4Error("Invalid data_class")
    if config.get("protocol_version") != PROTOCOL_VERSION:
        raise L4Error("Wrong L4 protocol version")
    if config.get("generated_slots") != list(SLOTS):
        raise L4Error("Exactly seven generated slots required")
    if config.get("live_authorized") not in (True, False) or not isinstance(config.get("live_authorized"), bool):
        raise L4Error("Explicit boolean live_authorized required")
    if not config.get("batch_id") or not config.get("targets") or not config.get("methods"):
        raise L4Error("Explicit nonempty batch/targets/methods required")
    targets = {t["task_id"]: t for t in config["targets"]}
    methods = config["methods"]
    if not all(isinstance(m, str) and m.strip() for m in methods):
        raise L4Error("Method IDs must be nonempty strings")
    if len(targets) != len(config["targets"]) or len(methods) != len(set(methods)):
        raise L4Error("Duplicate targets or methods")
    for t in targets.values():
        if not all(isinstance(t.get(k), str) and t[k].strip() for k in ("task_id", "project_id", "comparison_group", "name", "actor")):
            raise L4Error("Invalid task input")
    pairs = [(b["task_id"], b["method"]) for b in config["input_bindings"]]
    if len(pairs) != len(set(pairs)) or set(pairs) != {(t, m) for t in targets for m in methods}:
        raise L4Error("Explicit complete target/method matrix required, including missing outputs")
    source_ids = [s["source_id"] for s in config["sources"]]
    if len(source_ids) != len(set(source_ids)):
        raise L4Error("Duplicate source IDs")
    if not all(any(s["project_id"] == t["project_id"] for s in config["sources"]) for t in targets.values()):
        raise L4Error("Every project requires explicit original sources")
    policy = config["source_policy"]
    if set(policy) != {"search_max", "pages_max", "request_rounds", "tools_per_round"}:
        raise L4Error("One global source policy required; no per-method overrides")
    if any(type(v) is not int or not 1 <= v <= 1000 for v in policy.values()):
        raise L4Error("Invalid source limits")
    if set(config["judge"]) != {"judge_id", "version"} or not all(config["judge"].values()):
        raise L4Error("judge_id and judge version required")
    if not config["model"].get("config_id"):
        raise L4Error("Model configuration ID required even when NOT_BOUND")


def adapt(binding: dict, task: dict, methods: list[str]) -> dict:
    path = local_path(binding["path"])
    artifact = {"artifact_id": "L4-" + digest([task["task_id"], binding["method"]])[:24],
                "task_id": task["task_id"], "project_id": task["project_id"], "comparison_group": task["comparison_group"],
                "method": binding["method"], "path": binding["path"], "input_sha256": None,
                "state": "MISSING_FILE", "issues": [], "slots": {}, "empty_slots": []}
    if not path.is_file():
        return artifact
    artifact["input_sha256"] = file_hash(path)
    try:
        data = read_json(path)
    except (ValueError, UnicodeError, OSError):
        artifact["state"] = "STRUCTURE_ERROR"
        artifact["issues"] = ["INVALID_JSON"]
        return artifact
    if not isinstance(data, dict) or not isinstance(data.get("slots"), dict):
        artifact.update(state="STRUCTURE_ERROR", issues=["SLOTS_NOT_OBJECT"])
        return artifact
    if data.get("use_case_name") != task["name"] or data.get("actor") != task["actor"]:
        artifact["issues"].append("TASK_MISMATCH")
    # Only explicit machine-readable refusal flags; never infer real semantics.
    flags = data.get("quality_flags", [])
    refusal = data.get("refusal") is True or data.get("status") == "REFUSED" or (
        isinstance(flags, list) and any(isinstance(f, dict) and f.get("flag_type") == "unresolvable_use_case" for f in flags))
    omitted = []
    for slot in SLOTS:
        if slot not in data["slots"]:
            omitted.append(slot)
            continue
        item = data["slots"].get(slot)
        if not isinstance(item, dict):
            artifact["issues"].append(slot + ":INVALID_OR_MISSING_SLOT")
            continue
        if "slot" in item and item["slot"] != slot:
            artifact["issues"].append(slot + ":SLOT_NAME_MISMATCH")
        if omitted_claims_are_empty(item, slot, refusal=refusal):
            artifact["slots"][slot] = {"claims": []}
            artifact["empty_slots"].append(slot)
            continue
        if not isinstance(item.get("claims"), list):
            artifact["issues"].append(slot + ":INVALID_OR_MISSING_SLOT")
            continue
        clean = {"claims": []}
        if "raw_text" in item:
            if item["raw_text"] is not None and not isinstance(item["raw_text"], str):
                artifact["issues"].append(slot + ":INVALID_RAW_TEXT")
            clean["raw_text"] = deepcopy(item["raw_text"])
        for index, claim in enumerate(item["claims"]):
            if not isinstance(claim, dict) or not isinstance(claim.get("content"), str) or not claim["content"].strip():
                artifact["issues"].append(slot + ":INVALID_CLAIM")
                continue
            if "step_number" in claim and type(claim["step_number"]) not in {int, str}:
                artifact["issues"].append(slot + ":INVALID_STEP_NUMBER")
            clean["claims"].append({"claim_id": slot + ":" + str(index), "index": index,
                                    **{k: deepcopy(claim[k]) for k in CONTENT_FIELDS if k in claim}})
        if not item["claims"]:
            if isinstance(item.get("raw_text"), str) and item["raw_text"].strip():
                artifact["issues"].append(slot + ":RAW_WITHOUT_ASSERTIONS")
            else:
                artifact["empty_slots"].append(slot)
        artifact["slots"][slot] = clean
    # The producer may stop after an empty goal with an explicit refusal flag.
    # Retain physical omissions separately; the scoring view contains no content.
    artifact["omitted_slots"] = omitted
    early_refusal = (refusal and "goal" in data["slots"] and not artifact["issues"]
                     and len(artifact["empty_slots"]) + len(omitted) == len(SLOTS))
    if early_refusal:
        for slot in omitted:
            artifact["slots"][slot] = {"claims": []}
        artifact["empty_slots"] = list(SLOTS)
    else:
        artifact["issues"].extend(slot + ":INVALID_OR_MISSING_SLOT" for slot in omitted)
    # Preserve text rather than redact it if identity is embedded in content.
    texts = [task["name"], task["actor"]]
    texts += [c["content"] for s in artifact["slots"].values() for c in s["claims"]]
    texts += [s["raw_text"] for s in artifact["slots"].values() if isinstance(s.get("raw_text"), str)]
    texts += [c["step_number"] for s in artifact["slots"].values() for c in s["claims"] if isinstance(c.get("step_number"), str)]
    exposed = any(m.casefold() in t.casefold() for m in methods for t in texts)
    artifact["explicit_refusal"] = refusal
    if artifact["issues"]:
        artifact["state"] = "STRUCTURE_ERROR"
    elif exposed:
        artifact["state"] = "IDENTITY_EXPOSURE"
    elif refusal and len(artifact["empty_slots"]) == 7:
        artifact["state"] = "REFUSED"
    else:
        artifact["state"] = "EMPTY_OUTPUT" if len(artifact["empty_slots"]) == 7 else "PRESENT"
    return artifact


def collect(config: dict) -> dict:
    validate_config(config)
    import fitz
    sources = []
    for source in config["sources"]:
        p = local_path(source["path"])
        entry = {**source, "sha256": None, "pages": None, "state": "SOURCE_UNREADABLE"}
        try:
            entry["sha256"] = file_hash(p)
            with fitz.open(p) as pdf:
                entry["pages"] = len(pdf)
                if len(pdf) and not pdf.needs_pass:
                    entry["state"] = "READY"
            if source.get("expected_sha256") and source["expected_sha256"] != entry["sha256"]:
                entry["state"] = "SOURCE_CHANGED"
        except (OSError, RuntimeError, ValueError):
            pass
        sources.append(entry)
    tasks = {t["task_id"]: t for t in config["targets"]}
    artifacts = [adapt(b, tasks[b["task_id"]], config["methods"]) for b in config["input_bindings"]]
    for binding, artifact in zip(config["input_bindings"], artifacts):
        if "sha256" in binding and artifact["input_sha256"] != binding["sha256"]:
            raise L4Error("Frozen batch output changed or disappeared; prepare a new batch")
    manifest = {"batch_id": config["batch_id"], "data_class": config["data_class"],
                "research_status": "NOT_RESEARCH_DATA" if config["data_class"] == "MOCK_ONLY" else "STRUCTURE_ONLY_NOT_SCORED",
                "config_sha256": digest(config), "versions": versions(),
                "source_policy": config["source_policy"], "sources": sources,
                "targets": config["targets"], "methods": config["methods"], "artifacts": artifacts,
                "real_semantic_evaluation": "NOT_RUN"}
    manifest["manifest_sha256"] = digest(manifest)
    return manifest


def freeze(config: dict, run_dir: Path) -> dict:
    manifest = collect(config)
    save_new(run_dir / "config.snapshot.json", config)
    save_new(run_dir / "input_manifest.json", manifest)
    return manifest


def assert_current(run_dir: Path) -> tuple[dict, dict]:
    config = read_json(run_dir / "config.snapshot.json")
    manifest = read_json(run_dir / "input_manifest.json")
    if collect(config) != manifest:
        raise L4Error("Inputs, sources, code, prompt, rubric, or model configuration changed; create a new run")
    return config, manifest


def anonymous(artifact: dict, task: dict, sources: list[dict]) -> dict:
    if artifact["state"] != "PRESENT":
        raise L4Error("Only valid, nonempty, identity-safe final artifacts may be sent to a judge")
    return {"artifact_id": artifact["artifact_id"], "data_is_untrusted": True,
            "task": {"name": task["name"], "actor": task["actor"]},
            "slots": deepcopy(artifact["slots"]),
            "sources": [{"source_id": s["source_id"], "pages": s["pages"]} for s in sources]}
