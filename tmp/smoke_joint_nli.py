from eval.compute_metrics import MetricsComputer


class JointJudge:
    def __init__(self):
        self.calls = []

    def judge(self, claim, evidence_texts, image_paths=None, slot_name=None):
        self.calls.append((claim, list(evidence_texts), list(image_paths or [])))
        combined = "\n".join(evidence_texts)
        label = "entailment" if "fact A" in combined and "fact B" in combined else "neutral"
        return {"entailment": label, "rationale": "joint smoke", "judge_model": "smoke"}


judge = JointJudge()
computer = MetricsComputer(nli_judge=judge)
uc = {
    "slots": {
        "goal": {
            "claims": [{
                "content": "A and B",
                "evidence_refs": ["E1", "E2", "E1"],
            }]
        }
    }
}
evidence = {
    "evidence_cards": [
        {"card_id": "E1", "content": "fact A", "image_paths": []},
        {"card_id": "E2", "content": "fact B", "image_paths": []},
    ]
}
entailed, details, failures = computer.compute_l1_nli_g(uc, evidence)
assert entailed == 1
assert len(judge.calls) == 1
assert len(judge.calls[0][1]) == 2
assert details[0]["judgment_mode"] == "joint_cited_evidence"
assert not failures
print("JOINT_NLI_SMOKE_PASS")
