import json
from pathlib import Path

expected = {
    "jtvauctions": "data/images/jtvauctions",
    "milstd6016b_sec4": "data/images/milstd6016b_sec4",
}
issues = []
summary = {}
for method_dir in ("aucmr_full", "crest_without_dra"):
    for project, expected_source in expected.items():
        cards = 0
        sources = set()
        for path in (Path("outputs") / method_dir / project).glob("*-evidence.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            for card in data.get("evidence_cards", []):
                cards += 1
                source = str(card.get("source_doc", "")).replace("\\", "/")
                sources.add(source)
                if source != expected_source:
                    issues.append((method_dir, project, path.name, card.get("card_id"), source))
        summary[(method_dir, project)] = {"cards": cards, "source_docs": sorted(sources)}

print("EVIDENCE_BINDING_SUMMARY", summary)
assert not issues, issues[:20]
print("EVIDENCE_PROJECT_BINDING_PASS")
