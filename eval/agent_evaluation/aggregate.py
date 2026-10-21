"""Deterministic, document-wise descriptive statistics. No semantic heuristics."""
from __future__ import annotations

from collections import Counter

from .common import VALID, BEHAVIOR_LABELS, CONTENT_LABELS, EvaluationError


def content_counts(artifact: dict, decisions: dict[str, dict]) -> dict:
    ids = [c["claim_id"] for c in artifact["claims"]]
    if not set(decisions) <= set(ids):
        raise EvaluationError("Decisions outside FINAL_ONLY artifact")
    labels = []
    for cid in ids:
        decision = decisions.get(cid)
        label = decision.get("label") if decision else None
        if label is not None and label not in CONTENT_LABELS:
            raise EvaluationError("Invalid semantic label")
        labels.append(label)
    n = len(ids)
    counts = Counter(labels)
    k, d, u, f = (counts[x] for x in ("KEEP", "DELETE", "UNRESOLVED", None))
    valid = artifact["state"] in VALID
    ok = valid and n > 0 and f == 0
    return {"n_final": n, "k_keep": k, "d_delete": d, "u_unresolved": u, "f_failed": f,
            "cp_confirmed": k/n if ok else None, "cp_lower": k/n if ok else None,
            "cp_upper": (k+u)/n if ok else None, "confirmed_delete_fraction": d/n if ok else None,
            "bounds_kind": "UNRESOLVED_BOUNDS_NOT_CONFIDENCE_INTERVAL",
            "state": "TECHNICAL_INCOMPLETE" if not valid or f else ("EMPTY_OUTPUT" if not n else "COMPLETE"),
            "artifact_state": artifact["state"], "empty_slot_count": len(artifact.get("empty_slots", []))}


def scenario_state(labels: list[str | None]) -> str:
    if not labels or None in labels:
        return "TECHNICAL_INCOMPLETE"
    if not set(labels) <= BEHAVIOR_LABELS:
        raise EvaluationError("Invalid checkpoint label")
    if "UNRESOLVED" in labels:
        return "UNRESOLVED"
    if "DISTORTED" in labels:
        return "DISTORTED"
    if all(x == "RETAINED" for x in labels):
        return "RETAINED"
    if all(x == "MISSING" for x in labels):
        return "MISSING"
    return "PARTIAL"


def behavior_counts(artifact: dict, reference: dict | None, decisions: dict[str, dict]) -> list[dict]:
    rows = []
    for kind in ("CORE", "BRANCH"):
        scene = next((s for s in (reference or {}).get("scenarios", []) if s["kind"] == kind), None)
        if scene is None:
            rows.append({"scenario_kind": kind, "scenario_id": None, "scenario_state":
                         (reference or {}).get(kind.lower()+"_status", "CORE_UNESTABLISHED" if kind == "CORE" else "NOT_ASSESSED"),
                         "established": False, "checkpoints": [], "discovered_errors": []})
            continue
        checkpoints = []
        for cp in scene["checkpoints"]:
            payload = decisions.get(cp["checkpoint_id"])
            if artifact["state"] not in VALID:
                payload = None
            elif not artifact["claims"]:
                payload = {"label": "MISSING", "matched_claim_ids": [], "deterministic_empty": True}
            checkpoints.append({"checkpoint_id": cp["checkpoint_id"], "decision": payload})
        labels = [c["decision"].get("label") if c["decision"] else None for c in checkpoints]
        rows.append({"scenario_kind": kind, "scenario_id": scene["scenario_id"],
                     "scenario_state": scenario_state(labels), "established": True, "checkpoints": checkpoints,
                     "discovered_errors": [c for c in checkpoints if c["decision"] and
                                            c["decision"].get("distorted_relations")]})
    return rows


def average(values):
    return sum(values) / len(values) if values else None


def conclusion(*, incomplete=False, lower=None, upper=None, behavior_adverse=False) -> str:
    if incomplete or lower is None or upper is None:
        return "SOURCE_OR_TECHNICALLY_INCOMPLETE"
    if lower < 0 < upper or (lower == 0 and upper != 0) or (upper == 0 and lower != 0):
        return "RESULT_UNCERTAIN"
    if lower > 0 and not behavior_adverse:
        return "DESCRIPTIVE_ADVANTAGE_ON_OBSERVED_SET"
    return "MIXED_OR_NO_ADVANTAGE"


def causal_pair(left: dict, right: dict) -> dict:
    shared = left.get("pre_revision_hash") and left.get("pre_revision_hash") == right.get("pre_revision_hash")
    return {"shared_pre_revision_hash": left.get("pre_revision_hash") if shared else None,
            # Hash equality alone is necessary, not sufficient (permissions/budgets must also match).
            "causal_gain_established": False,
            "status": "MATCHED_INITIAL_ARTIFACT_CONTROLS_REQUIRE_VERIFICATION" if shared else "FINAL_SYSTEM_COMPARISON_ONLY"}


