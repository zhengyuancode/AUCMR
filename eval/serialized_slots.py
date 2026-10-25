"""Recognize empty SlotContent serialization without changing source artifacts."""


def omitted_claims_are_empty(item: dict, slot: str, *, refusal: bool = False) -> bool:
    """SlotContent.to_dict omits empty claims; metadata is never final content.

    Markerless empty objects/raw_text were already accepted for explicit early
    refusals. Keep that representation only in the refusal path. Unknown fields
    must not silently turn unrecognized content into an empty slot.
    """
    if "claims" in item or item.get("raw_text") not in (None, ""):
        return False
    if "slot" not in item:
        return refusal and set(item) <= {"raw_text"}
    if item["slot"] != slot or not set(item) <= {
        "slot", "raw_text", "coverage_state", "scope_contract", "evidence_card_ids"
    }:
        return False
    if "scope_contract" in item and not isinstance(item["scope_contract"], dict):
        return False
    if "evidence_card_ids" in item:
        refs = item["evidence_card_ids"]
        if not isinstance(refs, list) or not all(isinstance(x, str) for x in refs):
            return False
    if "coverage_state" in item:
        state = item["coverage_state"]
        if not isinstance(state, dict) or set(state) != {"covered_aspect_ids", "missing_aspects"}:
            return False
        if any(not isinstance(v, list) or not all(isinstance(x, str) for x in v)
               for v in state.values()):
            return False
    return True
