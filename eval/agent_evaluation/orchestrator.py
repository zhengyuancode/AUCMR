"""Source-first L3 state machine. Semantic decisions only come from the transport."""
from __future__ import annotations

from eval.implementation import implementation_manifest

from copy import deepcopy
from pathlib import Path

from .agents.executor import Executor
from .agents.roles import materials, review_materials
from .aggregate import aggregate
from .budget import Budget
from .common import (VALID, Blocked, EvaluationError, append_jsonl, digest, ordered,
                     read_json, sample_strata, write_json)
from .references import freeze, require_version, verify_frozen
from .schemas import (check_behavior, check_content, check_locators, exact_ids,
                      record, validate_payload)
from .source_access import SourceAccess


class Orchestrator:
    def __init__(self, run_dir: Path, config: dict, manifest: dict, transport):
        self.root, self.config, self.manifest, self.transport = run_dir, config, manifest, transport
        if transport.mode == "mock":
            ledger = run_dir / "mock-budget.sqlite"
        else:
            from .common import REPO, confined
            ledger = confined(REPO, config["campaign_ledger"])
        self.budget = Budget(ledger, config["budgets"].get("campaign_hard_cap") or 500,
                             config["judging"]["max_concurrency"], config["judging"]["max_provider_concurrency"],
                             provider_managed=config.get("cost_control") == "PROVIDER_MANAGED")
        self.executor = Executor(run_dir, config, transport, self.budget)
        self.references = read_json(run_dir / "scenario_freeze.json") if (run_dir / "scenario_freeze.json").exists() else {}
        for reference in self.references.values():
            verify_frozen(reference)
        self.targets = {t["target_id"]: t for t in manifest["cohort"]["targets"]}
        self.sources = manifest["sources"]
        self.units = []

    def access(self, target, role, *, family="SOURCE", scenario=None, review=False, initial_pages=None):
        pages = None
        if family == "BEHAVIOR":
            pages = {(l["source_id"], l["pdf_page"]) for cp in scenario["checkpoints"] for l in cp["locators"]}
        search, reads, extra = (12, 24, 0)
        if role == "SCENE_BUILD_A":
            extra = 8
        elif role == "SCENE_VERIFY_B" or review:
            search, reads = 6, 12
        return SourceAccess(self.sources, target["project_id"], role, self.root / "access_log.jsonl",
            search_max=search, pages_max=reads, extra_crossref=extra,
            allowed_pages=pages, initial_pages=initial_pages)

    def source_stage(self, target: dict) -> dict:
        tid = target["target_id"]
        scope_id = tid + ".scope"
        task = materials("SOURCE", target, self.sources)
        task["assigned_scope_id"] = scope_id
        task["source_selection_rule"] = "EXPLICIT_DEFAULT_ELSE_EARLIEST_FULLY_SUPPORTED; BRANCH_EXPLICIT_OUTCOME_CONDITION"
        def validate_build(rows, access):
            if [r["kind"] for r in rows] != ["scope", "scene_proposal"]:
                raise EvaluationError("Build must return scope then scene_proposal")
            scope, proposal = rows[0]["payload"], rows[1]["payload"]
            # IDs assigned by the program, never based on generated semantic text.
            scope["scope_id"] = scope_id
            proposal["scope_id"] = scope_id
            for si, scene in enumerate(proposal["scenarios"]):
                scene["scenario_id"] = f"{tid}.scene.{si}"
                for ci, cp in enumerate(scene["checkpoints"]):
                    cp["checkpoint_id"] = f"{tid}.scene.{si}.checkpoint.{ci}"
                    check_locators(cp["locators"], self.sources, access)
            validate_payload("scope", scope)
            validate_payload("scene_proposal", proposal)
            check_locators(scope["locators"], self.sources, access)
            if len({s["kind"] for s in proposal["scenarios"]}) != len(proposal["scenarios"]):
                raise EvaluationError("Duplicate scenario kind")
        built = self.executor.execute(task_id=f"{tid}/source/build", role="SCENE_BUILD_A", view="SOURCE",
            kinds=["scope", "scene_proposal"], materials=task, source=self.access(target, "SCENE_BUILD_A"),
            validate=validate_build, target_id=tid)
        scope, proposal = (r["payload"] for r in built)
        objects = {scope_id: "SCOPE"}
        for scene in proposal["scenarios"]:
            objects[scene["scenario_id"]] = "SCENARIO"
            objects.update({c["checkpoint_id"]: "CHECKPOINT" for c in scene["checkpoints"]})
        source_pages = {(l["source_id"], l["pdf_page"]) for s in proposal["scenarios"] for c in s["checkpoints"] for l in c["locators"]}
        source_pages |= {(l["source_id"], l["pdf_page"]) for l in scope["locators"]}
        review_task = materials("SOURCE", target, self.sources, proposal={"scope": scope, "scene_proposal": proposal})
        review_task["requested_objects"] = objects
        review_task["proposal_record_id"] = built[1]["record_id"]
        def validate_reviews(rows, access):
            if any(r["kind"] != "source_review" for r in rows):
                raise EvaluationError("Expected source reviews")
            exact_ids(list(objects), [r["payload"]["object_id"] for r in rows])
            for r in rows:
                p = r["payload"]
                p["proposal_record_id"] = built[1]["record_id"]
                validate_payload("source_review", p)
                if p["object_kind"] != objects[p["object_id"]]:
                    raise EvaluationError("Wrong reviewed object kind")
                check_locators(p["locators"], self.sources, access)
                if p["decision"] == "ACCEPT" and not p["locators"]:
                    raise EvaluationError("Source acceptance needs original evidence")
            # B/C must actually revisit every proposed page, including images.
            required_locators = scope["locators"] + [l for s in proposal["scenarios"] for c in s["checkpoints"] for l in c["locators"]]
            check_locators(required_locators, self.sources, access)
        verified = self.executor.execute(task_id=f"{tid}/source/verify", role="SCENE_VERIFY_B", view="SOURCE",
            kinds=["source_review"], materials=review_task,
            source=self.access(target, "SCENE_VERIFY_B", initial_pages=source_pages), validate=validate_reviews, target_id=tid)
        final = verified
        if any(r["payload"]["decision"] != "ACCEPT" for r in verified):
            access = self.access(target, "REVIEW_C_BLIND", review=True, initial_pages=source_pages)
            blind_task = deepcopy(review_task)
            blind_task["original_role"] = "SCENE_VERIFY_B"
            blind = self.executor.execute(task_id=f"{tid}/source/c-blind", role="REVIEW_C_BLIND", view="C_BLIND",
                kinds=["source_review"], materials=blind_task, source=access, validate=validate_reviews, target_id=tid)
            final = self.executor.execute(task_id=f"{tid}/source/c-final", role="REVIEW_C_FINAL", view="C_FINAL",
                kinds=["source_review"], materials=review_materials(blind_task, blind=[r["payload"] for r in blind], first=[built, verified]),
                source=access, validate=validate_reviews, target_id=tid)
            self.review_records(tid, None, None, "SOURCE_REVIEW", verified, blind, final, built)
        approved = {r["payload"]["object_id"] for r in final if r["payload"]["decision"] == "ACCEPT"}
        frozen_proposal = deepcopy(proposal)
        frozen_proposal["scenarios"] = [s for s in proposal["scenarios"] if scope_id in approved and
            {s["scenario_id"], *(c["checkpoint_id"] for c in s["checkpoints"])} <= approved]
        if not any(s["kind"] == "CORE" for s in frozen_proposal["scenarios"]):
            frozen_proposal["core_status"] = "CORE_UNESTABLISHED"
        if not any(s["kind"] == "BRANCH" for s in frozen_proposal["scenarios"]) and proposal["branch_status"] == "PROPOSED":
            frozen_proposal["branch_status"] = "NOT_ESTABLISHED_WITHIN_SEARCH"
        frozen_proposal["unresolved"] += ["Unverified proposed object: " + obj for obj in objects if obj not in approved]
        frozen_scope = deepcopy(scope)
        frozen_scope["status"] = "REVIEWED" if scope_id in approved else "SCOPE_UNRESOLVED"
        allowed = {scope_id} | {s["scenario_id"] for s in frozen_proposal["scenarios"]} | {
            c["checkpoint_id"] for s in frozen_proposal["scenarios"] for c in s["checkpoints"]}
        reference = freeze(frozen_scope, frozen_proposal,
            [r["payload"] for r in final if r["payload"]["object_id"] in allowed], self.sources)
        reference["original_proposal_record_ids"] = [r["record_id"] for r in built]
        reference["review_record_ids"] = [r["record_id"] for r in final]
        for name, records in (("scope_cards", built[:1]), ("scene_candidates", built[1:]), ("source_reviews", verified)):
            for r in records:
                append_jsonl(self.root / f"{name}.jsonl", r)
        return reference

    def review_records(self, tid, aid, version, family, first, blind, final, other):
        item_key, label_key = ("object_id", "decision") if family == "SOURCE_REVIEW" else (
            ("claim_id", "label") if family == "CONTENT_CLAIM" else ("checkpoint_id", "label"))
        by_blind = {r["payload"][item_key]: r for r in blind}
        for r in final:
            p = r["payload"]
            item = p[item_key]
            sources = [x["record_id"] for x in first + other]
            payload = {"decision_family": family, "item_id": item,
                "blind_label": by_blind[item]["payload"][label_key], "final_label": p[label_key],
                "source_record_ids": sources, "rationale": p.get("rationale", "; ".join(p.get("reasons", []))), "locators": p["locators"]}
            append_jsonl(self.root / "reviews_C.jsonl", record("review", payload, run_id=self.root.name,
                target_id=tid, artifact_id=aid, role="REVIEW_C_FINAL", view="C_FINAL",
                input_hash=digest(sources), version=version))

    def validators(self, family, artifact, scenario, requested):
        def validate(rows, access):
            expected_kind = "content" if family == "CONTENT" else "behavior"
            key = "claim_id" if family == "CONTENT" else "checkpoint_id"
            if any(r["kind"] != expected_kind for r in rows):
                raise EvaluationError("Wrong record family")
            exact_ids(requested, [r["payload"][key] for r in rows])
            for r in rows:
                if family == "CONTENT":
                    check_content(r["payload"], artifact["claims"], self.sources, access)
                else:
                    check_behavior(r["payload"], artifact["claims"], scenario, self.sources, access)
                    check_locators([loc for cp in scenario["checkpoints"] for loc in cp["locators"]], self.sources, access)
        return validate

    def score_artifact(self, artifact):
        if artifact["state"] not in VALID or not artifact["claims"]:
            return
        tid, aid = artifact["target_id"], artifact["artifact_id"]
        target, reference = self.targets[tid], self.references[tid]
        version = reference["scenario_version"]
        ids = [c["claim_id"] for c in artifact["claims"]]
        batches = [("CONTENT", ids[i:i+6], None, f"batch-{i//6}") for i in range(0, len(ids), 6)]
        batches += [("BEHAVIOR", [c["checkpoint_id"] for c in s["checkpoints"]], s, s["scenario_id"]) for s in reference["scenarios"]]
        first = {}
        for judge in ("A", "B"):
            for family, requested, scenario, unit_id in batches:
                role = f"{family}_{judge}"
                task = materials(family, target, self.sources, artifact=artifact, reference=reference,
                                 requested_ids=requested, scenario=scenario)
                validator = self.validators(family, artifact, scenario, requested)
                try:
                    records = self.executor.execute(task_id=f"{aid}/{version}/{role}/{unit_id}", role=role, view=f"{judge}_FIRST",
                        kinds=[family.lower()], materials=task, source=self.access(target, role, family=family, scenario=scenario),
                        validate=validator, target_id=tid, artifact_id=aid, version=version)
                except Blocked:
                    raise
                except EvaluationError as exc:
                    append_jsonl(self.root / "technical_failures.jsonl", {"target_id": tid, "artifact_id": aid,
                        "role": role, "requested_ids": requested, "error": str(exc), "scenario_version": version})
                    records = []
                first[judge, family, unit_id] = records
                for r in records:
                    append_jsonl(self.root / f"{family.lower()}_first_{judge}.jsonl", r)
        for family, requested, scenario, unit_id in batches:
            # Content sampling unit is one assertion; behavior sampling unit is the entire scenario.
            groups = [[cid] for cid in requested] if family == "CONTENT" else [requested]
            key = "claim_id" if family == "CONTENT" else "checkpoint_id"
            for group in groups:
                a = [r for r in first["A", family, unit_id] if r["payload"][key] in group]
                b = [r for r in first["B", family, unit_id] if r["payload"][key] in group]
                def signature(rows):
                    return sorted((r["payload"][key], r["payload"]["label"],
                        tuple(sorted(r["payload"].get("reasons", []))) if r["payload"]["label"] == "DELETE" else ()) for r in rows)
                self.units.append({"stable_id": f"{aid}/{version}/{family}/" + (group[0] if family == "CONTENT" else unit_id),
                    "project_id": target["project_id"], "decision_family": "CONTENT_CLAIM" if family == "CONTENT" else "BEHAVIOR_SCENARIO",
                    "artifact": artifact, "family": family, "scenario": scenario, "requested": group, "A": a, "B": b,
                    "complete": len(a) == len(b) == len(group), "disagreement": signature(a) != signature(b)})

    def reconcile(self):
        eligible = [u for u in self.units if u["complete"]]
        agreement = sample_strata([u for u in eligible if not u["disagreement"]], "agreement-audit")
        stability = sample_strata(eligible, "stability")
        write_json(self.root / "sampling.json", {"agreement": agreement, "stability": stability})
        selected = {x for s in agreement["strata"] for x in s["selected"]} | {u["stable_id"] for u in eligible if u["disagreement"]}
        stable = {x for s in stability["strata"] for x in s["selected"]}
        resolved, first_a, first_b = {}, {}, {}
        for unit in self.units:
            a, family, scene = unit["artifact"], unit["family"], unit["scenario"]
            tid, aid = a["target_id"], a["artifact_id"]
            target, ref = self.targets[tid], self.references[tid]
            task = materials(family, target, self.sources, artifact=a, reference=ref,
                             requested_ids=unit["requested"], scenario=scene)
            validator = self.validators(family, a, scene, unit["requested"])
            records = unit["A"] if unit["complete"] and not unit["disagreement"] else []
            if unit["stable_id"] in selected:
                source = self.access(target, "REVIEW_C_BLIND", family=family, scenario=scene, review=True)
                blind_task = {**task, "original_role": f"{family}_A"}
                try:
                    blind = self.executor.execute(task_id=unit["stable_id"]+"/c-blind", role="REVIEW_C_BLIND", view="C_BLIND",
                        kinds=[family.lower()], materials=blind_task, source=source, validate=validator,
                        target_id=tid, artifact_id=aid, version=ref["scenario_version"])
                    records = self.executor.execute(task_id=unit["stable_id"]+"/c-final", role="REVIEW_C_FINAL", view="C_FINAL",
                        kinds=[family.lower()], materials=review_materials(blind_task, blind=[r["payload"] for r in blind], first=[unit["A"], unit["B"]]),
                        source=source, validate=validator, target_id=tid, artifact_id=aid, version=ref["scenario_version"])
                    self.review_records(tid, aid, ref["scenario_version"], "CONTENT_CLAIM" if family == "CONTENT" else "BEHAVIOR_CHECKPOINT",
                                        unit["A"], blind, records, unit["B"])
                except Blocked:
                    raise
                except EvaluationError:
                    records = []  # Failed C adjudication remains technical incomplete.
            for store, rows in ((resolved, records), (first_a, unit["A"]), (first_b, unit["B"])):
                store.setdefault(family, {}).setdefault(aid, {}).update({r["payload"]["claim_id" if family == "CONTENT" else "checkpoint_id"]: r["payload"] for r in rows})
            if unit["stable_id"] in stable:
                for judge in ("A", "B"):
                    role = "STABILITY_" + judge
                    try:
                        repeated = self.executor.execute(task_id=unit["stable_id"]+"/stability-"+judge, role=role, view="STABILITY",
                            kinds=[family.lower()], materials={**task, "original_role": family+"_"+judge},
                            source=self.access(target, role, family=family, scenario=scene), validate=validator,
                            target_id=tid, artifact_id=aid, version=ref["scenario_version"])
                        for r in repeated:
                            append_jsonl(self.root / "stability.jsonl", r)
                    except Blocked:
                        raise
                    except EvaluationError:
                        append_jsonl(self.root / "stability_failures.jsonl", {"stable_id": unit["stable_id"], "judge": judge})
        outputs = {}
        for view, store in (("A_FIRST", first_a), ("B_FIRST", first_b), ("RECONCILED", resolved)):
            outputs[view] = aggregate(self.manifest, store.get("CONTENT", {}), store.get("BEHAVIOR", {}), self.references, view)
            write_json(self.root / f"decisions_{view}.json", store)
            write_json(self.root / f"aggregate_{view}.json", outputs[view])
        return outputs

    def run(self, target_ids: list[str]):
        unknown = set(target_ids) - self.targets.keys()
        if unknown:
            raise EvaluationError("Unregistered targets")
        from .common import PACKAGE, file_hash
        implementation = implementation_manifest("l3")
        fingerprint = digest({"config": self.config, "manifest": self.manifest,
                              "prompt": file_hash(PACKAGE / "prompts/PROMPTS.md"),
                              "implementation": implementation})
        binding_path = self.root / "runtime_binding.json"
        if binding_path.exists() and read_json(binding_path)["hash"] != fingerprint:
            raise EvaluationError("Input/prompt/model configuration drift; start a new frozen run")
        write_json(binding_path, {"hash": fingerprint, "implementation": implementation})
        for tid in ordered(target_ids, "source-order"):
            if tid not in self.references:
                self.references[tid] = self.source_stage(self.targets[tid])
                write_json(self.root / "scenario_freeze.json", self.references)
        write_json(self.root / "source_status.json", {tid: {"core_status": self.references[tid]["core_status"],
            "scenario_version": self.references[tid]["scenario_version"]} if tid in self.references else {"status": "NOT_RUN"}
            for tid in self.targets})
        artifacts = [a for a in self.manifest["artifacts"] if a["target_id"] in target_ids]
        index = {a["artifact_id"]: a for a in artifacts}
        for aid in ordered(list(index), "artifact-presentation"):
            self.score_artifact(index[aid])
        result = self.reconcile()
        write_json(self.root / "stage_state.json", {"state": "RECONCILED", "target_ids": target_ids,
            "reference_versions": {k: v["scenario_version"] for k,v in self.references.items()},
            "runtime_hash": fingerprint,
            "semantic_status": "MOCK_ONLY_NOT_RESEARCH" if self.transport.mode == "mock" else "RECORDS_AVAILABLE_REQUIRES_REPORTABILITY_CHECK"})
        return result
