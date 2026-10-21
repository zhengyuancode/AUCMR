"""Read FINAL_ONLY without rewriting, merging or inferring semantic truth."""
from __future__ import annotations

from pathlib import Path

from .common import SLOTS, VALID, EvaluationError, digest, file_hash, read_json


def adapt(path: Path, target: dict, method: str) -> dict:
    result = {"target_id": target["target_id"], "project_id": target["project_id"],
              "planned_group": target["planned_group"], "method": method, "path": str(path),
              "sha256": None, "claims": [], "empty_slots": [], "omitted_slots": [],
              "issues": [], "identity_exposures": [], "generation_api_provenance": "NOT_VERIFIED"}
    result["artifact_id"] = "A-" + digest([target["target_id"], method])[:24]
    if not path.is_file():
        result["state"] = "MISSING_FILE"
        return result
    result["sha256"] = file_hash(path)
    try:
        data = read_json(path)
    except (ValueError, UnicodeError):
        result["state"] = "PARSE_FAILED"
        return result
    if not isinstance(data, dict):
        result.update(state="STRUCTURE_ERROR", issues=["ROOT_NOT_OBJECT"])
        return result
    if data.get("use_case_name") != target["name"] or data.get("actor") != target["actor"]:
        result["issues"].append("TARGET_IDENTITY_MISMATCH")
    result["generation_version"] = data.get("generation_version")
    result["artifact_revision_id"] = data.get("artifact_revision_id")
    result["pre_revision_hash"] = data.get("pre_revision_hash")
    slots = data.get("slots")
    if not isinstance(slots, dict):
        result.update(state="STRUCTURE_ERROR", issues=result["issues"] + ["SLOTS_NOT_OBJECT"])
        return result
    flags = data.get("quality_flags", [])
    refusal = isinstance(flags, list) and any(isinstance(f, dict) and
        f.get("flag_type") == "unresolvable_use_case" for f in flags)
    early_eligible = method in {"AUCMR", "CREST w/o cross-slot check"} and refusal
    existing_empty = True
    early_errors = []
    for slot in SLOTS:
        if slot not in slots:
            result["omitted_slots"].append(slot)
            early_errors.append(f"{slot}:MISSING_SLOT")
            continue
        item = slots[slot]
        if not isinstance(item, dict):
            result["issues"].append(f"{slot}:INVALID_SLOT")
            existing_empty = False
            continue
        raw = item.get("raw_text", "")
        if raw is not None and not isinstance(raw, str):
            result["issues"].append(f"{slot}:INVALID_RAW_TEXT")
            existing_empty = False
        if raw:
            existing_empty = False
        if "claims" not in item:
            early_errors.append(f"{slot}:RAW_WITHOUT_CLAIMS" if raw else f"{slot}:MISSING_CLAIMS")
            continue
        claims = item["claims"]
        if not isinstance(claims, list):
            result["issues"].append(f"{slot}:CLAIMS_NOT_ARRAY")
            existing_empty = False
            continue
        if not claims:
            result["empty_slots"].append(slot)
            if raw:
                result["issues"].append(f"{slot}:NONEMPTY_RAW_EMPTY_CLAIMS")
        for index, claim in enumerate(claims):
            text = claim.get("content") if isinstance(claim, dict) else None
            if not isinstance(text, str) or not text.strip():
                result["issues"].append(f"{slot}:{index}:INVALID_CLAIM")
                existing_empty = False
                continue
            existing_empty = False
            cid = "C-" + digest([result["sha256"], slot, index])
            result["claims"].append({"claim_id": cid, "slot": slot, "index": index, "text": text})
            if any(name in text for name in ("AUCMR", "CREST", "VDocRAG", "ViDoRAG", "EvidenceCard")):
                result["identity_exposures"].append(cid)
    if early_eligible and existing_empty and not result["issues"]:
        result["state"] = "ABSTAINED"
        result["early_stop_omissions"] = early_errors
    else:
        result["issues"].extend(early_errors)
        result["state"] = "STRUCTURE_ERROR" if result["issues"] else ("PRESENT" if result["claims"] else "VALID_EMPTY")
    return result


def anonymous(artifact: dict, target: dict) -> dict:
    """Allowlist, not a blacklist. IDs are opaque, metadata never reaches judges."""
    if artifact["state"] not in VALID:
        raise EvaluationError("Damaged artifact cannot be judged")
    return {"artifact_id": artifact["artifact_id"], "target": {"name": target["name"], "actor": target["actor"]},
            "claims": [{k: c[k] for k in ("claim_id", "slot", "index", "text")} for c in artifact["claims"]]}
