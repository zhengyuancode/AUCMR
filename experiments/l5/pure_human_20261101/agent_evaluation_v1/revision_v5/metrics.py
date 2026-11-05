"""L5 Agent evaluation metrics. No model calls, identity disclosure, or invented scores.

Only the offline aggregation command reads organizer mappings. Score computation is
independent of method identity. Original records remain immutable.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import random
import statistics

METRICS = ("C", "K", "R")
CATEGORIES = ("condition", "main", "branch", "result")
ROLES = ("score_primary", "score_secondary", "score_adjudicated")
SEED = 20261101
BOOTSTRAP_REPLICATES = 10000


def mean(values):
    values = [x for x in values if x is not None]
    return statistics.fmean(values) if values else None


def _issues(record):
    issues = record.get("unresolved", [])
    affected = set()
    for issue in issues:
        metric = issue.get("metric", "ALL") if isinstance(issue, dict) else "ALL"
        affected.update(METRICS if metric == "ALL" else [metric])
    return affected


def score_record(record, reference):
    """Compute the three frozen dimensions from legal item-level judgments.

    UNRESOLVED reference obligations do not enter C's denominator (L5 rule).
    Null product judgments are adjudication uncertainty, *not* source-Q or
    API failure. Affected point estimates are withheld; identification bounds
    are retained rather than silently deleting uncertain judgments.
    """
    obligations = reference["obligations"]
    ids = [o["id"] for o in obligations]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate reference IDs")
    if any(o.get("status") not in ("CONFIRMED", "UNRESOLVED", "EXCLUDED") for o in obligations):
        raise ValueError("Illegal reference status")
    confirmed = {o["id"]: o for o in obligations if o["status"] == "CONFIRMED"}
    coverage = record["coverage"]
    if len({c["obligation_id"] for c in coverage}) != len(coverage):
        raise ValueError("Duplicate coverage judgments")
    cov = {c["obligation_id"]: c for c in coverage}
    if not set(confirmed).issubset(cov) or not set(cov).issubset(ids):
        raise ValueError("Coverage/reference IDs mismatch")
    for c in coverage:
        if c.get("value") is not None and (isinstance(c["value"], bool) or c["value"] not in (0, .5, 1)):
            raise ValueError("Illegal coverage label")
    units = record["units"]
    if not isinstance(record.get("empty_behavior"), bool):
        raise ValueError("Explicit boolean empty_behavior is required")
    if record["empty_behavior"] != (len(units) == 0):
        raise ValueError("Empty declaration and semantic unit inventory disagree")
    seen = set()
    for u in units:
        if u["id"] in seen:
            raise ValueError("Duplicate semantic-unit ID")
        if u.get("judgment") not in ("S", "X", "U", "Q", None):
            raise ValueError("Illegal correctness label")
        if u.get("category") not in CATEGORIES:
            raise ValueError("Illegal semantic category")
        if u.get("duplicate_of") is not None and u["duplicate_of"] not in seen:
            raise ValueError("Redundancy must link to an earlier unit")
        if u.get("same_assertion_as") is not None and u["same_assertion_as"] not in seen:
            raise ValueError("Equivalent assertions must link to an earlier unit")
        seen.add(u["id"])

    affected = _issues(record)
    n = len(confirmed)
    values = [cov[k].get("value") for k in confirmed]
    c_unknown = sum(v is None for v in values)
    c_sum = sum(v for v in values if v is not None)
    if record["empty_behavior"] and any(v not in (0, None) for v in values):
        raise ValueError("Empty output cannot cover obligations")
    c_bounds = [c_sum / n, (c_sum + c_unknown) / n] if n else None
    c = c_sum / n if n and not c_unknown and "C" not in affected else None
    c_issues = [issue for issue in record.get("unresolved", [])
                if isinstance(issue, dict) and issue.get("metric") in ("C", "ALL")]
    localized_c_issues = bool(c_issues) and all(
        issue.get("metric") == "C" and issue.get("item_id") in confirmed
        and cov[issue["item_id"]].get("value") is None for issue in c_issues)
    if "C" in affected and not localized_c_issues:
        c_bounds = [0., 1.] if n else None

    # Identical semantics are counted once for K, even if their repetition
    # serves necessary context and therefore is *not* redundant under R.
    dedup = [u for u in units if u.get("duplicate_of") is None and u.get("same_assertion_as") is None]
    counts = Counter(u.get("judgment") for u in dedup)
    known_k = counts["S"] + counts["X"] + counts["U"]
    k_unknown = counts[None]
    if not units:
        k, k_bounds = 0., [0., 0.]
    elif k_unknown:
        denom = known_k + k_unknown
        k, k_bounds = None, [counts["S"] / denom, (counts["S"] + k_unknown) / denom]
    elif known_k:
        k = counts["S"] / known_k
        k_bounds = [k, k]
    else:
        k, k_bounds = None, None
    if "K" in affected or "R" in affected:
        # Undecided duplicate relationships can change K's deduplicated units.
        k, k_bounds = None, ([0., 1.] if units or "R" in affected else [0., 0.])
    m = len(units)
    d = sum(u.get("duplicate_of") is not None for u in units)
    r = d / m if m else None
    r_bounds = [r, r] if r is not None else None
    if "R" in affected:
        # A segmentation dispute can change M itself. Do not manufacture a
        # tighter upper bound by treating the disputed unit inventory as fixed.
        r, r_bounds = None, [0., 1.]

    by_category = {}
    for cat in CATEGORIES:
        cv = [cov[k].get("value") for k, o in confirmed.items() if o.get("category") == cat]
        cu = [u for u in dedup if u.get("category") == cat]
        cc = Counter(u.get("judgment") for u in cu)
        denominator = cc["S"] + cc["X"] + cc["U"]
        by_category[cat] = {
            "C": mean(cv) if cv and None not in cv and "C" not in affected else None,
            "C_N": len(cv),
            "K": cc["S"] / denominator if denominator and not cc[None] and not ({"K", "R"} & affected) else None,
            "K_denominator": denominator,
            "source_Q": cc["Q"],
        }
    return {
        "C": c, "K": k, "R": r,
        "bounds": {"C": c_bounds, "K": k_bounds, "R": r_bounds},
        "bound_notes": {
            "C": "Fixed confirmed denominator; localized null judgments range over 0 to 1; global scope uncertainty uses [0,1].",
            "R": "Conservative enclosure [0,1]; unresolved segmentation may change M, so the upper endpoint is not a claim of attainability."
                 if "R" in affected else "Fixed validated semantic-unit inventory; no outstanding R issue.",
        },
        "denominators": {"C_N": n, "K_S_X_U": known_k, "R_M": m},
        "counts": {"coverage_full": values.count(1), "coverage_partial": values.count(.5),
                   "coverage_missing": values.count(0), "coverage_unresolved": c_unknown,
                   "S": counts["S"], "X": counts["X"], "U": counts["U"],
                   "Q": counts["Q"], "assertion_unresolved": k_unknown, "D": d,
                   "reference_unresolved": sum(o["status"] == "UNRESOLVED" for o in obligations),
                   "reference_excluded": sum(o["status"] == "EXCLUDED" for o in obligations)},
        "unresolved_issues": record.get("unresolved", []),
        "empty_behavior": record["empty_behavior"],
        "by_category": by_category,
        "interpretation": "U = not established by shared requirements; not proof of real-world error",
    }


def weighted_kappa(pairs):
    """Linear weighted Cohen kappa for 0, .5, 1; constants are NA."""
    pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
    if not pairs or len({x for x, _ in pairs}) < 2 or len({y for _, y in pairs}) < 2:
        return None
    n = len(pairs)
    a, b = Counter(x for x, _ in pairs), Counter(y for _, y in pairs)
    observed = sum(abs(x-y) for x, y in pairs) / n
    expected = sum(a[x] * b[y] * abs(x-y) for x in a for y in b) / (n*n)
    return 1 - observed / expected if expected else None


def nominal_kappa(pairs):
    pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
    if not pairs or len({x for x, _ in pairs}) < 2 or len({y for _, y in pairs}) < 2:
        return None
    n = len(pairs)
    a, b = Counter(x for x, _ in pairs), Counter(y for _, y in pairs)
    observed = sum(x == y for x, y in pairs) / n
    expected = sum(a[k] * b[k] for k in set(a) | set(b)) / (n*n)
    return (observed - expected) / (1 - expected) if expected != 1 else None


def percentile(xs, q):
    xs = sorted(xs)
    position = (len(xs)-1)*q
    lo = int(position)
    return xs[lo] if position == lo else xs[lo] + (xs[lo+1]-xs[lo])*(position-lo)


def cluster_interval(project_differences, seed=SEED, replicates=BOOTSTRAP_REPLICATES):
    """Percentile bootstrap over project means, never over requests or agents."""
    values = list(project_differences)
    if not values:
        return None
    rng = random.Random(seed)
    draws = [statistics.fmean(rng.choices(values, k=len(values))) for _ in range(replicates)]
    return [percentile(draws, .025), percentile(draws, .975)]


def _read(path):
    return json.loads(path.read_text())


def aggregate(root):
    root = Path(root)
    mapping = _read(root / "organizer_private/mapping.json")
    if len(mapping) != 58 or len({m["artifact_code"] for m in mapping}) != 58:
        raise ValueError("Expected the fixed 58 anonymous artifacts")
    cases = defaultdict(dict)
    artifacts, failure_records = [], []
    source_records, calculated = {}, {}
    for item in mapping:
        code, task = item["artifact_code"], item["task_id"]
        cases[item["case_id"]][item["kind"]] = item
        refpath = root / "references" / (task + ".json")
        ref = _read(refpath) if refpath.exists() else None
        outputs = {}
        for role in ROLES:
            f = root / "records" / role / (code + ".json")
            if not f.exists() or ref is None:
                outputs[role] = None
                failure_records.append({"artifact_code": code, "role": role,
                                        "reason": "missing_reference" if ref is None else "missing_record"})
                continue
            try:
                record = _read(f)
                metrics = score_record(record, ref)
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                outputs[role] = None
                failure_records.append({"artifact_code": code, "role": role, "reason": "invalid_record", "detail": str(exc)})
                continue
            source_records[code, role] = record
            calculated[code, role] = metrics
            outputs[role] = metrics
        primary, secondary = (outputs[role] for role in ROLES[:2])
        independent = {}
        for metric in METRICS:
            v = [x[metric] if x is not None else None for x in (primary, secondary)]
            independent[metric] = mean(v) if None not in v else None
        artifacts.append({**item, "scores": outputs, "independent_mean": independent})

    per_case = []
    by_artifact = {x["artifact_code"]: x for x in artifacts}
    for cid, members in sorted(cases.items()):
        if set(members) != {"A", "B"}:
            raise ValueError("Each fixed target must have exactly A and B")
        a, b = (by_artifact[members[k]["artifact_code"]] for k in ("A", "B"))
        row = {"case_id": cid, "task_id": a["task_id"], "document": a["document"],
               "artifact_codes": {"A": a["artifact_code"], "B": b["artifact_code"]}}
        for mode in ("independent_mean", "adjudicated"):
            av = a[mode] if mode == "independent_mean" else a["scores"]["score_adjudicated"]
            bv = b[mode] if mode == "independent_mean" else b["scores"]["score_adjudicated"]
            row[mode] = {}
            for metric in METRICS:
                x = av.get(metric) if av else None
                y = bv.get(metric) if bv else None
                row[mode][metric] = {"A": x, "B": y, "A_minus_B": x-y if x is not None and y is not None else None,
                                      "paired": x is not None and y is not None}
        per_case.append(row)

    projects = sorted({r["document"] for r in per_case})
    per_project = []
    for project in projects:
        group = [r for r in per_case if r["document"] == project]
        row = {"document": project, "planned_targets": len(group)}
        for mode in ("independent_mean", "adjudicated"):
            row[mode] = {}
            for metric in METRICS:
                pairs = [r[mode][metric] for r in group if r[mode][metric]["paired"]]
                row[mode][metric] = {key: mean([x[key] for x in pairs]) for key in ("A", "B", "A_minus_B")}
                row[mode][metric].update({"paired_targets": len(pairs),
                    "unavailable_targets": [r["case_id"] for r in group if not r[mode][metric]["paired"]]})
        per_project.append(row)

    overall = {}
    for mode in ("independent_mean", "adjudicated"):
        overall[mode] = {}
        for metric in METRICS:
            groups = [p[mode][metric] for p in per_project if p[mode][metric]["A_minus_B"] is not None]
            overall[mode][metric] = {key: mean([g[key] for g in groups]) for key in ("A", "B", "A_minus_B")}
            overall[mode][metric].update({"A_minus_B_CI95": cluster_interval([g["A_minus_B"] for g in groups]),
                                          "projects_evaluable": len(groups), "projects_planned": len(projects),
                                          "paired_targets": sum(g["paired_targets"] for g in groups),
                                          "targets_planned": len(per_case)})

    agreement_rows = []
    coverage_pairs = []
    aligned_pairs = []
    alignment_counts = Counter()
    for item in mapping:
        code = item["artifact_code"]
        p, s = (source_records.get((code, role)) for role in ROLES[:2])
        if p is None or s is None:
            continue
        pc = {c["obligation_id"]: c.get("value") for c in p["coverage"]}
        sc = {c["obligation_id"]: c.get("value") for c in s["coverage"]}
        pairs = [(pc[k], sc[k]) for k in pc.keys() & sc.keys() if pc[k] is not None and sc[k] is not None]
        coverage_pairs.extend(pairs)
        row = {"artifact_code": code, "coverage_items": len(pairs), "coverage_linear_weighted_kappa": weighted_kappa(pairs),
               "coverage_exact_agreement": mean([float(a == b) for a, b in pairs]),
               "metric_absolute_differences": {metric: abs(calculated[code, ROLES[0]][metric]-calculated[code, ROLES[1]][metric])
                     if calculated[code, ROLES[0]][metric] is not None and calculated[code, ROLES[1]][metric] is not None else None for metric in METRICS}}
        # Optional explicit BLIND semantic alignment. IDs alone never constitute alignment.
        alignment_path = root / "records" / "alignment" / (code + ".json")
        if alignment_path.exists():
            alignment = _read(alignment_path)
            pu = {u["id"]: u for u in p["units"]}
            su = {u["id"]: u for u in s["units"]}
            used_p, used_s = set(), set()
            for match in alignment.get("groups", []):
                if match.get("audit_status")=="NOT_REVIEWED":
                    alignment_counts["administrative_not_reviewed_groups"]+=1
                    alignment_counts["administrative_not_reviewed_units"]+=len(match.get("left_ids",[]))+len(match.get("right_ids",[]))
                if match.get("relation") != "equivalent" or len(match.get("left_ids", [])) != 1 or len(match.get("right_ids", [])) != 1:
                    alignment_counts["unmatched_or_non_one_to_one"] += 1
                    continue
                pi, si = match["left_ids"][0], match["right_ids"][0]
                if pi not in pu or si not in su or pi in used_p or si in used_s or not match.get("reason"):
                    raise ValueError("Invalid explicit alignment")
                used_p.add(pi); used_s.add(si)
                if any(u.get("duplicate_of") or u.get("same_assertion_as") for u in (pu[pi], su[si])):
                    alignment_counts["duplicate_pairs_excluded"] += 1
                    continue
                pair = (pu[pi].get("judgment"), su[si].get("judgment"))
                if None not in pair:
                    aligned_pairs.append(pair)
                    alignment_counts["judged_one_to_one_pairs"] += 1
            alignment_counts["primary_units_without_equivalent_match"] += len(pu)-len(used_p)
            alignment_counts["secondary_units_without_equivalent_match"] += len(su)-len(used_s)
        else:
            alignment_counts["artifacts_without_alignment"] += 1
        agreement_rows.append(row)
    agreement = {
        "per_artifact": agreement_rows,
        "coverage_linear_weighted_kappa": weighted_kappa(coverage_pairs),
        "coverage_exact_agreement": mean([float(a == b) for a, b in coverage_pairs]),
        "coverage_item_pairs": len(coverage_pairs),
        "metric_absolute_difference_mean": {m: mean([r["metric_absolute_differences"][m] for r in agreement_rows]) for m in METRICS},
        "assertion_nominal_kappa": nominal_kappa(aligned_pairs),
        "assertion_alignment_counts": dict(alignment_counts),
        "assertion_scope": "Only explicitly blind-aligned equivalent one-to-one deduplicated units; many-to-one/unmatched units not silently assigned equal IDs",
        "interpretation": "Agreement is descriptive; correlated units are not independent projects and agreement is not accuracy or human validation",
    }
    result = {
        "protocol": "L5 Agent execution of frozen C/K/R; agent-only, no human ratings",
        "bootstrap": {"unit": "project", "replicates": BOOTSTRAP_REPLICATES, "seed": SEED,
                      "method": "percentile; project means from paired available targets; projects equally weighted"},
        "completion": {"planned_artifacts": len(mapping), "planned_targets": len(per_case), "planned_projects": len(projects),
                       "valid_records_by_role": {role: sum((i["artifact_code"], role) in source_records for i in mapping) for role in ROLES},
                       "technical_or_missing_records": failure_records},
        "per_artifact": artifacts, "per_case": per_case, "per_project": per_project,
        "overall": overall, "agreement": agreement,
        "limitations": ["This is Agent evaluation of selected PURE artifacts under shared evidence, not a human review.",
                        "U is lack of support in the common evidence, not certain real-world error.",
                        "Historical human authors may have had additional information; this is not a controlled human-vs-AI production experiment.",
                        "Intervals with few projects are unstable; neither nonsignificance nor agreement proves equivalence or noninferiority.",
                        "Missing or semantically unresolved measurements are disclosed; quality aggregates use evaluable pairs and may be incomplete."]}
    return result


def save_aggregate(root):
    root = Path(root)
    result = aggregate(root)
    out = root / "results"
    out.mkdir(exist_ok=True)
    files = {"all_results.json": result, "per_artifact.json": result["per_artifact"], "per_case.json": result["per_case"],
             "per_project.json": result["per_project"], "overall.json": result["overall"], "agreement.json": result["agreement"]}
    for filename, value in files.items():
        (out / filename).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    (out / "calculation_provenance.json").write_text(json.dumps({
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "result_sha256": hashlib.sha256((out / "all_results.json").read_bytes()).hexdigest(),
        "agent_only": True, "bootstrap_seed": SEED, "bootstrap_replicates": BOOTSTRAP_REPLICATES,
    }, indent=2) + "\n")
    lines = ["# L5 Agent 评价计算结果", "", "仅针对选定 PURE 材料与共同证据范围；不是人工评分，也不是专家验证。", ""]
    for mode in ("independent_mean", "adjudicated"):
        lines += ["## " + ("独立评分均值" if mode == "independent_mean" else "裁决结果"), "",
                  "| 指标 | A | B | A−B | 项目聚类 95% 区间 | 可评价项目/目标 |", "|---|---:|---:|---:|---|---|"]
        for metric in METRICS:
            row = result["overall"][mode][metric]
            fmt = lambda x: "NA" if x is None else f"{x:.4f}"
            interval = row["A_minus_B_CI95"]
            ci = "NA" if interval is None else f"[{fmt(interval[0])}, {fmt(interval[1])}]"
            lines.append(f"| {metric} | {fmt(row['A'])} | {fmt(row['B'])} | {fmt(row['A_minus_B'])} | {ci} | {row['projects_evaluable']}/{row['projects_planned']} 项目；{row['paired_targets']}/{row['targets_planned']} 目标 |")
        lines.append("")
    lines += ["C、K 越高越好；R 越低越好。U 只表示共同需求无法证实。", "",
              "未决测量不强制多数票定真；受影响指标记 NA，逐项记录和界限见 JSON。技术失败不计零分。", "",
              "不将差异不显著解释为相当或不劣，不将 Agent 一致性解释为准确性。"]
    (out / "REPORT.md").write_text("\n".join(lines) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    result = save_aggregate(args.root)
    print(json.dumps({"results": str(args.root / "results"), "completion": result["completion"]}, ensure_ascii=False))
