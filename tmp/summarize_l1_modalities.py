import json
from collections import Counter
from pathlib import Path


audit = json.loads(Path("outputs/evaluation_audit_l1.json").read_text(encoding="utf-8"))
summary = {}
for method, rows in audit.items():
    counts = Counter()
    for row in rows:
        for claim in row["l1"]["claims"]:
            modalities = set((claim.get("evidence_modalities") or {}).values())
            if "image" in modalities:
                counts["claims_with_image"] += 1
            elif "text" in modalities:
                counts["text_only_claims"] += 1
            else:
                counts["no_valid_evidence"] += 1
    summary[method] = dict(counts)
print(json.dumps(summary, ensure_ascii=False, indent=2))
