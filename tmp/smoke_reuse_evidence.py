"""Offline smoke for v5 frozen-evidence re-authoring.

No service, retriever, source image, or real model is touched.  It verifies the
new path emits all slots, keeps the immutable evidence pool, and cannot call
retrieval while re-authoring.
"""

import json
import re

from crest import CRESTOrchestrator
from crest.agents import ModelProvider
from crest.schemas import CockburnSlot, EvidenceCard


def forbidden_retrieval(*_args, **_kwargs):
    raise AssertionError("frozen-evidence authoring must not invoke retrieval")


def fake_llm(prompt, _images, _assistant, _is_overlap):
    if "internal semantic evidence verifier" in prompt:
        # Missing verifier rows default to keep in non-fail-closed author mode.
        return json.dumps({"claims": []})
    match = re.search(r'"card_id"\s*:\s*"([^"]+)"', prompt)
    assert match, "author prompt must expose a frozen evidence card"
    return json.dumps({
        "claims": [{
            "content": "A supported test interaction is recorded.",
            "evidence_refs": [match.group(1)],
        }],
        "missing_aspects": [],
    })


def main():
    abbreviations = {
        CockburnSlot.GOAL: "G",
        CockburnSlot.PRECONDITIONS: "PC",
        CockburnSlot.TRIGGER: "TR",
        CockburnSlot.MAIN_FLOW: "MF",
        CockburnSlot.ALTERNATIVE_FLOW: "AF",
        CockburnSlot.EXCEPTION_FLOW: "EF",
        CockburnSlot.POSTCONDITIONS: "PO",
    }
    cards = [
        EvidenceCard(
            card_id=f"EC-{abbr}-000",
            content="The documented interaction supports this test action.",
            source_pages=[1],
            source_locations=[],
            confidence=0.95,
        )
        for abbr in abbreviations.values()
    ]
    provider = ModelProvider(fake_llm, fake_llm)
    orchestrator = CRESTOrchestrator(
        retrieval_fn=forbidden_retrieval,
        file_names=["test"],
        topk=1,
        provider=provider,
        total_doc_pages=1,
        dra_max_rounds=0,
    )
    uc = orchestrator.elaborate_from_evidence_cards("Tester", "Test action", cards)
    assert len(uc.all_evidence_cards) == len(cards)
    assert uc.runtime_stats["evidence_reused"] is True
    assert uc.runtime_stats["reused_card_count"] == len(cards)
    expected = {slot.value for slot in CockburnSlot}
    assert set(uc.slots) == expected
    assert all(uc.slots[slot.value].claims for slot in CockburnSlot)
    print("FROZEN_EVIDENCE_REUSE_SMOKE_PASS")


if __name__ == "__main__":
    main()
