import collections
import json
from pathlib import Path

AUDIT = Path("outputs/evaluation_audit_l1.json")
with AUDIT.open(encoding="utf-8") as handle:
    audit = json.load(handle)


def identity(row):
    return row.get("actor", ""), row.get("use_case", "")


full = {identity(row): row["l1"] for row in audit["AUCMR (CREST full)"]}
without = {identity(row): row["l1"] for row in audit["CREST w/o cross-slot"]}

print("PAIR_DELTAS n_ent(full-w/o), n_all(full-w/o)")
for key in sorted(full, key=lambda item: (full[item]["n_ent"] - without[item]["n_ent"], item)):
    f = full[key]
    w = without[key]
    print(
        f"{key[0]} | {key[1]} | "
        f"n_ent {f['n_ent']}-{w['n_ent']}={f['n_ent']-w['n_ent']:+d} | "
        f"n_all {f['n_all']}-{w['n_all']}={f['n_all']-w['n_all']:+d}"
    )

print("\nPAIR_FAILURE_STATUS_BY_UC")
for key in sorted(full):
    for label, data in (("full", full[key]), ("w/o", without[key])):
        counts = collections.Counter(
            claim.get("grounding_status", claim.get("entailment", "unknown"))
            for claim in data.get("claims", [])
        )
        print(key[1], label, dict(counts))

print("\nBASELINE_JUDGMENT_DISTRIBUTIONS")
for method in ("ReAct-Elaborate", "ViDoRAG-Elaborate", "VDocRAG-Elaborate", "TextRAG-Elaborate"):
    statuses = collections.Counter()
    entailment = collections.Counter()
    slot_fit = collections.Counter()
    modalities = collections.Counter()
    rationales = collections.Counter()
    for row in audit[method]:
        for claim in row["l1"].get("claims", []):
            statuses[claim.get("grounding_status", "missing")] += 1
            entailment[claim.get("entailment", "missing")] += 1
            slot_fit[str(claim.get("slot_fit", "missing"))] += 1
            modalities.update(claim.get("evidence_modalities", {}).values())
            rationales[claim.get("rationale", "")] += 1
    print(method)
    print(" statuses", dict(statuses))
    print(" entailment", dict(entailment), "slot_fit", dict(slot_fit))
    print(" modalities", dict(modalities))
    print(" common_rationales", rationales.most_common(3))

print("\nACTIVE_BASELINE_CARD_CONTENT")
for method in ("ReAct-Elaborate", "ViDoRAG-Elaborate", "VDocRAG-Elaborate", "TextRAG-Elaborate"):
    files = list(Path("outputs/baselines").rglob(f"*_{method}-evidence.json"))
    total = text = image_only = 0
    samples = []
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        for card in data.get("evidence_cards", []):
            total += 1
            content = str(card.get("content", "")).strip()
            if content:
                text += 1
                if len(samples) < 2:
                    samples.append((card.get("card_id"), content[:120]))
            elif card.get("image_paths") or card.get("source_pages"):
                image_only += 1
    print(method, {"files": len(files), "cards": total, "text_cards": text, "image_only_cards": image_only, "samples": samples})

print("\nNEGATIVE_PAIR_CLAIM_DIFFS")
for key in sorted(full, key=lambda item: (full[item]["n_ent"] - without[item]["n_ent"], item)):
    delta = full[key]["n_ent"] - without[key]["n_ent"]
    if delta >= 0:
        continue
    full_claims = {
        (row.get("slot"), row.get("claim")): row
        for row in full[key].get("claims", [])
    }
    without_claims = {
        (row.get("slot"), row.get("claim")): row
        for row in without[key].get("claims", [])
    }
    print(f"\n{key[0]} | {key[1]} | delta={delta:+d}")
    for label, rows in (
        ("FULL_ONLY", [full_claims[item] for item in full_claims.keys() - without_claims.keys()]),
        ("W/O_ONLY", [without_claims[item] for item in without_claims.keys() - full_claims.keys()]),
    ):
        for row in sorted(rows, key=lambda value: (value.get("slot", ""), value.get("claim", ""))):
            print(
                label,
                row.get("slot"),
                row.get("grounding_status", row.get("entailment")),
                repr(row.get("claim")),
                "|",
                row.get("rationale", "")[:180],
                "| slot:",
                row.get("slot_fit_rationale", "")[:140],
            )
