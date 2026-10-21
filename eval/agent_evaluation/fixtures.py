"""Constructed materials and scripted responses. Never judge real source content."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from .common import DESIGN, SLOTS, digest, file_hash, read_json, write_json


def render_materials(directory: Path) -> dict:
    import fitz
    from PIL import Image, ImageDraw
    specs = read_json(DESIGN / "fixtures.json")["fixtures"]
    directory.mkdir(parents=True, exist_ok=True)
    mapping = {}
    for spec in specs:
        path = directory / (spec["id"] + ".pdf")
        pdf = fitz.open()
        for text in spec["source_pages"]:
            page = pdf.new_page(width=612, height=792)
            page.insert_textbox(fitz.Rect(40, 40, 570, 400), text, fontsize=12)
        if spec.get("image_table"):
            table = spec["image_table"]
            image = Image.new("RGB", (1000, 240), "white")
            draw = ImageDraw.Draw(image)
            rows = [table["headers"], *table["rows"]]
            for ri, row in enumerate(rows):
                for ci, text in enumerate(row):
                    box = (ci*500, ri*80, (ci+1)*500-1, (ri+1)*80-1)
                    draw.rectangle(box, outline="black", width=2)
                    draw.text((box[0]+16, box[1]+24), text, fill="black", font_size=24)
            png = directory / (spec["id"] + "-table.png")
            image.save(png)
            pdf[0].insert_image(fitz.Rect(40, 180, 570, 307), filename=str(png))
        pdf.save(path)
        pdf.close()
        mapping[spec["id"]] = {"source_id": "FIX-" + spec["id"], "project_id": spec["id"],
            "pdf": str(path.resolve()), "sha256": file_hash(path), "pages": len(spec["source_pages"])}
    write_json(directory / "manifest.json", {"data_type": "FIXTURE / NOT_RESEARCH_DATA", "sources": mapping})
    return mapping


def fixture_manifest(directory: Path, ids=None, methods=None) -> tuple[dict, dict]:
    from .adapters import adapt
    mapping = render_materials(directory / "materials")
    specs = {s["id"]: s for s in read_json(DESIGN / "fixtures.json")["fixtures"]}
    ids = ids or list(specs)
    methods = methods or ["AUCMR", "CREST w/o cross-slot check"]
    targets, artifacts = [], []
    for fid in ids:
        spec = specs[fid]
        target = {"target_id": fid, "name": "Constructed fixture " + fid, "actor": "Fixture User",
                  "project_id": fid, "planned_group": "MAIN"}
        targets.append(target)
        for mi, method in enumerate(methods):
            data = {"use_case_name": target["name"], "actor": target["actor"], "slots": {slot: {"claims": []} for slot in SLOTS}}
            data["slots"]["main_flow"]["claims"] = [{"content": text} for text in spec["artifact"]]
            path = directory / "inputs" / f"{fid}-{mi}.json"
            write_json(path, data)
            artifacts.append(adapt(path, target, method))
    return {"data_type": "FIXTURE / NOT_RESEARCH_DATA", "sources": [mapping[fid] for fid in ids],
            "cohort": {"targets": targets, "methods": methods}, "artifacts": artifacts}, specs


class FixtureScript:
    """Preset transport only: no inference, keyword scoring, or real input permitted."""
    def __init__(self, specs, sources):
        self.specs = specs
        self.sources = {s["project_id"]: s for s in sources}

    def locator(self, fid, page=1):
        spec, source = self.specs[fid], self.sources[fid]
        visual = bool(spec.get("image_only_table"))
        return {"source_id": source["source_id"], "source_sha256": source["sha256"], "pdf_page": page,
                "bbox": None, "region_description": "Constructed table" if visual else "Constructed page",
                "quote": None if visual else spec["source_pages"][page-1], "modality": "TABLE" if visual else "TEXT"}

    def __call__(self, task, messages, attempt):
        package = task["materials"]
        fid = package["target"]["target_id"]
        if fid not in self.specs:
            raise ValueError("Mock only accepts registered constructed fixture IDs")
        spec = self.specs[fid]
        if len(messages) == 2:
            return {"tool_calls": [{"name": "source_page", "arguments": {"source_id": self.sources[fid]["source_id"],
                "pdf_page": p, "image": bool(spec.get("image_only_table"))}} for p in range(1, self.sources[fid]["pages"]+1)]}
        role = task["role"]
        locs = [self.locator(fid, p) for p in range(1, self.sources[fid]["pages"]+1)]
        if role == "SCENE_BUILD_A":
            cp = spec.get("checkpoint", {"statement": "Constructed success", "condition": None,
                        "action": "Constructed action", "outcome": "Constructed result"})
            checkpoint = {"checkpoint_id": "temporary", "statement": cp["statement"], "condition": cp["condition"],
                "actor": "Fixture User", "object": None, "action": cp["action"], "outcome": cp["outcome"],
                "relations": ["Fixture-only relation"], "locators": locs}
            scope = {"scope_id": "temporary", "purpose": "Constructed test only", "actors": ["Fixture User"],
                "objects": [], "start_boundary": None, "end_boundary": None, "in_scope": ["Fixture"],
                "out_of_scope": [], "aliases": [], "status": "DRAFT", "locators": locs, "unresolved": []}
            scene = {"scenario_id": "temporary", "kind": "CORE", "title": "Constructed core",
                "selection_reason": "Preset constructed default; no real-source semantics", "condition": cp["condition"],
                "checkpoints": [checkpoint], "excluded_candidates": [], "cross_page": len(locs)>1,
                "visual_required": bool(spec.get("image_only_table")), "status": "DRAFT"}
            proposal = {"scope_id": "temporary", "core_status": "PROPOSED", "branch_status": "NOT_ESTABLISHED_WITHIN_SEARCH",
                "scenarios": [scene], "source_search_log_ids": [], "unresolved": []}
            return {"records": [{"kind": "scope", "payload": scope}, {"kind": "scene_proposal", "payload": proposal}]}
        if "requested_objects" in package:
            return {"records": [{"kind": "source_review", "payload": {"proposal_record_id": package["proposal_record_id"],
                "object_id": oid, "object_kind": kind, "decision": "ACCEPT", "reasons": ["Scripted fixture verification"],
                "locators": locs, "proposed_change": None}} for oid, kind in package["requested_objects"].items()]}
        artifact = package["artifact"]
        claims = artifact["claims"]
        rows = []
        for requested in package["requested_ids"]:
            if "scope_card" in package:
                index = next(i for i, c in enumerate(claims) if c["claim_id"] == requested)
                expected = spec["expected"] if fid.startswith("C") and index == spec["judge_index"] else "KEEP"
                reason = spec.get("expected_reason", "NONE") if expected != "KEEP" else "NONE"
                # One deliberately scripted disagreement exercises blind/final adjudication.
                if fid == "C01" and role == "CONTENT_B":
                    expected, reason = "UNRESOLVED", "PARTIAL_SUPPORT"
                p = {"claim_id": requested, "label": expected, "reasons": [reason],
                     "rationale": "Preset fixture response; not semantic model validation", "locators": locs,
                     "context_claim_ids": [], "duplicate_of": claims[spec["duplicate_of_index"]]["claim_id"] if reason == "REDUNDANT" else None,
                     "search_log_ids": []}
                rows.append({"kind": "content", "payload": p})
            else:
                expected = spec["expected"] if fid.startswith("B") else "RETAINED"
                p = {"scenario_id": package["scenario"]["scenario_id"], "checkpoint_id": requested,
                    "label": expected, "matched_claim_ids": [c["claim_id"] for c in claims] if expected != "MISSING" else [],
                    "rationale": "Preset fixture response; not semantic model validation",
                    "missing_elements": ["Fixture required outcome"] if expected == "PARTIAL" else [],
                    "distorted_relations": ["Fixture wrong condition/action relation"] if expected == "DISTORTED" else [],
                    "alternative_legal_variant": None, "locators": locs, "deterministic_empty": False}
                rows.append({"kind": "behavior", "payload": p})
        return {"records": rows}


def execute_fixtures(run_dir: Path, config: dict) -> dict:
    from .orchestrator import Orchestrator
    from .transport import MockTransport, deny_network
    from .export import export_tables
    root = run_dir / "fixtures" / "mock"
    # Frozen files are reused on resume; PDFs must not get new serialization hashes.
    if (root / "fixture_manifest.json").exists():
        manifest = read_json(root / "fixture_manifest.json")
        specs = {s["id"]: s for s in read_json(DESIGN / "fixtures.json")["fixtures"]}
    else:
        manifest, specs = fixture_manifest(root)
        write_json(root / "fixture_manifest.json", manifest)
    transport = MockTransport(FixtureScript(specs, manifest["sources"]))
    with deny_network():
        runner = Orchestrator(root, config, manifest, transport)
        result = runner.run([t["target_id"] for t in manifest["cohort"]["targets"]])
    reconciled = read_json(root / "decisions_RECONCILED.json")
    checks = []
    for a in manifest["artifacts"]:
        fid, spec = a["target_id"], specs[a["target_id"]]
        if fid.startswith("C"):
            cid = a["claims"][spec["judge_index"]]["claim_id"]
            label = reconciled.get("CONTENT", {}).get(a["artifact_id"], {}).get(cid, {}).get("label")
        else:
            decisions = reconciled.get("BEHAVIOR", {}).get(a["artifact_id"], {})
            label = next(iter(decisions.values()), {}).get("label")
        checks.append({"fixture": fid, "artifact_id": a["artifact_id"], "expected": spec["expected"],
                       "observed": label, "pass": label == spec["expected"]})
    provenance = {"protocol_version": config["protocol_version"], "run_id": run_dir.name,
        "input_manifest_hash": digest(manifest), "cohort_hash": digest(manifest["cohort"]),
        "source_hashes": {s["source_id"]: s["sha256"] for s in manifest["sources"]},
        "output_hashes": {a["artifact_id"]: a["sha256"] for a in manifest["artifacts"]},
        "prompt_hash": file_hash(Path(__file__).parent / "prompts/PROMPTS.md"), "model_binding_hash": digest(config["models"]),
        "reference_versions": {k: v["scenario_version"] for k, v in runner.references.items()},
        "report_scope": "FIXTURE", "semantic_status": "NOT_RUN", "transport": "mock"}
    export_tables(root / "tables", result["RECONCILED"], provenance, fixture=True)
    report = {"data_type": "FIXTURE / NOT_RESEARCH_DATA", "checks": checks,
        "passed": sum(c["pass"] for c in checks), "failed": sum(not c["pass"] for c in checks),
        "mock_requests_this_invocation": len(transport.calls), "real_model_calls": 0, "model_cost_cny": 0,
        "model_fixture_test": "NOT_RUN", "scientific_results": "NOT_ESTABLISHED"}
    write_json(root / "fixture_report.json", report)
    return report
