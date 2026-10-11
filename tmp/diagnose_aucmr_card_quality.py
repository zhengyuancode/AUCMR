import collections
import json
from pathlib import Path

audit = json.loads(Path("outputs/evaluation_audit_l1.json").read_text(encoding="utf-8"))
rows = audit["AUCMR (CREST full)"]
cards_by_uc = {}
for path in Path("outputs/aucmr_full").rglob("*-evidence.json"):
    raw = json.loads(path.read_text(encoding="utf-8"))
    cards_by_uc[(raw.get("actor"), raw.get("use_case_name"))] = {
        card["card_id"]: card for card in raw.get("evidence_cards", [])
    }

bad = []
card_counts = collections.Counter()
for row in rows:
    key = (row["actor"], row["use_case"])
    for claim in row["l1"]["claims"]:
        if claim.get("grounding_status") in {"neutral", "contradiction", "entailed_but_slot_misfit"}:
            bad.append((key, claim))
            card_counts.update(claim.get("evidence_refs", []))

print("BAD_AUCMR_CLAIMS", len(bad), "BY_STATUS", collections.Counter(c["grounding_status"] for _, c in bad))
print("TOP_REFERENCED_CARDS_IN_BAD_CLAIMS")
for card_id, count in card_counts.most_common(20):
    matches = []
    for key, cards in cards_by_uc.items():
        if card_id in cards:
            card = cards[card_id]
            matches.append({
                "uc": key,
                "card": card_id,
                "count": count,
                "pages": card.get("source_pages"),
                "cross": card.get("is_cross_page"),
                "region": card.get("region_type"),
                "content": card.get("content"),
                "relevance": card.get("slot_relevance"),
            })
    print(json.dumps(matches, ensure_ascii=False))

print("BAD_CLAIM_SAMPLES")
for key, claim in bad[:35]:
    print(json.dumps({
        "uc": key,
        "slot": claim.get("slot"),
        "status": claim.get("grounding_status"),
        "claim": claim.get("claim"),
        "refs": claim.get("evidence_refs"),
        "rationale": claim.get("rationale"),
        "slot_reason": claim.get("slot_fit_rationale"),
    }, ensure_ascii=False))
