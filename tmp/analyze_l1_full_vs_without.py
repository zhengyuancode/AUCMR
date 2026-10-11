import json
from collections import Counter
from pathlib import Path


audit = json.loads(Path("outputs/evaluation_audit_l1.json").read_text(encoding="utf-8"))
full_name = "AUCMR (CREST full)"
without_name = "CREST w/o cross-slot"


def rows_by_case(method):
    return {
        (row["use_case"], row["actor"]): row["l1"]["claims"]
        for row in audit[method]
    }


full = rows_by_case(full_name)
without = rows_by_case(without_name)
payload = {"per_case": [], "same_claim_label_changes": [], "unmatched": []}

for identity in sorted(full):
    full_claims = full[identity]
    without_claims = without[identity]
    full_labels = Counter(item.get("entailment") for item in full_claims)
    without_labels = Counter(item.get("entailment") for item in without_claims)
    payload["per_case"].append({
        "use_case": identity[0],
        "full": dict(full_labels),
        "without": dict(without_labels),
        "entailed_delta": full_labels["entailment"] - without_labels["entailment"],
        "claim_count_delta": len(full_claims) - len(without_claims),
    })

    def keyed(claims):
        result = {}
        for index, item in enumerate(claims):
            key = (item.get("slot"), item.get("claim"))
            result.setdefault(key, []).append((index, item))
        return result

    full_keyed = keyed(full_claims)
    without_keyed = keyed(without_claims)
    for key in sorted(set(full_keyed) & set(without_keyed), key=str):
        for (_fi, fc), (_wi, wc) in zip(full_keyed[key], without_keyed[key]):
            if fc.get("entailment") != wc.get("entailment"):
                payload["same_claim_label_changes"].append({
                    "use_case": identity[0],
                    "slot": key[0],
                    "claim": key[1],
                    "full": fc,
                    "without": wc,
                })
    full_only = []
    without_only = []
    for key, values in full_keyed.items():
        surplus = len(values) - len(without_keyed.get(key, []))
        if surplus > 0:
            full_only.extend(item for _, item in values[-surplus:])
    for key, values in without_keyed.items():
        surplus = len(values) - len(full_keyed.get(key, []))
        if surplus > 0:
            without_only.extend(item for _, item in values[-surplus:])
    if full_only or without_only:
        payload["unmatched"].append({
            "use_case": identity[0],
            "full_only": full_only,
            "without_only": without_only,
        })

print(json.dumps(payload, ensure_ascii=False, indent=2))
