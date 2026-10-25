"""Offline regressions for the producer's omitted-empty-list representation."""
import json
from copy import deepcopy
from unittest.mock import patch

import pytest

from eval.agent_evaluation.adapters import adapt as adapt_l3, anonymous as anonymous_l3
from eval.l2_independent.common import SLOTS
from eval.l2_independent.inputs import adapt_bytes, anonymous as anonymous_l2
from eval.l4_agent.inputs import adapt as adapt_l4
from eval.l4_agent.engine import _judge_one
from eval.l4_agent.records import validate_record
from eval.l4_agent.export import export_records
from eval.implementation import implementation_manifest
from eval.serialized_slots import omitted_claims_are_empty


def output():
    return {"use_case_name": "Save record", "actor": "User",
            "slots": {s: {"slot": s, "claims": []} for s in SLOTS}}


def adapt_all(data, tmp_path, method="Example"):
    raw = json.dumps(data, ensure_ascii=False).encode()
    path = tmp_path / "artifact.json"
    path.write_bytes(raw)
    target = {"target_id": "T1", "task_id": "T1", "project_id": "fixture",
              "source_project": "fixture", "planned_group": "MAIN", "comparison_group": "MAIN",
              "name": "Save record", "actor": "User", "primary_actor": "User"}
    l2 = adapt_bytes(raw, target, method, "fixture", data_origin="FIXTURE")
    l3 = adapt_l3(path, target, method)
    with patch("eval.l4_agent.inputs.REPO", tmp_path):
        l4 = adapt_l4({"path": str(path), "method": method}, target, [method])
    assert path.read_bytes() == raw
    return l2, l3, l4


def states(artifacts):
    a, b, c = artifacts
    return a["input_status"], b["state"], c["state"]


@pytest.mark.parametrize("metadata", [{}, {"coverage_state": {
    "covered_aspect_ids": ["INTERNAL_NOT_FINAL"], "missing_aspects": []}}])
@pytest.mark.parametrize("method", ["AUCMR", "CREST w/o cross-slot check", "TextRAG-Elaborate"])
def test_sparse_empty_slot_preserves_nonempty_claims(tmp_path, metadata, method):
    data = output()
    text = "  Save original record.  "
    data["slots"]["main_flow"]["claims"] = [{"content": text, "step_number": "1a"}]
    data["slots"]["exception_flow"] = {"slot": "exception_flow", **metadata}
    before = deepcopy(data)
    a, b, c = adapt_all(data, tmp_path, method)
    assert states((a, b, c)) == ("PRESENT", "PRESENT", "PRESENT")
    assert all("exception_flow" in row["empty_slots"] for row in (a, b, c))
    assert a["assertions"][0]["text"] == b["claims"][0]["text"] == c["slots"]["main_flow"]["claims"][0]["content"] == text
    assert a["assertions"][0]["step_number"] == c["slots"]["main_flow"]["claims"][0]["step_number"] == "1a"
    assert "INTERNAL_NOT_FINAL" not in json.dumps(anonymous_l2(a))
    assert "INTERNAL_NOT_FINAL" not in json.dumps(anonymous_l3(b, {"name": "Save record", "actor": "User"}))
    assert "INTERNAL_NOT_FINAL" not in json.dumps(c["slots"])
    assert data == before
    explicit = deepcopy(data)
    explicit["slots"]["exception_flow"]["claims"] = []
    explicit_rows = adapt_all(explicit, tmp_path, method)
    assert [r["empty_slots"] for r in explicit_rows] == [r["empty_slots"] for r in (a, b, c)]


@pytest.mark.parametrize("item", [
    {"slot": "goal", "claims": None}, {"slot": "goal", "claims": ""},
    {"slot": "goal", "claims": {}}, {"slot": "goal", "claims": [None]},
    {"slot": "goal", "raw_text": "Unparsed final text"},
    {"slot": "goal", "content": "Unrecognized final text"},
    {"slot": "goal", "coverage_state": "invalid"},
    {"slot": "goal", "raw_text": False},
    {"slot": "main_flow"},
])
@pytest.mark.parametrize("refusal", [False, True])
def test_malformed_slots_never_become_empty_even_with_refusal(tmp_path, item, refusal):
    data = output()
    data["slots"]["goal"] = item
    if refusal:
        data["quality_flags"] = [{"flag_type": "unresolvable_use_case"}]
    assert states(adapt_all(data, tmp_path)) == ("STRUCTURE_ERROR",) * 3


