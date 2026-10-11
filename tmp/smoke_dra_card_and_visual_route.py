"""No-network smoke for card-grounded DRA patches and L1 vision routing."""

import json

from config.settings import ModelEndpoint
from crest.agents import ModelProvider, _verify_atomic_dra_patch
from crest.schemas import CockburnSlot, EvidenceCard
from eval.compute_metrics import NLIJudge


def fake_llm(prompt, _images, _assistant, _overlap):
    assert 'Attached source-page bindings (when non-empty):\n[]' in prompt
    return json.dumps({
        "claims": [{
            "index": 0,
            "decision": "keep",
            "missing_aspects": [],
        }],
    })


def main():
    provider = ModelProvider(fake_llm, lambda *_args: (_ for _ in ()).throw(AssertionError("no LVLM")))
    card = EvidenceCard(
        card_id="EC-MF-000", content="The system records the submitted value.",
        source_pages=[1], source_locations=[], source_doc="data/images/jtvauctions",
    )
    patch, reason = _verify_atomic_dra_patch(
        CockburnSlot.MAIN_FLOW,
        {"action": "add", "new_claim": {"content": "The system records the submitted value.", "evidence_refs": [card.card_id]}},
        {card.card_id: card}, provider,
    )
    assert patch is not None and not reason
    assert provider.stats_snapshot()["lvlm_calls"] == 0

    calls = []
    import config.settings as settings
    original = settings.chat_completion
    try:
        settings.chat_completion = lambda endpoint, messages, image_paths=None: (
            calls.append((endpoint.model_name, list(image_paths or []))) or
            '{"entailment":"entailment","slot_fit":true,"rationale":"x","slot_fit_rationale":"y"}'
        )
        judge = NLIJudge(
            endpoint=ModelEndpoint(model_name="text-judge"),
            vision_endpoint=ModelEndpoint(model_name="vision-judge"),
        )
        image = "data/images/jtvauctions/1.png"
        result = judge.judge("claim", ["image evidence card"], [image], "main_flow")
        assert result["judge_model"] == "vision-judge"
        assert calls == [("vision-judge", [image])]
    finally:
        settings.chat_completion = original
    print("DRA_CARD_AND_VISUAL_ROUTE_SMOKE_PASS")


if __name__ == "__main__":
    main()
