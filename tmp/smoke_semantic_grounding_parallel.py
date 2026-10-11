import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor

from crest.agents import ModelProvider, semantic_grounding_verifier
from crest.schemas import Claim, CockburnSlot, EvidenceCard, SlotContent
from eval.l2_judge import L2Judge


verifier_response = json.dumps({
    "claims": [
        {"index": 0, "decision": "keep", "content": "ignored", "evidence_refs": ["E1"], "missing_aspects": []},
        {"index": 1, "decision": "rewrite", "content": "The system displays an error.", "evidence_refs": ["E2"], "missing_aspects": ["explicit recovery behavior"]},
    ]
})
provider = ModelProvider(
    llm_call=lambda *_args, **_kwargs: verifier_response,
    lvlm_call=lambda *_args, **_kwargs: "{}",
)
cards = [
    EvidenceCard("E1", "The user submits.", [1], []),
    EvidenceCard("E2", "The system displays an error.", [2], []),
]
slot = SlotContent(
    slot="exception_flow",
    claims=[
        Claim("The user submits.", ["E1"]),
        Claim("The system displays an error and returns to Step 2.", ["E2"]),
    ],
)
verified = semantic_grounding_verifier(
    CockburnSlot.EXCEPTION_FLOW, slot, cards, provider
)
assert [claim.content for claim in verified.claims] == [
    "The user submits.", "The system displays an error.",
]
assert verified.missing_aspects == ["explicit recovery behavior"]
assert verified.pre_constraint_draft["claims"][1]["content"] == "The system displays an error."

# Goal rewrites must also update the scope anchor used by downstream retrieval.
goal_response = json.dumps({
    "claims": [{
        "index": 0, "decision": "rewrite", "content": "The customer pays for won items.",
        "evidence_refs": ["E1"], "missing_aspects": [],
    }]
})
goal_provider = ModelProvider(
    llm_call=lambda *_args, **_kwargs: goal_response,
    lvlm_call=lambda *_args, **_kwargs: goal_response,
)
goal = SlotContent(
    slot="goal", claims=[Claim("The customer pays and does unrelated work.", ["E1"])],
    scope_contract={"goal_statement": "stale"},
)
goal = semantic_grounding_verifier(CockburnSlot.GOAL, goal, cards, goal_provider)
assert goal.scope_contract["goal_statement"] == "The customer pays for won items."

# If a real source image is present, the semantic gate must use LVLM rather than
# silently trusting the card summary alone.
image_file = os.path.join("data", "images", "jtvauctions", "1.png")
if os.path.isfile(image_file):
    vision_calls = 0
    def vision_model(*_args, **_kwargs):
        global vision_calls
        vision_calls += 1
        return verifier_response
    vision_provider = ModelProvider(
        llm_call=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("LLM path used")),
        lvlm_call=vision_model,
    )
    vision_cards = [
        EvidenceCard("E1", "The user submits.", [1], [], source_doc="data/images/jtvauctions"),
        EvidenceCard("E2", "The system displays an error.", [1], [], source_doc="data/images/jtvauctions"),
    ]
    vision_slot = SlotContent(
        slot="exception_flow",
        claims=[Claim("The user submits.", ["E1"]), Claim("Error and retry.", ["E2"])],
    )
    semantic_grounding_verifier(
        CockburnSlot.EXCEPTION_FLOW, vision_slot, vision_cards, vision_provider,
    )
    assert vision_calls == 1

# Removing the last DRA claim must clear the audit population instead of
# leaving a stale pre-DRA assertion behind.
empty = SlotContent(
    slot="postconditions",
    pre_constraint_draft={"claims": [{"content": "stale", "evidence_refs": ["E1"]}]},
)
empty = semantic_grounding_verifier(CockburnSlot.POSTCONDITIONS, empty, cards, provider)
assert empty.pre_constraint_draft["claims"] == []

# CitCov must still see a verifier-authored invalid identifier before the final
# deterministic whitelist removes that candidate.
invalid_response = json.dumps({
    "claims": [{
        "index": 0, "decision": "rewrite", "content": "Candidate with bad ref.",
        "evidence_refs": ["NOT_EXPOSED"], "missing_aspects": [],
    }]
})
invalid_provider = ModelProvider(
    llm_call=lambda *_args, **_kwargs: invalid_response,
    lvlm_call=lambda *_args, **_kwargs: invalid_response,
)
invalid_slot = SlotContent(
    slot="postconditions", claims=[Claim("Original.", ["E1"])],
)
invalid_slot = semantic_grounding_verifier(
    CockburnSlot.POSTCONDITIONS, invalid_slot, cards, invalid_provider,
)
assert invalid_slot.claims == []
assert invalid_slot.pre_constraint_draft["claims"][0]["evidence_refs"] == ["NOT_EXPOSED"]


lock = threading.Lock()
calls = 0


def l2_model(_prompt):
    global calls
    with lock:
        calls += 1
    return "[]"


slots = {
    "goal": {"claims": [{"content": "Goal", "evidence_refs": ["E1"]}]},
    "primary_actor": {"claims": [{"content": "Actor", "evidence_refs": ["E1"]}]},
}
with ThreadPoolExecutor(max_workers=8) as pool:
    results = list(pool.map(
        lambda _i: L2Judge(model_fn=l2_model).judge("UC", "Actor", slots),
        range(16),
    ))
assert calls == 2, calls  # one vertical and one lateral call
assert all(not result["parse_failures"] for result in results)
print("SEMANTIC_GROUNDING_AND_L2_DEDUP_SMOKE_PASS")
