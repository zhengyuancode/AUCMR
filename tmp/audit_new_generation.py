import json
import re
from collections import Counter
from pathlib import Path

import run


ROOT = Path("outputs")
OLD = ROOT / "history" / "pre_final_grounding_20260816"
SLOTS = ["goal", "preconditions", "trigger", "main_flow", "alternative_flow", "exception_flow", "postconditions"]
CONTROL = re.compile(r"\b(return(?:s|ed|ing)?|resume(?:s|d|ing)?|continue(?:s|d|ing)?|terminate(?:s|d|ing)?|proceed(?:s|ed|ing)?\s+to\s+step)\b", re.I)


def norm(text):
    return " ".join(str(text).casefold().split())


def load_current(directory):
    rows = {}
    for path in directory.rglob("*.json"):
        if path.name.endswith("-evidence.json"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("generation_version") != run.CREST_GENERATION_VERSION:
            continue
        rows[(path.parent.name, str(data.get("use_case_name")), str(data.get("actor")))] = data
    return rows


full = load_current(ROOT / "aucmr_full")
wo = load_current(ROOT / "crest_without_dra")
old_audit = json.loads((OLD / "evaluation_audit_l1.json").read_text(encoding="utf-8"))
old_bad_by_uc = {}
for row in old_audit[run.FULL]:
    old_bad_by_uc[(row["use_case"], row["actor"])] = [
        claim for claim in row["l1"]["claims"] if claim.get("entailment") != "entailment"
    ]

claims_total = 0
audit_total = 0
empty_slots = 0
control_claims = []
flags = Counter()
old_bad_total = 0
old_bad_unchanged = []
audit_versions = Counter()
changed_slots = 0
changed_cases = 0
per_case = []

for identity, data in sorted(full.items()):
    current_claims = []
    case_changed = False
    case_empty = []
    case_claim_count = 0
    other = wo.get(identity, {})
    for slot in SLOTS:
        slot_data = data.get("slots", {}).get(slot, {}) or {}
        claims = slot_data.get("claims", []) or []
        claims_total += len(claims)
        case_claim_count += len(claims)
        if not claims and not str(slot_data.get("raw_text", "")).strip():
            empty_slots += 1
            case_empty.append(slot)
        for claim in claims:
            text = str(claim.get("content", ""))
            current_claims.append((slot, text))
            if slot == "exception_flow" and CONTROL.search(text):
                control_claims.append((identity[1], text))
        if slot_data != (other.get("slots", {}).get(slot, {}) or {}):
            changed_slots += 1
            case_changed = True
    changed_cases += int(case_changed)
    for flag in data.get("quality_flags", []) or []:
        flags[str(flag.get("flag_type", "unknown"))] += 1
    per_case.append({
        "use_case": identity[1],
        "claims": case_claim_count,
        "empty_slots": case_empty,
        "flags": [
            {"type": flag.get("flag_type"), "slot": flag.get("slot"), "detail": flag.get("detail")}
            for flag in data.get("quality_flags", []) or []
        ],
    })
    citation = data.get("citation_audit", {}) or {}
    audit_versions[str(citation.get("version", "missing"))] += 1
    audit_slots = citation.get("pre_constraint_slots", {}) or {}
    audit_total += sum(len((audit_slots.get(slot, {}) or {}).get("claims", []) or []) for slot in SLOTS)

    old_bad = old_bad_by_uc.get((identity[1], identity[2]), [])
    old_bad_total += len(old_bad)
    normalized_new = [(slot, norm(text)) for slot, text in current_claims]
    for bad in old_bad:
        old_prefix = norm(bad.get("claim", ""))
        match = next((text for slot, text in normalized_new if slot == bad.get("slot") and text.startswith(old_prefix)), None)
        if match:
            old_bad_unchanged.append((identity[1], bad.get("slot"), bad.get("claim")))

print(json.dumps({
    "completed_full_cases": len(full),
    "completed_without_dra_cases": len(wo),
    "final_claims": claims_total,
    "l1_audit_candidates": audit_total,
    "empty_slots_total": empty_slots,
    "old_nonentailed_claims_in_completed_cases": old_bad_total,
    "old_nonentailed_claims_still_unchanged": len(old_bad_unchanged),
    "full_vs_without_dra_changed_cases": changed_cases,
    "full_vs_without_dra_changed_slots": changed_slots,
    "quality_flags": dict(flags),
    "citation_audit_versions": dict(audit_versions),
    "exception_control_wording_count": len(control_claims),
    "exception_control_wording_examples": control_claims[:12],
    "unchanged_old_bad_examples": old_bad_unchanged[:12],
    "per_case": per_case,
}, ensure_ascii=False, indent=2))
