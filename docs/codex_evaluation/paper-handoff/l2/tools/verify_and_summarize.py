"""Offline handoff integrity and aggregation check; Python standard library only.

No model imports, network requests, source mutation, or output files.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
RULES = [f"R{i}" for i in range(1, 8)]


def read(name):
    return json.loads((PACKAGE / "data" / name).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def calculate(rows, cases):
    methods = sorted({r["method"] for r in rows})
    targets = sorted(c["target_id"] for c in cases)
    by = {(r["target_id"], r["method"]): r for r in rows}
    assert len(by) == len(rows) == len(methods) * len(targets)
    common = [t for t in targets if all(by[t, m]["status"] == "COMPLETE" for m in methods)]
    primary, secondary, coverage = [], [], []
    for method in methods:
        all_rows = [by[t, method] for t in targets]
        complete = [r for r in all_rows if r["status"] == "COMPLETE"]
        coverage.append({"method": method, "planned": len(targets), "completed": len(complete),
                         "technical_failures": sum(r["status"] == "TECHNICAL_FAILURE" for r in all_rows),
                         "no_evaluable_content": sum(r["status"] == "NO_EVALUABLE_CONTENT" for r in all_rows),
                         "unprocessed": len(targets) - len(all_rows)})
        for subset, dest in (([by[t, method] for t in common], primary), (complete, secondary)):
            n = len(subset)
            totals = {rule: sum(r["counts"][rule] for r in subset) for rule in RULES}
            dest.append({"method": method, "N": n, "rule_totals": totals,
                         "rule_means": {k: v / n if n else None for k, v in totals.items()},
                         "rule_not_applicable_cases": {rule: sum(r["rule_resolution"].get(rule, {}).get("state") == "NOT_APPLICABLE_EMPTY_INPUT" for r in subset) for rule in RULES},
                         "total_violations": sum(totals.values()),
                         "total_per_case": sum(totals.values()) / n if n else None})
    return {"primary_common_case_table": primary, "secondary_all_available": secondary,
            "coverage_table": coverage, "common_target_ids": common}


def verify_records(rows, cases, inputs, responses, summary, tables):
    by_input = {a["artifact_id"]: a for a in inputs}
    assert len(by_input) == len(inputs) == len(rows) == 651
    by_request = {r["request_id"]: r for r in responses}
    assert len(by_request) == len(responses) == summary["accepted_response_count"]
    counts = Counter()
    used_requests = set()
    versions = Counter()
    protocol = read("protocol.json")
    for r in rows:
        a = by_input[r["artifact_id"]]
        assert (a["task_id"], a["method"], a["input_hash"], a["input_path"], a["input_status"]) == (
            r["target_id"], r["method"], r["input_hash"], r["input_path"], r["input_status"])
        slots = {}
        for claim in a["assertions"]:
            slot = slots.setdefault(claim["slot"], [])
            assert claim["original_index"] == len(slot)
            slot.append(claim)
        if r["status"] != "COMPLETE":
            assert r["counts"] is None and not r["groups"] and not r["violations"]
            continue
        assert len(r["groups"]) == 2 and all(g["status"] == "COMPLETE" for g in r["groups"])
        assert r["violations"] == [v for g in r["groups"] for v in g["violations"]]
        assert r["counts"] == {rule: sum(v["rule_id"] == rule for v in r["violations"]) for rule in RULES}
        for v in r["violations"]:
            contents = slots.get(v["offending_slot"], [])
            if v["offending_slot"] == "primary_actor":
                contents = a["primary_actor_slot"].get("claims", [])
            idx = v["offending_claim_index"]
            assert contents and type(idx) is int and -1 <= idx < len(contents)
        for rule, resolution in r["rule_resolution"].items():
            if resolution["state"] in ("NOT_APPLICABLE_EMPTY_INPUT", "SATISFIED_LITERAL_ACTOR_PRESENCE"):
                assert r["counts"][rule] == 0
        for g in r["groups"]:
            counts["groups"] += 1
            versions[g["version"]] += 1
            rid = g["request_id"]
            if rid:
                assert rid not in used_requests
                used_requests.add(rid)
                receipt = by_request[rid]
                assert receipt["artifact_id"] == r["artifact_id"] and receipt["group"] == g["group"]
                assert receipt["response_model"] == "glm-4.6v" and receipt["finish_reason"] == "stop"
                payload = {k: v for k, v in receipt["request_payload"].items() if k != "messages"}
                assert payload == protocol["observed_payload_without_messages"]
            else:
                counts["deterministic_groups"] += 1
    assert used_requests == set(by_request)
    assert counts == {"groups": summary["total_record_groups"],
                      "deterministic_groups": summary["deterministic_group_count"]}
    assert dict(versions) == summary["implementation_version_distribution"]
    assert dict(Counter(r["response_model"] for r in responses)) == summary["response_model_counts"]
    statuses = Counter(r["status"] for r in rows)
    assert statuses["COMPLETE"] == summary["complete_pairs"]
    assert statuses["TECHNICAL_FAILURE"] == summary["technical_failures"]
    assert statuses["NO_EVALUABLE_CONTENT"] == summary["no_evaluable_pairs"]
    assert sum(statuses.values()) == summary["planned_pairs"] == 651
    result = calculate(rows, cases)
    for key, value in result.items():
        assert value == tables[key], f"TABLE_MISMATCH:{key}"
    assert len(result["common_target_ids"]) == summary["primary_common_cases"]
    assert len(result["common_target_ids"]) * 7 == summary["primary_common_pairs"]
    return result


def verify_selection(rows, inputs, supplemental_rows, supplemental_inputs):
    main = {r["artifact_id"]: r for r in rows}
    main_inputs = {r["artifact_id"]: r for r in inputs}
    extra = {r["artifact_id"]: r for r in supplemental_rows}
    extra_inputs = {r["artifact_id"]: r for r in supplemental_inputs}
    substitutions = read("input_substitutions.json")
    ids = {r["artifact_id"] for r in substitutions}
    assert len(substitutions) == len(supplemental_rows) == len(supplemental_inputs) == 3
    assert set(extra) == set(extra_inputs) == ids
    assert all(r["input_path"].startswith("outputs/junxi_unified93_deepseek_flash_20261022/") for r in inputs)
    for s in substitutions:
        aid = s["artifact_id"]
        assert s["used_in_primary"] is False and s["used_in_supplemental"] is True
        assert s["direct_pairing_with_original_L1_L4"] is False
        assert main[aid]["status"] == "NO_EVALUABLE_CONTENT" and main[aid]["counts"] is None
        assert extra[aid]["status"] == "COMPLETE"
        for item in (main[aid], main_inputs[aid]):
            assert (item["input_path"], item["input_hash"]) == (s["original_input"], s["original_input_hash"])
        for item in (extra[aid], extra_inputs[aid]):
            assert (item["input_path"], item["input_hash"]) == (s["selected_input"], s["selected_input_hash"])
        assert s["original_input_hash"] != s["selected_input_hash"]
    selection = read("selection.json")
    assert selection["evaluator_repairs_retained"] is True
    assert selection["L1_L4_inputs_and_results_unchanged"] is True
    assert selection["supplemental"]["replacement_artifact_ids"] == sorted(ids)
    excluded = {r["artifact_id"] for r in rows if r["status"] == "NO_EVALUABLE_CONTENT"}
    exclusions = read("exclusions.json")
    assert len(exclusions) == len(excluded) == 5
    assert {r["artifact_id"] for r in exclusions} == excluded
    for e in exclusions:
        assert e["counts"] is None and e["generation_coverage_missing"] is True
        assert e["input_hash"] == main[e["artifact_id"]]["input_hash"]
    for filename, records in (("examples.json", main), ("supplemental/examples.json", extra)):
        for example in read(filename):
            assert example["violation"] in records[example["artifact_id"]]["violations"]
    return ([extra.get(r["artifact_id"], r) for r in rows],
            [extra_inputs.get(r["artifact_id"], r) for r in inputs])


def verify_original_selection(root, rows, inputs, calculated, scope):
    selection = read("selection.json")[scope]
    manifest_path = root / selection["result_manifest"]
    assert sha(manifest_path) == selection["result_manifest_sha256"]
    manifest = json.loads(manifest_path.read_text())
    selected = {r["artifact_id"]: r for r in manifest["records"]}
    assert len(selected) == len(rows)
    for row in rows:
        item = selected[row["artifact_id"]]
        # Historical manifests contain absolute paths; compare the recorded
        # repository-relative suffix without rewriting those frozen manifests.
        assert item["path"] == row["source_record"] or item["path"].endswith("/" + row["source_record"])
        raw = json.loads((root / row["source_record"]).read_text())
        assert row["source_record_signed_sha256"] == item["sha256"] == raw["sha256"]
        for key in ("artifact_id", "target_id", "method", "status", "input_status", "input_path",
                    "input_hash", "counts", "violations", "rule_resolution"):
            assert row[key] == raw[key], f"SOURCE_RECORD_MISMATCH:{row['artifact_id']}:{key}"
    if scope == "primary":
        path = root / selection["input_manifest"]
        assert sha(path) == selection["input_manifest_sha256"]
        bindings = {r["artifact_id"]: r for r in json.loads(path.read_text())}
        for item in inputs:
            assert all(value == bindings[item["artifact_id"]][key] for key, value in item.items())
        saved_path = root / selection["saved_tables"]
        assert sha(saved_path) == selection["saved_tables_sha256"]
        saved = json.loads(saved_path.read_text())
        assert all(value == saved[key] for key, value in calculated.items())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, help="Also verify original repository source files; all must be present.")
    parser.add_argument("--json", action="store_true", help="Print verified tables as JSON instead of the compact summary.")
    parser.add_argument("--scope", choices=("main", "supplemental", "both"), default="main",
                        help="Output scope; the paper main table always uses original frozen inputs (89 cases).")
    args = parser.parse_args()
    manifest = json.loads((PACKAGE / "manifest.json").read_text())
    for item in manifest["package_files"]:
        path = PACKAGE / item["path"]
        assert path.is_file() and sha(path) == item["sha256"], f"PACKAGE_HASH_MISMATCH:{item['path']}"
    rows, cases, inputs = read("final_records.json"), read("cases.json"), read("inputs.json")
    supplemental_rows, supplemental_inputs = verify_selection(
        rows, inputs, read("supplemental/final_records.json"), read("supplemental/inputs.json"))
    responses, added_responses = read("accepted_responses.json"), read("supplemental/accepted_responses.json")
    assert len(responses) == 1231 and len(added_responses) == 6
    assert not {r["request_id"] for r in responses} & {r["request_id"] for r in added_responses}
    summary, supplemental_summary = read("summary.json"), read("supplemental/summary.json")
    tables, supplemental_tables = read("tables.json"), read("supplemental/tables.json")
    assert tables["result_scope"] == "PAPER_PRIMARY_ORIGINAL_FROZEN_INPUTS"
    assert supplemental_tables["paper_primary"] is False and supplemental_summary["paper_primary"] is False
    assert (summary["complete_pairs"], summary["technical_failures"], summary["no_evaluable_pairs"],
            summary["primary_common_cases"], summary["selected_input_substitutions"]) == (646, 0, 5, 89, 0)
    assert (supplemental_summary["complete_pairs"], supplemental_summary["technical_failures"],
            supplemental_summary["no_evaluable_pairs"], supplemental_summary["primary_common_cases"]) == (649, 0, 2, 91)
    result = verify_records(rows, cases, inputs, responses, summary, tables)
    supplemental_result = verify_records(supplemental_rows, cases, supplemental_inputs,
                                         responses + added_responses, supplemental_summary, supplemental_tables)
    for case in cases:
        assert case["included_in_primary_common"] == (case["target_id"] in result["common_target_ids"])
        assert case["included_in_supplemental_common"] == (case["target_id"] in supplemental_result["common_target_ids"])
    for project in read("source_documents.json"):
        assert project["primary_common_cases"] == sum(
            c["source_project"] == project["project"] and c["included_in_primary_common"] for c in cases)
    if args.source_root:
        bad = []
        for item in read("source_files.json"):
            path = args.source_root / item["path"]
            if not path.is_file() or sha(path) != item["sha256"]:
                bad.append(item["path"])
        if bad:
            raise AssertionError("SOURCE_MISSING_OR_CHANGED:" + json.dumps(bad, ensure_ascii=False))
        verify_original_selection(args.source_root, rows, inputs, result, "primary")
        verify_original_selection(args.source_root, supplemental_rows, supplemental_inputs, supplemental_result, "supplemental")
    selected_tables = {"main": tables, "supplemental": supplemental_tables}
    if args.json:
        print(json.dumps(selected_tables if args.scope == "both" else selected_tables[args.scope], ensure_ascii=False, indent=2))
    else:
        print("PASS: package hashes, frozen-input selection, 1231 main + 6 supplemental response links and both sets of tables verified.")
        for scope in (("main", "supplemental") if args.scope == "both" else (args.scope,)):
            print("MAIN: 646 complete; 0 technical failures; 5 generation exclusions; 89 common cases."
                  if scope == "main" else "SUPPLEMENTAL ONLY: 649 complete; 0 technical failures; 2 generation exclusions; 91 common cases.")
            for row in selected_tables[scope]["primary_common_case_table"]:
                print(f"{row['method']}: N={row['N']}, total={row['total_violations']}, mean={row['total_per_case']:.6f}")


if __name__ == "__main__":
    main()
