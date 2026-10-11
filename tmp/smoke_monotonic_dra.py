import json

from crest.agents import (
    _apply_patch,
    _patch_needs_concrete_target,
    _refresh_dra_slot_audit,
    _verify_atomic_dra_patch,
)
from crest.prompts import ANSWER_GENERATOR, CLAIM_PATCH
from crest.prompts import CLAIM_PATCH
from crest.schemas import Claim, CockburnSlot, EvidenceCard, SlotContent


class RewriteProvider:
    def llm(self, _prompt):
        return json.dumps({
            "claims": [{
                "index": 0,
                "decision": "rewrite",
                "content": "Grounded inserted step.",
                "evidence_refs": ["EC-MF-003"],
                "missing_aspects": [],
            }]
        })


class InvalidProvider:
    def llm(self, _prompt):
        return "not-json"


def card(card_id):
    return EvidenceCard(
        card_id=card_id,
        content=f"Evidence for {card_id}",
        source_pages=[1],
        source_locations=[],
        confidence=0.9,
    )


cards = {item.card_id: item for item in [card("EC-MF-001"), card("EC-MF-002"), card("EC-MF-003")]}
slot = SlotContent(
    slot="main_flow",
    claims=[
        Claim("Stable first step.", ["EC-MF-001"], step_number=1),
        Claim("Stable second step.", ["EC-MF-002"], step_number=2),
    ],
    evidence_card_ids=["EC-MF-001", "EC-MF-002"],
)
before = [(claim.content, list(claim.evidence_refs)) for claim in slot.claims]
patch = {
    "action": "add",
    "new_claim": {
        "content": "Over-broad inserted step.",
        "evidence_refs": ["EC-MF-003"],
        "step_number": 2,
    },
}
verified, reason = _verify_atomic_dra_patch(
    CockburnSlot.MAIN_FLOW, patch, cards, RewriteProvider()
)
assert verified is not None, reason
assert verified["new_claim"]["content"] == "Grounded inserted step."
assert verified["new_claim"]["step_number"] == 2
ok, reason = _apply_patch(slot, verified, -1, cards)
assert ok, reason
_refresh_dra_slot_audit(CockburnSlot.MAIN_FLOW, slot, cards)
after_stable = [
    (claim.content, list(claim.evidence_refs))
    for claim in slot.claims
    if claim.content.startswith("Stable")
]
assert after_stable == before
assert [claim.step_number for claim in slot.claims] == [1, 2, 3]
assert len(slot.pre_constraint_draft.get("claims", [])) == 3

assert _patch_needs_concrete_target(
    {"action": "remove", "target_claim_index": -1}, -1, slot,
)
assert not _patch_needs_concrete_target(
    {"action": "remove", "target_claim_index": 1}, -1, slot,
)
assert not _patch_needs_concrete_target(
    {"action": "add", "new_claim": {"content": "x"}}, -1, slot,
)

rejected, reason = _verify_atomic_dra_patch(
    CockburnSlot.MAIN_FLOW, patch, cards, InvalidProvider()
)
assert rejected is None
assert "not supported" in reason

prompt_text = CLAIM_PATCH.template
assert '"action": "slot_rewrite"' not in prompt_text
assert "Never rewrite the whole slot" in prompt_text
assert "one concrete existing" in prompt_text
assert "Preserve evidence-supported DETAIL COVERAGE" in ANSWER_GENERATOR.template
assert "Keep audit metadata out of business claims" in ANSWER_GENERATOR.template
print("MONOTONIC_ATOMIC_DRA_SMOKE_PASS")
