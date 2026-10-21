"""Explicit input bindings and lossless final-claim adaptation; no semantic judging."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .common import (L2Error, SLOTS, PROTOCOL_VERSION, PROMPT_VERSION,
                     canonical, confined, digest, read_json, reject_secrets)


def validate_config(config, *, allow_fixture=False):
    reject_secrets(config)
    if config.get("protocol_version") != PROTOCOL_VERSION:
        raise L2Error("Protocol version mismatch")
    if config.get("prompt_version") != PROMPT_VERSION:
        raise L2Error("Prompt version mismatch")
    if config.get("data_origin") not in ({"REAL", "FIXTURE"} if allow_fixture else {"REAL"}):
        raise L2Error("Inventory config must describe real structural inputs")
    if not isinstance(config.get("live_authorized"), bool):
        raise L2Error("live_authorized must be an explicit boolean")
    if not isinstance(config.get("model"), dict):
        raise L2Error("Missing isolated model configuration")
    for key in ("input_batch_id", "batch_provenance_status"):
        if not isinstance(config.get(key), str) or not config[key]:
            raise L2Error("Missing input batch declaration")
    targets = config.get("targets")
    methods = config.get("methods")
    bindings = config.get("input_bindings")
    if not isinstance(targets, list) or not targets or not isinstance(methods, list) or not methods:
        raise L2Error("Nonempty frozen targets and methods are required")
    if any(not isinstance(m, str) or not m.strip() for m in methods) or len(set(methods)) != len(methods):
        raise L2Error("Invalid or duplicate method")
    ids = []
    for target in targets:
        if not isinstance(target, dict) or any(
            not isinstance(target.get(k), str) or not target[k].strip()
            for k in ("task_id", "source_project", "name", "primary_actor")
        ):
            raise L2Error("Invalid external task input")
        ids.append(target["task_id"])
    if len(set(ids)) != len(ids):
        raise L2Error("Duplicate task")
    if not isinstance(bindings, list):
        raise L2Error("Missing explicit input bindings")
    pairs, paths = [], []
    for b in bindings:
        if not isinstance(b, dict):
            raise L2Error("Invalid binding")
        pairs.append((b.get("task_id"), b.get("method")))
        path = b.get("path")
        if not isinstance(path, str) or not path.startswith("outputs/"):
            raise L2Error("Bind an exact repository-relative output JSON file")
        paths.append(path)
        expected = b.get("sha256")
        if expected is not None and (
            not isinstance(expected, str) or len(expected) != 64 or
            any(c not in "0123456789abcdef" for c in expected)
        ):
            raise L2Error("Invalid frozen SHA256")
        if "sha256" not in b:
            raise L2Error("Each input needs a frozen SHA256, or null for missing")
    expected_pairs = {(t, m) for t in ids for m in methods}
    if len(set(pairs)) != len(pairs) or set(pairs) != expected_pairs:
        raise L2Error("Bindings must cover exactly the same frozen targets for every method")
    if len(set(paths)) != len(paths):
        raise L2Error("One file cannot represent multiple method/task pairs")
    return config


def load_config(path, *, allow_fixture=False):
    return validate_config(read_json(path), allow_fixture=allow_fixture)


def adapt_bytes(raw, target, method, batch_id, *, data_origin="REAL", path=None):
    """One original claim -> one assertion. Never use pre_constraint_slots/raw_text as fallback."""
    h = hashlib.sha256(raw).hexdigest()
    a = {
        "artifact_id": "A-" + digest([batch_id, target["task_id"], method, h])[:24],
        "task_id": target["task_id"], "source_project": target["source_project"],
        "task_input": {"name": target["name"], "primary_actor": target["primary_actor"]},
        "cohort_group": target.get("cohort_group", "UNSPECIFIED"),
        "method": method, "input_hash": h, "input_path": path, "input_batch_id": batch_id,
        "data_origin": data_origin, "input_status": "PRESENT",
        "assertions": [], "empty_slots": [], "omitted_slots": [], "structural_issues": [],
        "explicit_refusal": False, "identity_exposures": [],
    }
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeError):
        a["input_status"] = "PARSE_FAILED"
        return a
    if not isinstance(data, dict):
        a.update(input_status="STRUCTURE_ERROR", structural_issues=["ROOT_NOT_OBJECT"])
        return a
    issues = a["structural_issues"]
    if data.get("use_case_name") != target["name"] or data.get("actor") != target["primary_actor"]:
        issues.append("TASK_IDENTITY_MISMATCH")
    slots = data.get("slots")
    if not isinstance(slots, dict):
        a.update(input_status="STRUCTURE_ERROR")
        issues.append("FINAL_SLOTS_NOT_OBJECT")
        return a
    # Only an explicit producer marker is recognized. Do not infer refusal from claim semantics.
    flags = data.get("quality_flags", [])
    a["explicit_refusal"] = isinstance(flags, list) and any(
        isinstance(f, dict) and f.get("flag_type") == "unresolvable_use_case" for f in flags)
    omitted = []
    missing_claims = []
    any_raw = False
    for slot in SLOTS:
        if slot not in slots:
            omitted.append(slot)
            continue
        item = slots[slot]
        if not isinstance(item, dict):
            issues.append(slot + ":SLOT_NOT_OBJECT")
            continue
        raw_text = item.get("raw_text")
        if raw_text is not None and not isinstance(raw_text, str):
            issues.append(slot + ":RAW_TEXT_NOT_STRING")
        if isinstance(raw_text, str) and raw_text.strip():
            any_raw = True
        if "claims" not in item:
            missing_claims.append(slot)
            continue
        claims = item["claims"]
        if not isinstance(claims, list):
            issues.append(slot + ":CLAIMS_NOT_ARRAY")
            continue
        if not claims:
            a["empty_slots"].append(slot)
            if isinstance(raw_text, str) and raw_text.strip():
                issues.append(slot + ":NONEMPTY_RAW_WITHOUT_CLAIMS")
        for index, claim in enumerate(claims):
            if not isinstance(claim, dict) or not isinstance(claim.get("content"), str) or not claim["content"].strip():
                issues.append(f"{slot}:{index}:INVALID_CLAIM")
                continue
            step = claim.get("step_number")
            if step is not None and (isinstance(step, bool) or not isinstance(step, (int, str))):
                issues.append(f"{slot}:{index}:INVALID_STEP_NUMBER")
                continue
            assertion = {"assertion_id": f"{slot}:{index}", "slot": slot,
                         "original_index": index, "text": claim["content"]}
            if "step_number" in claim:
                assertion["step_number"] = step
            a["assertions"].append(assertion)
    a["omitted_slots"] = omitted
    # Early refusal serialization can contain only an empty goal and omit all other slots.
    clean_early_refusal = a["explicit_refusal"] and not a["assertions"] and not any_raw and not issues
    if not clean_early_refusal:
        issues.extend(s + ":MISSING_SLOT" for s in omitted)
        issues.extend(s + ":MISSING_CLAIMS" for s in missing_claims)
    if issues:
        a["input_status"] = "STRUCTURE_ERROR"
    elif clean_early_refusal:
        a["input_status"] = "EXPLICIT_REFUSAL"
    elif not a["assertions"]:
        a["input_status"] = "VALID_EMPTY"
    # A nonempty result with a refusal flag is not dropped. The flag never enters the request.
    return a


def collect(config, repo, *, allow_fixture=False):
    validate_config(config, allow_fixture=allow_fixture)
    targets = {t["task_id"]: t for t in config["targets"]}
    artifacts = []
    for binding in config["input_bindings"]:
        path = confined(repo, binding["path"])
        target = targets[binding["task_id"]]
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            raw = None
            state = "MISSING_FILE"
        except OSError:
            raw = None
            state = "READ_FAILED"
        if raw is not None:
            a = adapt_bytes(raw, target, binding["method"], config["input_batch_id"],
                            data_origin=config["data_origin"], path=binding["path"])
        else:
            a = {
                "artifact_id": "A-" + digest([config["input_batch_id"], target["task_id"], binding["method"], None])[:24],
                "task_id": target["task_id"], "source_project": target["source_project"],
                "task_input": {"name": target["name"], "primary_actor": target["primary_actor"]},
                "cohort_group": target.get("cohort_group", "UNSPECIFIED"),
                "method": binding["method"], "input_hash": None, "input_path": binding["path"],
                "input_batch_id": config["input_batch_id"], "data_origin": config["data_origin"],
                "input_status": state, "assertions": [], "empty_slots": [], "omitted_slots": [],
                "structural_issues": [], "explicit_refusal": False, "identity_exposures": [],
            }
        a["expected_input_hash"] = binding["sha256"]
        a["hash_matches_binding"] = a["input_hash"] == binding["sha256"]
        if not a["hash_matches_binding"]:
            a["structural_issues"].append("INPUT_HASH_CHANGED_REQUIRES_NEW_BINDING")
        # Do not redact generated prose, which would change assertions. Block accidental identity leaks.
        for c in a["assertions"]:
            if any(m.casefold() in c["text"].casefold() for m in config["methods"]):
                a["identity_exposures"].append(c["assertion_id"])
        artifacts.append(a)
    return artifacts


def anonymous(a):
    if a["input_status"] != "PRESENT" or not a["assertions"]:
        raise L2Error("Only nonempty, structurally valid final slots can be judged")
    if a.get("hash_matches_binding") is False:
        raise L2Error("Input hash drift")
    if a.get("identity_exposures"):
        raise L2Error("Method identity in generated content requires explicit review; no rewriting")
    return {
        "artifact_id": a["artifact_id"],
        "task_input": dict(a["task_input"]),
        "slots": {s: [{k: c[k] for k in ("assertion_id", "original_index", "text", "step_number") if k in c}
                      for c in a["assertions"] if c["slot"] == s] for s in SLOTS},
    }


def structure_row(a):
    """Real inventory never writes semantic labels or a model request."""
    return {k: v for k, v in a.items() if k not in {"assertions", "task_input"}} | {
        "assertion_count": len(a["assertions"]), "empty_slot_count": len(a["empty_slots"]),
        "assertion_count_is_complete": a["input_status"] in {"PRESENT", "VALID_EMPTY", "EXPLICIT_REFUSAL"},
        "semantic_status": "NOT_RUN",
    }
