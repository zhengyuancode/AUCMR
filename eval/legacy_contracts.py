"""Explicit identities and claim populations for the retained L1/rule-L2 entrypoint."""
import hashlib
import json
from pathlib import Path
import re

METHODS = ("LVLM-Direct", "TextRAG-Elaborate", "ViDoRAG-Elaborate", "VDocRAG-Elaborate",
           "ReAct-Elaborate", "CREST w/o cross-slot", "AUCMR (CREST full)")
ALIASES = {"AUCMR": METHODS[-1], "CREST w/o cross-slot check": METHODS[-2]}
SLOTS = ("goal", "preconditions", "trigger", "main_flow", "alternative_flow", "exception_flow", "postconditions")


def legacy_method(name):
    value = ALIASES.get(name, name)
    if value not in METHODS:
        raise ValueError("Unknown evaluation method: " + str(name))
    return value


def canonical_method(name):
    value = legacy_method(name)
    return {METHODS[-1]: "AUCMR", METHODS[-2]: "CREST w/o cross-slot check"}.get(value, value)


def method_from_output_path(path, data):
    """Bind a generated file independently of its self-reported method."""
    path = Path(path)
    directory = path.parent.parent.name
    name, actor = data.get("use_case_name"), data.get("actor")
    if any(not isinstance(v, str) or not v.strip() for v in (name, actor)):
        raise ValueError("Missing output task identity: " + str(path))
    raw = actor + "_" + name
    if directory in {"aucmr_full", "crest_without_dra"}:
        method = METHODS[-1] if directory == "aucmr_full" else METHODS[-2]
        stem = re.sub(r"[^\w\-.]+", "", raw.replace(" ", "_")) or "unnamed"
    elif directory == "baselines":
        matches = [m for m in METHODS[:5] if path.stem.endswith("_" + m)]
        if len(matches) != 1:
            raise ValueError("Cannot bind baseline method from filename: " + str(path))
        method = matches[0]
        stem = raw.replace(" ", "_").replace("/", "_") + "_" + method
    else:
        raise ValueError("Unknown evaluation output directory: " + str(path))
    if path.name != stem + ".json":
        raise ValueError("Output filename differs from task identity: " + str(path))
    if data.get("method") is not None and legacy_method(data["method"]) != method:
        raise ValueError("Output method differs from directory/filename binding: " + str(path))
    return method


def target_id(project, name, actor):
    cohort = Path(__file__).parent / "agent_evaluation/design/cohort.json"
    for target in json.loads(cohort.read_text(encoding="utf-8"))["targets"]:
        if (target["project_id"], target["name"], target["actor"]) == (project, name, actor):
            return target["target_id"]
    # Identical serialization and identity order to prepare_evaluation_batch.
    raw = (json.dumps((project, actor, name), ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    return "UC-" + hashlib.sha256(raw).hexdigest()[:16]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def claim_population(data, *, required=False):
    audit = data.get("citation_audit") or {}
    slots = audit.get("pre_constraint_slots") if isinstance(audit, dict) else None
    final = data.get("slots") or {}
    if isinstance(slots, dict) and slots:
        version = audit.get("version")
        if required and version not in {"pre_constraint_draft_v1", "final_semantic_verified_candidate_v2"}:
            raise ValueError("Formal L1 requires a recognized citation audit version")
        for name in SLOTS:
            draft = slots.get(name)
            if draft is None:
                if (final.get(name) or {}).get("claims"):
                    raise ValueError("Nonempty final slot missing from citation audit: " + name)
                continue
            if not isinstance(draft, dict) or not isinstance(draft.get("claims"), list):
                raise ValueError("Invalid citation audit claims: " + name)
            if not isinstance(draft.get("exposed_evidence_refs"), list):
                raise ValueError("Invalid citation audit exposed evidence: " + name)
            if required:
                for claim in draft["claims"]:
                    if not isinstance(claim, dict) or not isinstance(claim.get("content"), str) or not claim["content"].strip():
                        raise ValueError("Invalid citation audit claim content: " + name)
                    refs = claim.get("evidence_refs", [])
                    if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
                        raise ValueError("Invalid citation audit claim references: " + name)
                if any(not isinstance(ref, str) or not ref.strip() for ref in draft["exposed_evidence_refs"]):
                    raise ValueError("Invalid citation audit exposed reference: " + name)
        return {"claim_population": "AUDIT_CANDIDATES", "citation_audit_version": version}
    nonempty = any((final.get(s) or {}).get("claims") for s in SLOTS)
    if required and nonempty:
        raise ValueError("Formal L1 cannot silently fall back to final claims")
    return {"claim_population": "LEGACY_FINAL_FALLBACK" if nonempty else "EMPTY_NO_CANDIDATES",
            "citation_audit_version": None}


def result_identity(key, paths, *, require_l1_audit=False):
    method, project, name, actor = key
    path, evidence = map(Path, paths)
    raw = path.read_bytes()
    data = json.loads(raw.decode("utf-8-sig"))
    if (data.get("use_case_name"), data.get("actor")) != (name, actor):
        raise ValueError("Output task identity differs from registered result")
    if data.get("method") is not None and legacy_method(data["method"]) != legacy_method(method):
        raise ValueError("Output method differs from registered result")
    evidence_raw = evidence.read_bytes()
    evidence_data = json.loads(evidence_raw.decode("utf-8-sig"))
    for field, expected in (("use_case_name", name), ("actor", actor)):
        if field in evidence_data and evidence_data[field] != expected:
            raise ValueError("Evidence task identity differs from registered result")
    if evidence_data.get("method") is not None and legacy_method(evidence_data["method"]) != legacy_method(method):
        raise ValueError("Evidence method differs from registered result")
    if data.get("artifact_revision_id") != evidence_data.get("artifact_revision_id"):
        raise ValueError("Main output and evidence revisions differ")
    return {"project_id": project, "target_id": target_id(project, name, actor),
            "method": canonical_method(method), "input_path": str(path), "evidence_path": str(evidence),
            "input_hash": hashlib.sha256(raw).hexdigest(), "evidence_hash": hashlib.sha256(evidence_raw).hexdigest(),
            **claim_population(data, required=require_l1_audit)}


def cohort_inventory(results, methods, identities):
    rows = []
    for project, name, actor in sorted(identities):
        for method in methods:
            paths = results.get((method, project, name, actor))
            row = {"project_id": project, "target_id": target_id(project, name, actor),
                   "method": canonical_method(method), "use_case": name, "actor": actor,
                   "status": "MISSING_RESULT_OR_EVIDENCE", "input_hash": None, "evidence_hash": None}
            if paths:
                row.update(result_identity((method, project, name, actor), paths), status="PRESENT")
            rows.append(row)
    return {"registered_pairs": rows, "missing_pairs": sum(r["status"] != "PRESENT" for r in rows),
            "real_model_calls": 0}


def table_results(results, expected_methods=None):
    normalized = {}
    for method, values in results.items():
        name = legacy_method(method)
        if name in normalized:
            raise ValueError("Duplicate method aliases in table")
        normalized[name] = values
    expected = [legacy_method(m) for m in expected_methods] if expected_methods is not None else list(normalized)
    if len(expected) != len(set(expected)) or set(normalized) - set(expected):
        raise ValueError("Table methods differ from registered methods")
    return normalized, [m for m in METHODS if m in expected]
