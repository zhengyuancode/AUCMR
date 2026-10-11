"""Offline smoke for contextual, card-only L1 scoring."""

from eval.compute_metrics import MetricsComputer, NLIJudge


class CardOnlyJudge:
    def judge(self, claim, evidence_texts, image_paths=None, slot_name=None):
        assert image_paths is None, "L1 must not attach source images"
        joined = "\n".join(evidence_texts)
        if claim == "combined fact":
            assert "part A" in joined and "part B" in joined
            return {
                "entailment": "entailment", "slot_fit": True,
                "rationale": "the cited card union supports both parts",
                "slot_fit_rationale": "this is the stated goal",
            }
        return {
            "entailment": "entailment", "slot_fit": False,
            "rationale": "the cited card supports the fact",
            "slot_fit_rationale": "the fact is not a pre-interaction state",
        }


def main():
    uc = {
        "slots": {
            "goal": {"claims": [{"content": "combined fact", "evidence_refs": ["E1", "E2"]}]},
            "preconditions": {"claims": [{"content": "true but misplaced", "evidence_refs": ["E1"]}]},
        },
    }
    evidence = {
        "evidence_cards": [
            {"card_id": "E1", "content": "part A", "source_doc": "data/images/jtvauctions", "source_pages": [1]},
            {"card_id": "E2", "content": "part B", "source_doc": "data/images/jtvauctions", "source_pages": [2]},
        ],
    }
    entailed, details, failures = MetricsComputer(CardOnlyJudge()).compute_l1_nli_g(uc, evidence)
    assert not failures
    assert entailed == 1
    assert [row["grounding_status"] for row in details] == [
        "entailed_and_slot_fit", "entailed_but_slot_misfit",
    ]
    parsed = NLIJudge(model_fn=lambda _prompt: '{"entailment":"entailment","rationale":"x"}').judge(
        "claim", ["card"], slot_name="goal",
    )
    assert parsed["slot_fit"] is False, "missing slot_fit must fail closed"
    print("L1_CONTEXTUAL_CARD_ONLY_SMOKE_PASS")


if __name__ == "__main__":
    main()
