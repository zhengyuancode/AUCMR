from __future__ import annotations

import json
from pathlib import Path

import pytest

from config.settings import ModelEndpoint
from eval.compute_metrics import MetricsComputer, SlotMetrics, UCMetrics
from eval.compute_metrics import NLIJudge
from eval.run_experiments import ExperimentRunner, generate_latex_table_l1


class AlwaysEntails:
    def __init__(self):
        self.calls = 0

    def judge(self, claim, evidence_texts, image_paths=None):
        self.calls += 1
        return {"entailment": "entailment", "rationale": "supported"}


def _uc(claims):
    return {
        "use_case_name": "UC",
        "actor": "Actor",
        "slots": {
            "primary_actor": {
                "claims": [{"content": "Actor", "evidence_refs": []}],
                "raw_text": "Actor",
            },
            "goal": {"claims": claims, "raw_text": ""},
        },
    }


def test_l1_excludes_external_actor_and_requires_resolvable_exposed_id(tmp_path):
    evidence = {
        "evidence_cards": [
            {"card_id": "P1", "content": "source", "image_paths": []},
            {"card_id": "P2", "content": "", "image_paths": [str(tmp_path / "missing.png")]},
        ]
    }
    claims = [
        {"content": "valid", "evidence_refs": ["P1"]},
        {"content": "not exposed", "evidence_refs": ["P999"]},
        {"content": "missing image", "evidence_refs": ["P2"]},
    ]
    computer = MetricsComputer(nli_judge=AlwaysEntails())
    rate, cited, total = computer.compute_l1_scg(_uc(claims), evidence)
    assert (cited, total) == (1, 3)
    assert rate == pytest.approx(100 / 3)


def test_l1_judges_all_claims_and_each_source_until_one_entails():
    class PerSource:
        def __init__(self):
            self.calls = 0

        def judge(self, claim, evidence_texts, image_paths=None):
            self.calls += 1
            label = "entailment" if evidence_texts == ["support"] else "neutral"
            return {"entailment": label, "rationale": label}

    judge = PerSource()
    computer = MetricsComputer(nli_judge=judge)
    claims = [
        {"content": f"claim {index}", "evidence_refs": ["E1", "E2"]}
        for index in range(55)
    ]
    evidence = {"evidence_cards": [
        {"card_id": "E1", "content": "irrelevant"},
        {"card_id": "E2", "content": "support"},
    ]}
    entailed, details, failures = computer.compute_l1_nli_g(_uc(claims), evidence)
    assert entailed == 55
    assert len(details) == 55
    assert not failures
    assert judge.calls == 110


def test_nli_routes_text_and_image_evidence_to_different_endpoints(tmp_path, monkeypatch):
    calls = []

    def fake_chat(endpoint, messages, image_paths=None):
        calls.append((endpoint.model_name, list(image_paths or [])))
        return '{"entailment":"entailment","rationale":"supported"}'

    monkeypatch.setattr("config.settings.chat_completion", fake_chat)
    judge = NLIJudge(
        endpoint=ModelEndpoint(model_name="deepseek-v4-pro"),
        vision_endpoint=ModelEndpoint(model_name="qwen3.5-plus"),
    )
    text_result = judge.judge("claim", ["text evidence"])
    image = tmp_path / "1.png"
    image.write_bytes(b"not-decoded-by-mock")
    image_result = judge.judge("claim", [], [str(image)])
    assert calls == [
        ("deepseek-v4-pro", []),
        ("qwen3.5-plus", [str(image)]),
    ]
    assert text_result["judge_model"] == "deepseek-v4-pro"
    assert image_result["judge_model"] == "qwen3.5-plus"


def _metric(total, cited, entailed, empty=0):
    metric = UCMetrics(use_case_name="UC", actor="Actor")
    metric.total_claims = total
    metric.total_cited_claims = cited
    metric.total_entailed_claims = entailed
    metric.empty_slot_count = empty
    metric.slot_metrics = {name: SlotMetrics(name, is_filled=True) for name in (
        "goal", "primary_actor", "preconditions", "trigger", "main_flow",
        "alternative_flow", "exception_flow", "postconditions",
    )}
    metric.l2_violations = {f"R{index}": 0 for index in range(1, 8)}
    metric.l2_parse_failures = []
    return metric


def test_paper_l1_is_micro_averaged_and_table_matches_current_columns():
    aggregate = MetricsComputer(nli_judge=AlwaysEntails()).compute_aggregate(
        [_metric(1, 1, 1, 1), _metric(9, 4, 3, 2)], "TextRAG-Elaborate"
    )
    assert aggregate["l1_n_all"] == 10
    assert aggregate["l1_n_cite"] == 5
    assert aggregate["l1_n_ent"] == 4
    assert aggregate["l1_citcov"] == 50.0
    assert aggregate["l1_nli_g"] == 40.0
    assert aggregate["l1_chg"] == 20.0
    assert aggregate["l1_empty_slots"] == 3
    assert aggregate["l1_esc_per_uc"] == 1.5
    row = generate_latex_table_l1({"TextRAG-Elaborate": aggregate})
    assert "TextRAG-Elaborate & 10 & 5 & 4 & 50.0 & 40.0 & 20.0 & 3 / 1.50" in row


def _write_result(root: Path, method: str, use_case: str, actor: str, rejected=False):
    directory = root / method.replace(" ", "_")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{use_case}.json"
    data = {
        "method": method, "use_case_name": use_case, "actor": actor, "slots": {},
        "quality_flags": ([{"flag_type": "unresolvable_use_case"}] if rejected else []),
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    evidence = path.with_name(path.stem + "-evidence.json")
    evidence.write_text(json.dumps({"evidence_cards": []}), encoding="utf-8")
    return str(path), str(evidence)


def test_shared_cohort_comes_from_full_aucmr_fast_fail(tmp_path):
    methods = ["AUCMR (CREST full)", "TextRAG-Elaborate"]
    result_map = {}
    for method in methods:
        result_map[(method, "project", "valid", "Actor")] = _write_result(
            tmp_path, method, "valid", "Actor"
        )
        result_map[(method, "project", "rejected", "Actor")] = _write_result(
            tmp_path, method, "rejected", "Actor", rejected=(method == methods[0])
        )
    selected, manifest = ExperimentRunner.select_paper_cohort(result_map, methods)
    assert len(selected) == 2
    assert {key[2] for key in selected} == {"valid"}
    assert manifest["candidate_count"] == 2
    assert manifest["valid_count"] == 1


def test_cohort_rejects_missing_method_result(tmp_path):
    full = "AUCMR (CREST full)"
    result_map = {
        (full, "project", "valid", "Actor"): _write_result(tmp_path, full, "valid", "Actor")
    }
    with pytest.raises(RuntimeError, match="missing method/use-case"):
        ExperimentRunner.select_paper_cohort(result_map, [full, "TextRAG-Elaborate"])