def aggregate(manifest: dict, content: dict, behavior: dict, references: dict, view="RECONCILED") -> dict:
    cohort = manifest["cohort"]
    main = [t for t in cohort["targets"] if t["planned_group"] == "MAIN"]
    main_ids = {t["target_id"] for t in main}
    artifacts = [a for a in manifest["artifacts"] if a["target_id"] in main_ids]
    methods = cohort["methods"]
    if {(a["target_id"], a["method"]) for a in artifacts} != {(t, m) for t in main_ids for m in methods}:
        raise EvaluationError("Missing registered pairs in aggregation")
    per_artifact, per_scenario = [], []
    for a in artifacts:
        aid = a["artifact_id"]
        common = {k: a[k] for k in ("artifact_id", "target_id", "project_id", "method")}
        per_artifact.append({**common, **content_counts(a, content.get(aid, {}))})
        for row in behavior_counts(a, references.get(a["target_id"]), behavior.get(aid, {})):
            per_scenario.append({**common, **row})
    content_table, behavior_table, comparisons, errors = [], [], [], []
    for project in sorted({t["project_id"] for t in main}):
        targets = sorted(t["target_id"] for t in main if t["project_id"] == project)
        rows = [r for r in per_artifact if r["project_id"] == project]
        by_pair = {(r["target_id"], r["method"]): r for r in rows}
        common = [t for t in targets if all(by_pair[t, m]["cp_confirmed"] is not None for m in methods)]
        for method in methods:
            mr = [r for r in rows if r["method"] == method]
            counts = {k: sum(r[k] for r in mr) for k in ("n_final", "k_keep", "d_delete", "u_unresolved", "f_failed")}
            content_table.append({"project_id": project, "method": method, "main_targets": len(targets),
                "valid_nonempty": sum(r["cp_confirmed"] is not None for r in mr),
                "empty_outputs": sum(r["state"] == "EMPTY_OUTPUT" for r in mr),
                "technical_failed_artifacts": sum(r["state"] == "TECHNICAL_INCOMPLETE" for r in mr), **counts,
                "common_cp_target_ids": common, "common_cp_target_count": len(common),
                "comparison_scope": "COMMON_NONEMPTY_OUTPUT_SUBSET" if common != targets else "ALL_MAIN_TARGETS",
                **{k: average([by_pair[t, method][k] for t in common]) for k in ("cp_confirmed", "cp_lower", "cp_upper")}})
            reason_counts = Counter()
            unresolved_counts = Counter()
            for a in artifacts:
                if a["method"] == method and a["project_id"] == project:
                    for d in content.get(a["artifact_id"], {}).values():
                        if d and d.get("label") == "DELETE":
                            reason_counts.update(set(d["reasons"]))
                        elif d and d.get("label") == "UNRESOLVED":
                            unresolved_counts.update(set(d["reasons"]))
            errors.append({"project_id": project, "method": method, "delete_reasons": dict(sorted(reason_counts.items())),
                           "unresolved_reasons": dict(sorted(unresolved_counts.items())), "reason_counts_overlap": True})
            for kind in ("CORE", "BRANCH"):
                br = [r for r in per_scenario if r["project_id"] == project and r["method"] == method and r["scenario_kind"] == kind]
                states = Counter(r["scenario_state"] for r in br if r["established"])
                behavior_table.append({"project_id": project, "method": method, "scenario_kind": kind,
                    "frozen_scenarios": sum(r["established"] for r in br), "unestablished_targets": [r["target_id"] for r in br if not r["established"]],
                    "unestablished_states": dict(Counter(r["scenario_state"] for r in br if not r["established"])),
                    **{s: states[s] for s in sorted(BEHAVIOR_LABELS | {"TECHNICAL_INCOMPLETE"})}})
        if "AUCMR" not in methods:
            continue
        for method in methods:
            if method == "AUCMR":
                continue
            delta = average([by_pair[t, "AUCMR"]["cp_confirmed"] - by_pair[t, method]["cp_confirmed"] for t in common])
            low = average([by_pair[t, "AUCMR"]["cp_lower"] - by_pair[t, method]["cp_upper"] for t in common])
            high = average([by_pair[t, "AUCMR"]["cp_upper"] - by_pair[t, method]["cp_lower"] for t in common])
            wins = Counter("WIN" if by_pair[t, "AUCMR"]["cp_confirmed"] > by_pair[t, method]["cp_confirmed"] else
                           "LOSS" if by_pair[t, "AUCMR"]["cp_confirmed"] < by_pair[t, method]["cp_confirmed"] else "TIE" for t in common)
            transitions, adverse, incomplete = [], False, any(r["state"] == "TECHNICAL_INCOMPLETE" for r in rows)
            sr = {(r["target_id"], r["method"], r["scenario_kind"]): r for r in per_scenario}
            for kind in ("CORE", "BRANCH"):
                trans = Counter()
                for t in targets:
                    a, b = sr[t, "AUCMR", kind], sr[t, method, kind]
                    trans[(b["scenario_state"], a["scenario_state"])] += 1
                    incomplete |= kind == "CORE" and not a["established"]
                    incomplete |= a["scenario_state"] == "TECHNICAL_INCOMPLETE" or b["scenario_state"] == "TECHNICAL_INCOMPLETE"
                    adverse |= a["scenario_state"] in {"DISTORTED", "UNRESOLVED", "MISSING", "PARTIAL"} and b["scenario_state"] == "RETAINED"
                transitions += [{"scenario_kind": kind, "paired_state_from": k[0], "paired_state_to": k[1], "count": n} for k, n in sorted(trans.items())]
            comparisons.append({"project_id": project, "comparator": method, "common_cp_target_ids": common,
                "cp_delta": delta, "delta_lower": low, "delta_upper": high, "wins_ties_losses": dict(wins),
                "behavior_transitions": transitions, "interpretation": conclusion(incomplete=incomplete, lower=low, upper=high, behavior_adverse=adverse),
                "causal_gain_established": False})
    return {"view": view, "per_artifact": per_artifact, "per_scenario": per_scenario,
            "content_table": content_table, "behavior_table": behavior_table, "errors_table": errors,
            "paired_comparisons": comparisons, "statistical_unit": "DOCUMENT", "inferential_statistics": "NOT_REQUESTED"}