@pytest.mark.parametrize("method", ["AUCMR", "OtherMethod"])
def test_early_refusal_is_rule_scored_without_model(tmp_path, method):
    data = output()
    data["slots"] = {"goal": {"slot": "goal", "coverage_state": {
        "covered_aspect_ids": ["goal"], "missing_aspects": ["goal statement"]}}}
    data["quality_flags"] = [{"flag_type": "unresolvable_use_case", "slot": "goal"}]
    a, b, c = adapt_all(data, tmp_path, method)
    assert states((a, b, c)) == ("EXPLICIT_REFUSAL", "ABSTAINED", "REFUSED")
    assert all(row["omitted_slots"] == list(SLOTS[1:]) for row in (a, b, c))
    assert c["empty_slots"] == list(SLOTS)
    config = {"data_class": "MOCK_ONLY", "judge": {"judge_id": "fixture", "version": "1"},
              "model": {"config_id": "NOT_BOUND"}, "methods": [method],
              "targets": [{"task_id": "T1", "project_id": "fixture", "comparison_group": "MAIN"}],
              "input_bindings": [{"task_id": "T1", "method": method}]}
    manifest = {"manifest_sha256": "fixture", "sources": [], "versions": {}}
    # No client exists: a refusal must finish without attempting any judge call.
    record = _judge_one(c, {}, [], config, manifest, tmp_path, None)
    validate_record(record, c, manifest, config)
    assert all(v["score"] == 0 for v in record["pcs"].values())
    assert record["prc"]["score"] is None
    assert record["prc"]["status"] == "NO_EVALUABLE_CONTENT"
    assert record["model_requests"] == 0
    assert record["processing_status"] == "REFUSED"
    summary = export_records([record], config, tmp_path / "exports")
    group = summary["groups"][0]
    assert group["counts"]["refusal"] == 1
    assert group["counts"]["technical_failure"] == 0
    assert group["available_pcs"]["denominator"] == 1
    assert group["available_pcs"]["mean"] == 0
    assert group["available_prc"]["denominator"] == 0
    assert group["available_prc"]["mean"] is None


@pytest.mark.parametrize("refusal", [False, True])
def test_missing_goal_is_not_early_refusal(tmp_path, refusal):
    data = output()
    del data["slots"]["goal"]
    if refusal:
        data["quality_flags"] = [{"flag_type": "unresolvable_use_case"}]
    assert states(adapt_all(data, tmp_path)) == ("STRUCTURE_ERROR",) * 3


def test_nonempty_refusal_preserves_body(tmp_path):
    data = output()
    data["slots"]["goal"] = {"claims": [{"content": "Save record"}]}
    data["quality_flags"] = [{"flag_type": "unresolvable_use_case"}]
    assert states(adapt_all(data, tmp_path)) == ("PRESENT",) * 3
    del data["slots"]["exception_flow"]
    assert states(adapt_all(data, tmp_path)) == ("STRUCTURE_ERROR",) * 3


def test_all_sparse_slots_are_empty_not_perfect(tmp_path):
    data = output()
    data["slots"] = {s: {"slot": s} for s in SLOTS}
    assert states(adapt_all(data, tmp_path)) == ("VALID_EMPTY", "VALID_EMPTY", "EMPTY_OUTPUT")


def test_sparse_representation_requires_marker_outside_refusal():
    assert not omitted_claims_are_empty({}, "goal")
    assert omitted_claims_are_empty({}, "goal", refusal=True)
    assert not omitted_claims_are_empty({"other_text": "hidden"}, "goal", refusal=True)


@pytest.mark.parametrize("layer", ["l2", "l3", "l4"])
def test_shared_reader_is_part_of_frozen_implementation(layer):
    assert "eval/serialized_slots.py" in implementation_manifest(layer)
