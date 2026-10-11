import json
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from eval.compute_metrics import MetricsComputer, NLIJudge, UCMetrics
from eval.run_experiments import generate_latex_table_l1


class AlwaysEntails:
    def __init__(self):
        self.calls = 0

    def judge(self, claim, evidence_texts, image_paths=None, slot_name=None):
        self.calls += 1
        return {"entailment": "entailment", "rationale": "supported"}


uc = {
    "use_case_name": "UC",
    "actor": "Actor",
    "slots": {
        "goal": {"claims": [{"content": "delivered", "evidence_refs": ["E1"]}]},
    },
    "citation_audit": {
        "pre_constraint_slots": {
            "goal": {
                "exposed_evidence_refs": ["E1"],
                "claims": [
                    {"content": "joint claim", "evidence_refs": ["E1", "E2"]},
                    {"content": "invented id", "evidence_refs": ["E999"]},
                    {"content": "uncited", "evidence_refs": []},
                ],
            }
        }
    },
}
evidence = {
    "evidence_cards": [
        {"card_id": "E1", "content": "part A"},
        {"card_id": "E2", "content": "part B"},
        {"card_id": "E999", "content": "resolvable but not exposed"},
    ]
}

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    page_root = root / "data" / "images" / "doc"
    page_root.mkdir(parents=True)
    (page_root / "7.png").write_bytes(b"not-a-real-image-but-a-resolvable-file")
    resolved = MetricsComputer._resolve_card_image_paths({
        "source_doc": str(page_root), "source_pages": [7]
    })
    assert resolved == [str((page_root / "7.png").resolve())]
    # A logical document id is not an image directory.  Text chunks must not
    # borrow uncited content from the rest of their source page.
    assert MetricsComputer._resolve_card_image_paths({
        "source_doc": "doc", "source_pages": [7]
    }) == []
    uc_path = root / "uc.json"
    evidence_path = root / "evidence.json"
    uc_path.write_text(json.dumps(uc), encoding="utf-8")
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    judge = AlwaysEntails()
    computer = MetricsComputer(nli_judge=judge)
    metric = computer.compute_all(str(uc_path), str(evidence_path), run_nli=True)
    aggregate = computer.compute_aggregate([metric], "TextRAG-Elaborate")

assert (metric.total_claims, metric.total_cited_claims, metric.total_entailed_claims) == (3, 1, 1)
assert judge.calls == 1
assert aggregate["l1_citcov"] == 33.3
assert aggregate["l1_nli_g"] == 33.3
assert aggregate["l1_chg"] == 0.0
row = generate_latex_table_l1({"TextRAG-Elaborate": aggregate})
assert "TextRAG-Elaborate & 3 & 1 & 1 & 33.3 & 33.3 & 0.0" in row

uncited_only = UCMetrics("UC", "Actor", total_claims=2)
uncited_aggregate = MetricsComputer().compute_aggregate(
    [uncited_only], "TextRAG-Elaborate"
)
assert uncited_aggregate["l1_chg"] is None
assert "--" in generate_latex_table_l1({"TextRAG-Elaborate": uncited_aggregate})

call_lock = threading.Lock()
call_count = 0


def identical_judge(_prompt):
    global call_count
    with call_lock:
        call_count += 1
    time.sleep(0.1)
    return '{"entailment":"entailment","rationale":"same input"}'


deduplicated = NLIJudge(model_fn=identical_judge)
with ThreadPoolExecutor(max_workers=16) as pool:
    judgments = list(pool.map(
        lambda _index: deduplicated.judge("same claim", ["same evidence"]),
        range(32),
    ))
assert call_count == 1
assert sum(bool(item["judge_cache_hit"]) for item in judgments) == 31
assert {item["entailment"] for item in judgments} == {"entailment"}
print("L1_SINGLE_POPULATION_SMOKE_PASS")
