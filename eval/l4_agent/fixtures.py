"""Constructed deterministic examples only. Never load real outputs into this mock."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from .protocol import PROTOCOL_VERSION, REPO, SLOTS, digest, file_hash, save_new

SCENARIOS = ("correct", "conflict", "wrong_slot", "empty_slot", "insufficient", "malformed",
             "empty", "refusal", "missing", "broken_response", "partial_response")
TEXT = {
    "goal": "Save a document.", "preconditions": "An open document is required.",
    "trigger": "The editor selects Save.", "main_flow": "1. The system writes the document to a file.",
    "alternative_flow": "Save As allows choosing a different file name.",
    "exception_flow": "On a write failure, show an error and preserve the document.",
    "postconditions": "The saved document can be reopened."}


def make_fixtures(run_dir: Path) -> dict:
    import fitz
    root = run_dir / "fixtures"
    root.mkdir(exist_ok=False)
    pdf_path = root / "original.pdf"
    with fitz.open() as pdf:
        page = pdf.new_page(width=480, height=360)
        page.insert_text((24, 30), "FIXTURE - NOT_RESEARCH_DATA\nSave a Document", fontsize=11)
        for i, statement in enumerate(TEXT.values()):
            page.insert_text((24, 70 + i * 24), statement, fontsize=10)
        page.draw_rect(fitz.Rect(24, 260, 120, 300), color=(0, 0, 1))
        page.insert_text((30, 285), "Save -> File", fontsize=11)
        pdf.save(pdf_path)
    methods = ["Fixture-Method-A", "Fixture-Method-B"]
    config = {"protocol_version": PROTOCOL_VERSION, "data_class": "MOCK_ONLY", "research_status": "NOT_RESEARCH_DATA", "batch_id": "SYNTHETIC_L4_V1",
              "live_authorized": False, "generated_slots": list(SLOTS),
              "judge": {"judge_id": "fixture-judge", "version": "1"},
              "model": {"config_id": "FIXTURE_CLIENT_V1", "binding_status": "NOT_BOUND", "provider": None, "model_id": None},
              "source_policy": {"search_max": 3, "pages_max": 2, "request_rounds": 3, "tools_per_round": 4},
              "sources": [{"source_id": "SRC-FIXTURE", "project_id": "fixture-editor", "path": pdf_path.relative_to(REPO).as_posix(),
                           "expected_sha256": file_hash(pdf_path)}], "methods": methods, "targets": [], "input_bindings": []}
    scenarios = {}
    for index, scenario in enumerate(SCENARIOS):
        tid = "F-" + str(index + 1).zfill(2)
        config["targets"].append({"task_id": tid, "project_id": "fixture-editor", "comparison_group": "FIXTURE", "name": "Save a Document", "actor": "Editor"})
        for method_index, method in enumerate(methods):
            path = root / (tid + "-" + str(method_index) + ".json")
            config["input_bindings"].append({"task_id": tid, "method": method, "path": path.relative_to(REPO).as_posix()})
            aid = "L4-" + digest([tid, method])[:24]
            scenarios[aid] = scenario
            if scenario == "missing" or (scenario == "conflict" and method_index == 1):
                continue
            artifact = {"data_class": "FIXTURE", "research_status": "NOT_RESEARCH_DATA", "use_case_name": "Save a Document", "actor": "Editor", "slots": {
                slot: {"claims": [{"content": value, **({"step_number": 1} if slot == "main_flow" else {})}]} for slot, value in TEXT.items()},
                "method": method, "quality_flags": [], "revision_history": ["PRIVATE_REPAIR_RECORD"],
                "old_human_score": 5, "L1": "PRIVATE_OLD_SCORE", "pre_constraint_slots": {"goal": "NEVER_USE_ME"}}
            # No generated evidence refs: correctness can still be checked against originals.
            artifact["slots"]["main_flow"]["raw_text"] = TEXT["main_flow"]
            if scenario == "conflict":
                artifact["slots"]["preconditions"]["claims"][0]["content"] = "An open document is not required."
            elif scenario == "wrong_slot":
                artifact["slots"]["goal"]["claims"][0]["content"] = TEXT["exception_flow"]
            elif scenario == "empty_slot":
                artifact["slots"]["alternative_flow"] = {"claims": []}
            elif scenario == "insufficient":
                artifact["slots"]["goal"]["claims"][0]["content"] = "Save to an unregistered cloud."
            elif scenario in {"empty", "refusal"}:
                artifact["slots"] = {s: {"claims": []} for s in SLOTS}
                if scenario == "refusal":
                    artifact["quality_flags"] = [{"flag_type": "unresolvable_use_case"}]
            elif scenario == "malformed":
                artifact["slots"]["trigger"] = "not a slot object"
            save_new(path, artifact)
    save_new(root / "fixture_manifest.json", {"data_class": "FIXTURE", "research_status": "NOT_RESEARCH_DATA", "scenarios": scenarios})
    return config


class FixtureClient:
    """Scripted mock oracle for these constructed cases; not a semantic model."""
    def __init__(self, scenarios: dict):
        self.scenarios = scenarios
        self.calls = 0

    def respond(self, request: dict, history: list) -> str:
        self.calls += 1
        artifact = request["artifact"]
        scenario = self.scenarios[artifact["artifact_id"]]
        sid = artifact["sources"][0]["source_id"]
        if not history:
            return json.dumps({"tool_calls": [
                {"name": "source_index", "arguments": {"source_id": sid}},
                {"name": "source_search", "arguments": {"source_id": sid, "query": "unregistered cloud" if scenario == "insufficient" else "document"}},
                {"name": "source_page", "arguments": {"source_id": sid, "pdf_page": 1}},
                {"name": "source_page", "arguments": {"source_id": sid, "pdf_page": 1, "image": True}}]})
        if scenario == "broken_response":
            return "{ damaged JSON"
        results = history[0]["tool_results"]
        access = results[2]["result"]["access_id"]
        def ref(slot):
            return {"source_id": sid, "pdf_page": 1, "access_id": access, "kind": "TEXT", "quote": TEXT[slot]}
        pcs = {}
        for slot in SLOTS:
            claims = artifact["slots"][slot]["claims"]
            if not claims:
                continue
            finding, score = "SUPPORTED", 3
            quote_slot = slot
            if scenario == "conflict" and slot == "preconditions":
                finding, score = "CONTRADICTED", 0
            elif scenario == "wrong_slot" and slot == "goal":
                finding, score, quote_slot = "WRONG_SLOT", 0, "exception_flow"
            elif scenario == "insufficient" and slot == "goal":
                finding, score = "UNRESOLVED", None
            unresolved = ["原文没有给出该云服务的保存行为，无法判断该断言。"] if score is None else []
            pcs[slot] = {"score": score, "status": "UNRESOLVED" if score is None else "SCORED",
                         "reason": "构造样例检查 " + slot + " 的原文陈述和槽位用途。",
                         "checks": [{"claim_id": c["claim_id"], "finding": finding,
                                     "rationale": "构造判定 " + finding + "；对应陈述为 " + TEXT[quote_slot],
                                     "source_refs": [] if score is None else [ref(quote_slot)]} for c in claims], "unresolved": unresolved}
        score = {"conflict": 2, "wrong_slot": 3, "empty_slot": 4, "insufficient": None}.get(scenario, 5)
        change = {"kind": "NONE", "location": "main_flow:0", "need": "已检查保存动作、失败保留和成功结果，构造样例中未发现阻碍继续使用的问题。", "source_refs": []}
        if scenario == "conflict":
            change = {"kind": "FACTUAL", "location": "preconditions:0", "need": "把不需要打开文档改为必须已打开文档，否则会执行错误前提。", "source_refs": [ref("preconditions")]}
        elif scenario == "wrong_slot":
            change.update(kind="ORGANIZATION", location="goal:0", need="将失败处理移出目标槽位，目标应表达保存意图。")
        elif scenario == "empty_slot":
            change.update(kind="ORGANIZATION", location="main_flow:0", need="在步骤号与叙述间添加明确的动作主体说明，便于后续编辑。")
        elif scenario == "insufficient":
            change.update(kind="ORGANIZATION", location="goal:0", need="先核实云服务目标，现有来源不足以判断可用性。")
        prc = {"score": score, "status": "UNRESOLVED" if score is None else "SCORED", "reason": change["need"],
               "modifications": [change], "unresolved": ["云服务行为没有原始说明。"] if score is None else []}
        if scenario == "partial_response":
            pcs.pop("trigger")
        return json.dumps({"pcs": pcs, "prc": prc}, ensure_ascii=False)
