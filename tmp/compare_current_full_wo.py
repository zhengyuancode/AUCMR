import json

audit = json.load(open("outputs/evaluation_audit_l1.json", encoding="utf-8"))
full = {(r["actor"], r["use_case"]): r["l1"] for r in audit["AUCMR (CREST full)"]}
wo = {(r["actor"], r["use_case"]): r["l1"] for r in audit["CREST w/o cross-slot"]}
for key in sorted(full, key=lambda k: full[k]["n_all"] - wo[k]["n_all"]):
    f, w = full[key], wo[key]
    print(key[0], "|", key[1], "| n_all", f["n_all"], w["n_all"], "delta", f["n_all"] - w["n_all"], "| n_ent", f["n_ent"], w["n_ent"], "delta", f["n_ent"] - w["n_ent"])
