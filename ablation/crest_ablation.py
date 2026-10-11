"""Isolated CREST component-ablation runner.

This entry point deliberately uses ``ablation_runtime`` and
``outputs_ablation``.  It never imports or writes the production ``crest``
package/output tree, and refuses to run while a normal AUCMR generation or
another ablation process is active.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import copy
import dataclasses
import datetime as dt
import hashlib
import json
import os
import random
import re
import signal
import statistics
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run import (
    CREST_GENERATION_VERSION,
    L1_EVALUATION_VERSION,
    L2_EVALUATION_VERSION,
)
from ablation_runtime.schemas import COCKBURN_DEPENDENCY_ORDER

DEFAULT_CONTROL = Path(__file__).with_name("experiment.json")
ABLATION_GENERATION_VERSION = (
    f"{CREST_GENERATION_VERSION}-crest-components-v4"
)
COCKBURN_ORDER = [slot.value for slot in COCKBURN_DEPENDENCY_ORDER]


@dataclass(frozen=True)
class Variant:
    key: str
    method: str
    directory: str
    seed: int | None = None
    slot_order: tuple[str, ...] | None = None
    reuse_prior_evidence: bool = True
    single_page_only: bool = False
    enforce_evidence_refs: bool = True
    dra_enabled: bool = True

    def metadata(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class ProjectRuntime:
    project: str
    file_names: tuple[str, ...]
    topk: int
    total_pages: int
    retrieval_fn: Any


def _read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def load_control(path: Path) -> tuple[dict, Path, Path]:
    path = path.expanduser().resolve()
    control = _read_json(path)
    required = control.get("required_generation_version")
    if required and required != CREST_GENERATION_VERSION:
        raise RuntimeError(
            f"Ablation config expects {required!r}, but current run.py provides "
            f"{CREST_GENERATION_VERSION!r}; rebuild the isolated runtime first"
        )
    source_hashes = control.get("upstream_source_sha256", {})
    for relative, expected in source_hashes.items():
        source = (ROOT / str(relative)).resolve()
        try:
            source.relative_to(ROOT)
        except ValueError as exc:
            raise ValueError(f"Unsafe upstream source path: {relative!r}") from exc
        if not source.is_file():
            raise FileNotFoundError(f"Pinned upstream source is missing: {source}")
        actual = hashlib.sha256(source.read_bytes()).hexdigest()
        if actual.lower() != str(expected).lower():
            raise RuntimeError(
                f"Current AUCMR source changed ({relative}: {actual}); rebuild the "
                "isolated ablation runtime and update its source pin before running"
            )
    base = (path.parent / str(control["base_config"])).resolve()
    output = (path.parent / str(control["output_root"])).resolve()
    return control, base, output


def build_variants(control: dict, selected: Iterable[str] | None = None,
                   random_seeds: Iterable[int] | None = None) -> list[Variant]:
    enabled = set(selected or control.get("variants", []))
    variants: list[Variant] = []
    if "full" in enabled:
        variants.append(Variant(
            key="full", method="AUCMR (CREST full)", directory="full",
        ))
    if "without_dra" in enabled:
        variants.append(Variant(
            key="without_dra", method="CREST w/o cross-slot",
            directory="without_dra", dra_enabled=False,
        ))
    if "random_order" in enabled:
        seen: set[tuple[str, ...]] = set()
        seeds = list(random_seeds) if random_seeds is not None else control.get("random_order_seeds", [])
        for seed in seeds:
            order = list(COCKBURN_ORDER)
            random.Random(int(seed)).shuffle(order)
            order_tuple = tuple(order)
            if order_tuple in seen:
                raise ValueError(f"Duplicate random slot permutation for seed {seed}")
            seen.add(order_tuple)
            variants.append(Variant(
                key="random_order",
                method=f"CREST - Cockburn order [seed={int(seed)}]",
                directory=f"random_order/seed_{int(seed):03d}",
                seed=int(seed), slot_order=order_tuple,
            ))
    if "without_cross_slot_reuse" in enabled:
        variants.append(Variant(
            key="without_cross_slot_reuse",
            method="CREST - cross-slot evidence reuse",
            directory="without_cross_slot_reuse", reuse_prior_evidence=False,
        ))
    if "single_page" in enabled:
        variants.append(Variant(
            key="single_page", method="CREST - complete-region reading",
            directory="single_page", single_page_only=True,
        ))
    if "without_evidence_constraint" in enabled:
        variants.append(Variant(
            key="without_evidence_constraint",
            method="CREST - mandatory evidence constraint",
            directory="without_evidence_constraint", enforce_evidence_refs=False,
        ))
    if not variants:
        raise ValueError("No ablation variants selected")
    return variants


def _safe_stem(actor: str, use_case: str) -> str:
    return re.sub(r"[^\w\-.]+", "", f"{actor}_{use_case}".replace(" ", "_")) or "unnamed"


def _paths(output_root: Path, variant: Variant, item: dict) -> tuple[Path, Path]:
    directory = output_root / variant.directory / str(item["project"])
    stem = _safe_stem(str(item["actor"]), str(item["use_case"]))
    return directory / f"{stem}.json", directory / f"{stem}-evidence.json"


def _production_paths(production_root: Path, variant: Variant,
                      item: dict) -> tuple[Path, Path]:
    if variant.key == "full":
        directory = production_root / "aucmr_full" / str(item["project"])
    elif variant.key == "without_dra":
        directory = production_root / "crest_without_dra" / str(item["project"])
    else:
        raise ValueError(f"Production reuse is unsupported for variant: {variant.key}")
    stem = _safe_stem(str(item["actor"]), str(item["use_case"]))
    return directory / f"{stem}.json", directory / f"{stem}-evidence.json"


def _pair_complete(output_root: Path, variant: Variant, item: dict) -> bool:
    main_path, evidence_path = _paths(output_root, variant, item)
    if not main_path.is_file() or not evidence_path.is_file():
        return False
    try:
        main = _read_json(main_path)
        evidence = _read_json(evidence_path)
        return all(
            data.get("method") == variant.method
            and data.get("actor") == str(item["actor"])
            and data.get("use_case_name") == str(item["use_case"])
            and data.get("generation_version") == CREST_GENERATION_VERSION
            and data.get("ablation_generation_version") == ABLATION_GENERATION_VERSION
            and data.get("ablation", {}).get("key") == variant.key
            and data.get("ablation", {}).get("seed") == variant.seed
            for data in (main, evidence)
        ) and bool(main.get("artifact_revision_id")) and (
            main.get("artifact_revision_id") == evidence.get("artifact_revision_id")
        )
    except Exception:
        return False


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    os.replace(temporary, path)


def _atomic_result_pair(main_path: Path, main: dict,
                        evidence_path: Path, evidence: dict) -> None:
    """Commit evidence first and main last, with a shared revision marker."""
    main_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    revision_id = uuid.uuid4().hex
    main["artifact_revision_id"] = revision_id
    evidence["artifact_revision_id"] = revision_id
    staged: list[tuple[Path, Path]] = []
    for path, payload in ((main_path, main), (evidence_path, evidence)):
        temporary = path.with_name(
            f".{path.name}.tmp-{os.getpid()}-{threading.get_ident()}"
        )
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        staged.append((temporary, path))
    os.replace(staged[1][0], staged[1][1])
    os.replace(staged[0][0], staged[0][1])


def _production_core_min_mtime(control: dict) -> float | None:
    raw = control.get("production_core_min_mtime")
    if not raw:
        return None
    parsed = dt.datetime.fromisoformat(str(raw))
    if parsed.tzinfo is None:
        raise ValueError("production_core_min_mtime must include a timezone offset")
    return parsed.timestamp()


def _validated_production_pair(production_root: Path, variant: Variant,
                               item: dict, min_mtime: float | None = None) -> tuple[dict, dict]:
    main_path, evidence_path = _production_paths(production_root, variant, item)
    if not main_path.is_file() or not evidence_path.is_file():
        raise FileNotFoundError(
            f"Reusable production pair is incomplete: {main_path} / {evidence_path}"
        )
    if min_mtime is not None:
        stale = [
            str(path) for path in (main_path, evidence_path)
            if path.stat().st_mtime < min_mtime
        ]
        if stale:
            raise ValueError(
                "Production pair predates the latest-AUCMR run boundary: "
                + ", ".join(stale)
            )
    main = _read_json(main_path)
    evidence = _read_json(evidence_path)
    if any(
        data.get("generation_version") != CREST_GENERATION_VERSION
        for data in (main, evidence)
    ):
        raise ValueError(
            f"Production pair is not current generation {CREST_GENERATION_VERSION}: "
            f"{main_path} / {evidence_path}"
        )
    if not main.get("artifact_revision_id") or (
        main.get("artifact_revision_id") != evidence.get("artifact_revision_id")
    ):
        raise ValueError(
            f"Production main/evidence revision mismatch: {main_path} / {evidence_path}"
        )
    expected = {
        "method": variant.method,
        "actor": str(item["actor"]),
        "use_case_name": str(item["use_case"]),
    }
    for field, value in expected.items():
        if main.get(field) != value:
            raise ValueError(
                f"Production main file mismatch ({field}): {main_path}; "
                f"expected={value!r}, actual={main.get(field)!r}"
            )
        if evidence.get(field) != value:
            raise ValueError(
                f"Production evidence file mismatch ({field}): {evidence_path}; "
                f"expected={value!r}, actual={evidence.get(field)!r}"
            )
    return main, evidence


def _reuse_production_pair(production_root: Path, output_root: Path,
                           variant: Variant, item: dict, resume: bool,
                           min_mtime: float | None = None) -> None:
    target_main, target_evidence = _paths(output_root, variant, item)
    source_main, source_evidence = _production_paths(production_root, variant, item)
    main, evidence = _validated_production_pair(
        production_root, variant, item, min_mtime=min_mtime,
    )
    provenance = {
        "kind": "validated_production_artifact",
        "source_directory": (
            "outputs/aucmr_full" if variant.key == "full"
            else "outputs/crest_without_dra"
        ),
        "source_mtime_ns": {
            "main": source_main.stat().st_mtime_ns,
            "evidence": source_evidence.stat().st_mtime_ns,
        },
    }
    if resume and _pair_complete(output_root, variant, item):
        existing_main = _read_json(target_main)
        existing_evidence = _read_json(target_evidence)
        main_source = existing_main.get("ablation_source", {})
        evidence_source = existing_evidence.get("ablation_source", {})
        if main_source == provenance and evidence_source == provenance:
            print(f"SKIP reused: {variant.method} / {item['actor']} / {item['use_case']}")
            return
    main = copy.deepcopy(main)
    evidence = copy.deepcopy(evidence)
    main["ablation"] = variant.metadata()
    main["ablation_source"] = provenance
    main["ablation_generation_version"] = ABLATION_GENERATION_VERSION
    evidence["ablation"] = variant.metadata()
    evidence["ablation_source"] = provenance
    evidence["ablation_generation_version"] = ABLATION_GENERATION_VERSION
    _atomic_result_pair(target_main, main, target_evidence, evidence)
    print(f"REUSED {target_main}")


def _write_result(uc: Any, variant: Variant, output_root: Path, project: str) -> None:
    item = {"project": project, "actor": uc.actor, "use_case": uc.use_case_name}
    main_path, evidence_path = _paths(output_root, variant, item)
    main = uc.to_dict()
    main["method"] = variant.method
    main["generation_version"] = CREST_GENERATION_VERSION
    main["ablation_generation_version"] = ABLATION_GENERATION_VERSION
    main["ablation"] = variant.metadata()
    referenced = {
        ref
        for slot in uc.slots.values()
        for claim in slot.claims
        for ref in claim.evidence_refs
    }
    cards = {card.card_id: card for card in uc.all_evidence_cards}
    evidence = {
        "use_case_name": uc.use_case_name,
        "actor": uc.actor,
        "method": variant.method,
        "generation_version": CREST_GENERATION_VERSION,
        "ablation_generation_version": ABLATION_GENERATION_VERSION,
        "ablation": variant.metadata(),
        "evidence_summary": uc.evidence_summary,
        "referenced_card_ids": sorted(referenced),
        "evidence_cards": [card.to_dict() for card in cards.values()],
    }
    _atomic_result_pair(main_path, main, evidence_path, evidence)
    print(main_path)


def _running_experiments() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (blocking, non-blocking) AUCMR experiment processes.

    Baseline generation writes to the production output tree but not to the
    isolated ablation tree or CREST state, so it may run concurrently.  CREST
    generation/revision and another ablation generation remain blockers.
    """
    conflicts: list[dict[str, Any]] = []
    concurrent: list[dict[str, Any]] = []
    proc = Path("/proc")
    if not proc.is_dir():
        return conflicts, concurrent
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                "utf-8", errors="replace"
            )
        except (OSError, PermissionError):
            continue
        normalized = " ".join(cmdline.split())
        process = {"pid": int(entry.name), "cmdline": normalized}
        is_ablation = (
            "ablation/crest_ablation.py generate" in normalized
            or "ablation/crest_ablation.py all" in normalized
        )
        is_revision = "run.py revise" in normalized
        is_generation = "run.py generate" in normalized
        selects_crest = (
            "AUCMR (CREST full)" in normalized
            or "CREST w/o cross-slot" in normalized
        )
        # A generate command without an explicit method selection may include
        # CREST, so treat it conservatively as blocking.
        implicit_all_methods = is_generation and "--methods" not in normalized
        if is_ablation or is_revision or (is_generation and (selects_crest or implicit_all_methods)):
            conflicts.append(process)
        elif is_generation:
            concurrent.append(process)
    return conflicts, concurrent


def _active_conflicts() -> list[dict[str, Any]]:
    return _running_experiments()[0]


def _assert_idle(*, allow_parallel_ablation: bool = False,
                 allow_production_core: bool = False) -> None:
    conflicts = _active_conflicts()
    if allow_parallel_ablation:
        conflicts = [
            item for item in conflicts
            if "ablation/crest_ablation.py" not in item.get("cmdline", "")
        ]
    if allow_production_core:
        conflicts = [
            item for item in conflicts
            if not (
                "run.py generate" in item.get("cmdline", "")
                and "AUCMR (CREST full)" in item.get("cmdline", "")
                and "CREST w/o cross-slot" in item.get("cmdline", "")
            )
        ]
    if conflicts:
        raise RuntimeError(
            "Refusing to start an ablation while another AUCMR generation/revision is active: "
            + json.dumps(conflicts, ensure_ascii=False)
        )


@contextmanager
def _exclusive_lock(output_root: Path, lock_name: str = ".ablation.lock"):
    output_root.mkdir(parents=True, exist_ok=True)
    if not re.fullmatch(r"[.A-Za-z0-9_-]+", lock_name):
        raise ValueError(f"Unsafe lock name: {lock_name!r}")
    lock = output_root / lock_name
    descriptor: int | None = None
    for _attempt in range(2):
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError as exc:
            owner_text = (
                lock.read_text(encoding="utf-8", errors="replace")
                if lock.exists() else ""
            )
            try:
                owner_data = json.loads(owner_text)
                owner_pid = int(owner_data["pid"])
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as parse_exc:
                # A simultaneously starting process may have created the file
                # but not yet written its owner record.  Never reclaim an
                # unparseable lock automatically.
                raise RuntimeError(
                    f"Ablation output has an unreadable lock: {lock} ({owner_text or 'empty'})"
                ) from parse_exc

            owner_proc = Path("/proc") / str(owner_pid)
            owner_active = False
            if owner_proc.is_dir():
                try:
                    owner_cmdline = owner_proc.joinpath("cmdline").read_bytes().replace(
                        b"\0", b" "
                    ).decode("utf-8", errors="replace")
                    owner_active = "ablation/crest_ablation.py" in owner_cmdline
                except (OSError, PermissionError):
                    # If the PID exists but cannot be inspected, preserve the
                    # lock rather than risk two writers.
                    owner_active = True
            if owner_active:
                raise RuntimeError(
                    f"Ablation output is locked by active PID {owner_pid}: {lock} ({owner_text})"
                ) from exc

            # The recorded PID is dead or has been reused by an unrelated
            # process.  Remove only this exact lock file, then retry O_EXCL.
            try:
                lock.unlink()
                print(f"RECOVERED stale ablation lock from PID {owner_pid}: {lock}")
            except FileNotFoundError:
                pass
    if descriptor is None:
        raise RuntimeError(f"Could not acquire ablation output lock: {lock}")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"pid": os.getpid(), "started_at": dt.datetime.now().isoformat()}, handle)
        yield
    finally:
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


def _orchestrator(config: dict, registry: Any, provider: Any, retrieval_fn: Any,
                  variant: Variant, dra_rounds: int | None = None):
    from ablation_runtime import CRESTOrchestrator

    algorithm = config.get("algorithm", {})
    unconstrained = not variant.enforce_evidence_refs
    return CRESTOrchestrator(
        retrieval_fn=retrieval_fn,
        file_names=registry.file_names,
        topk=registry.topk,
        provider=provider,
        total_doc_pages=registry.total_pages,
        max_slot_rounds=int(algorithm.get("max_slot_rounds", 3)),
        goal_min_evidence_cards=(0 if unconstrained else int(algorithm.get("goal_min_evidence_cards", 1))),
        goal_min_confidence=(0.0 if unconstrained else float(algorithm.get("goal_min_confidence", 0.3))),
        overlap_rate_max=float(algorithm.get("overlap_rate_max", 0.65)),
        query_retry_max=int(algorithm.get("query_retry_max", 2)),
        answer_convergence_repeat=int(algorithm.get("answer_convergence_repeat", 1)),
        max_pages_ratio=float(algorithm.get("page_count_ratio", 0.3)),
        min_novel_pages_per_round=int(algorithm.get("min_novel_pages_per_round", 2)),
        dra_max_rounds=(
            int(algorithm.get("dra_max_rounds", 2)) if dra_rounds is None else int(dra_rounds)
        ),
        goal_cohesion_max_span_ratio=float(algorithm.get("goal_cohesion_max_span_ratio", 0.4)),
        slot_order=list(variant.slot_order) if variant.slot_order else None,
        reuse_prior_evidence=variant.reuse_prior_evidence,
        single_page_only=variant.single_page_only,
        enforce_evidence_refs=variant.enforce_evidence_refs,
    )


@contextmanager
def _runtime_switches(variant: Variant):
    from ablation_runtime import agents

    token = agents.ABLATION_SINGLE_PAGE_ONLY.set(variant.single_page_only)
    try:
        yield
    finally:
        agents.ABLATION_SINGLE_PAGE_ONLY.reset(token)


def _is_unresolvable(uc: Any) -> bool:
    return any(flag.flag_type == "unresolvable_use_case" for flag in uc.quality_flags)


def _apply_dra(config: dict, uc: Any, *, item: dict,
               runtime: ProjectRuntime, provider: Any, orchestrator: Any) -> Any:
    """Apply the current production DRA and account for its runtime statistics."""
    if _is_unresolvable(uc):
        orchestrator._build_evidence_summary(uc)
        return uc
    from ablation_runtime.agents import cross_slot_revision

    started = time.perf_counter()
    stats_before = provider.stats_snapshot()
    revised = cross_slot_revision(
        actor=item["actor"], use_case_name=item["use_case"], uc=uc,
        file_names=list(runtime.file_names), topk=runtime.topk,
        retrieval_fn=runtime.retrieval_fn, provider=provider,
        max_rounds=int(config.get("algorithm", {}).get("dra_max_rounds", 2)),
    )
    dra_stats = provider.stats_delta(stats_before, provider.stats_snapshot())
    base_runtime = dict(revised.runtime_stats or {})
    base_runtime["dra_elapsed_seconds"] = round(time.perf_counter() - started, 3)
    base_runtime["elapsed_seconds"] = round(
        float(base_runtime.get("elapsed_seconds", 0.0))
        + base_runtime["dra_elapsed_seconds"], 3,
    )
    for key, value in dra_stats.items():
        base_runtime[f"dra_{key}"] = value
        if key in {"llm_calls", "lvlm_calls", "llm_seconds", "lvlm_seconds"}:
            base_runtime[key] = round(
                float(base_runtime.get(key, 0.0)) + float(value), 3,
            )
    revised.runtime_stats = base_runtime
    orchestrator._build_evidence_summary(revised)
    return revised


def _generate_core_pair(config: dict, output_root: Path, item: dict,
                        full: Variant, without: Variant, registry: Any,
                        resume: bool) -> None:
    if resume and _pair_complete(output_root, full, item) and _pair_complete(output_root, without, item):
        print(f"SKIP complete pair: {item['actor']} / {item['use_case']}")
        return
    from ablation_runtime.agents import cross_slot_revision

    registry.set_collection(item["project"], item.get("file_names"))
    provider = registry.get_model_provider(think=False)
    retrieval_fn = registry.get_retrieval_adapter()
    with _runtime_switches(without):
        orchestrator = _orchestrator(config, registry, provider, retrieval_fn, without, dra_rounds=0)
        base = orchestrator.elaborate(item["actor"], item["use_case"])
        _write_result(base, without, output_root, item["project"])
        revised = copy.deepcopy(base)
        if not _is_unresolvable(revised):
            started = time.perf_counter()
            revised = cross_slot_revision(
                actor=item["actor"], use_case_name=item["use_case"], uc=revised,
                file_names=registry.file_names, topk=registry.topk,
                retrieval_fn=retrieval_fn, provider=provider,
                max_rounds=int(config.get("algorithm", {}).get("dra_max_rounds", 2)),
            )
            revised.runtime_stats = dict(revised.runtime_stats or {})
            revised.runtime_stats["dra_elapsed_seconds"] = round(time.perf_counter() - started, 3)
        orchestrator._build_evidence_summary(revised)
        _write_result(revised, full, output_root, item["project"])


def _generate_variant(config: dict, output_root: Path, item: dict,
                      variant: Variant, registry: Any, resume: bool) -> None:
    if resume and _pair_complete(output_root, variant, item):
        print(f"SKIP complete: {variant.method} / {item['actor']} / {item['use_case']}")
        return
    registry.set_collection(item["project"], item.get("file_names"))
    provider = registry.get_model_provider(think=False)
    retrieval_fn = registry.get_retrieval_adapter()
    with _runtime_switches(variant):
        orchestrator = _orchestrator(
            config, registry, provider, retrieval_fn, variant,
            dra_rounds=(int(config.get("algorithm", {}).get("dra_max_rounds", 2))
                        if variant.dra_enabled else 0),
        )
        uc = orchestrator.elaborate(item["actor"], item["use_case"])
        _write_result(uc, variant, output_root, item["project"])


def _build_project_runtimes(config: dict, registry: Any,
                            retrieval_concurrency: int) -> dict[str, ProjectRuntime]:
    if retrieval_concurrency < 1:
        raise ValueError("retrieval_concurrency must be positive")
    semaphore = threading.BoundedSemaphore(retrieval_concurrency)
    runtimes: dict[str, ProjectRuntime] = {}
    for item in config.get("experiment", {}).get("use_cases", []):
        project = str(item["project"])
        if project in runtimes:
            continue
        registry.set_collection(project, item.get("file_names"))
        adapter = registry.get_retrieval_adapter()

        def limited_retrieval(queries, file_names, topk, _adapter=adapter):
            with semaphore:
                return _adapter(queries, file_names, topk)

        runtimes[project] = ProjectRuntime(
            project=project,
            file_names=tuple(registry.file_names),
            topk=int(registry.topk),
            total_pages=int(registry.total_pages),
            retrieval_fn=limited_retrieval,
        )
    # Initialize the shared API client before worker threads enter lazy paths.
    registry.get_model_provider(think=False)
    return runtimes


def _generate_variant_shared(config: dict, output_root: Path, item: dict,
                             variant: Variant, runtime: ProjectRuntime,
                             registry: Any, resume: bool,
                             production_output: Path,
                             reuse_frozen_evidence: bool) -> str:
    label = f"{variant.method} / {item['actor']} / {item['use_case']}"
    if resume and _pair_complete(output_root, variant, item):
        return f"SKIP {label}"
    print(f"TASK START {label}")
    provider = registry.get_model_provider(think=False)
    with _runtime_switches(variant):
        seed_cards = None
        seed_path = None
        if reuse_frozen_evidence and variant.key != "single_page":
            from run import load_v4_seed_evidence_cards
            try:
                seed_cards, seed_path = load_v4_seed_evidence_cards(
                    production_output, item,
                )
            except (FileNotFoundError, RuntimeError, ValueError) as exc:
                # The four goal-fast-failed candidates do not have valid frozen
                # v4 pools. They intentionally take the same fresh path as their
                # production controls; all valid paper-cohort cases reuse cards.
                print(f"FROZEN EVIDENCE UNAVAILABLE; fresh collection for {label}: {exc}")
        orchestrator = _orchestrator(
            config, runtime, provider, runtime.retrieval_fn, variant,
            dra_rounds=(
                0 if seed_cards is not None else
                int(config.get("algorithm", {}).get("dra_max_rounds", 2))
                if variant.dra_enabled else 0
            ),
        )
        if seed_cards is None:
            uc = orchestrator.elaborate(item["actor"], item["use_case"])
        else:
            uc = orchestrator.elaborate_from_evidence_cards(
                item["actor"], item["use_case"], seed_cards,
            )
            uc.runtime_stats = dict(uc.runtime_stats or {})
            uc.runtime_stats.update({
                "evidence_reused": True,
                "evidence_reuse_source": str(seed_path),
            })
            if variant.dra_enabled:
                uc = _apply_dra(
                    config, uc, item=item, runtime=runtime,
                    provider=provider, orchestrator=orchestrator,
                )
        _write_result(uc, variant, output_root, item["project"])
    return f"DONE {label}"


def _generate_core_pair_shared(config: dict, output_root: Path, item: dict,
                               full: Variant, without: Variant,
                               runtime: ProjectRuntime, registry: Any,
                               resume: bool) -> str:
    label = f"paired full/no-DRA / {item['actor']} / {item['use_case']}"
    if (
        resume
        and _pair_complete(output_root, full, item)
        and _pair_complete(output_root, without, item)
    ):
        return f"SKIP {label}"
    from ablation_runtime.agents import cross_slot_revision

    print(f"TASK START {label}")
    provider = registry.get_model_provider(think=False)
    with _runtime_switches(without):
        orchestrator = _orchestrator(
            config, runtime, provider, runtime.retrieval_fn, without,
            dra_rounds=0,
        )
        base = orchestrator.elaborate(item["actor"], item["use_case"])
        _write_result(base, without, output_root, item["project"])
        revised = copy.deepcopy(base)
        if not _is_unresolvable(revised):
            started = time.perf_counter()
            revised = cross_slot_revision(
                actor=item["actor"], use_case_name=item["use_case"], uc=revised,
                file_names=list(runtime.file_names), topk=runtime.topk,
                retrieval_fn=runtime.retrieval_fn, provider=provider,
                max_rounds=int(config.get("algorithm", {}).get("dra_max_rounds", 2)),
            )
            revised.runtime_stats = dict(revised.runtime_stats or {})
            revised.runtime_stats["dra_elapsed_seconds"] = round(
                time.perf_counter() - started, 3,
            )
        orchestrator._build_evidence_summary(revised)
        _write_result(revised, full, output_root, item["project"])
    return f"DONE {label}"


def generate_threaded(control: dict, base_config_path: Path, output_root: Path,
                      variants: list[Variant], resume: bool, *, workers: int,
                      retrieval_concurrency: int) -> None:
    """Run independent (use case, variant) jobs in one shared-model process."""
    if workers < 1 or workers > 256:
        raise ValueError("--workers must be between 1 and 256")
    core_selected = any(
        variant.key in {"full", "without_dra"} for variant in variants
    )
    _assert_idle(allow_production_core=not core_selected)
    from run import load_config, preflight

    config, resolved_config = load_config(str(base_config_path))
    if preflight(config, resolved_config, ["AUCMR (CREST full)"]) != 0:
        raise RuntimeError("Base AUCMR preflight failed")
    from services.model_registry import registry

    use_cases = list(config.get("experiment", {}).get("use_cases", []))
    production_output = Path(str(config.get("paths", {}).get("output", "outputs")))
    if not production_output.is_absolute():
        production_output = (base_config_path.parent / production_output).resolve()
    reuse_core = bool(control.get("reuse_production_core", True))
    reuse_frozen_evidence = bool(
        control.get("reuse_frozen_evidence_for_compatible_variants", True)
    )
    core_min_mtime = _production_core_min_mtime(control)
    core_variants = [item for item in variants if item.key in {"full", "without_dra"}]
    fresh_variants = [item for item in variants if item.key not in {"full", "without_dra"}]
    full = next((item for item in core_variants if item.key == "full"), None)
    without = next((item for item in core_variants if item.key == "without_dra"), None)

    # Controls are copied and validated before any model is loaded.
    if reuse_core:
        for item in use_cases:
            for variant in core_variants:
                _validated_production_pair(
                    production_output, variant, item, min_mtime=core_min_mtime,
                )
        for item in use_cases:
            for variant in core_variants:
                _reuse_production_pair(
                    production_output, output_root, variant, item, resume,
                    min_mtime=core_min_mtime,
                )
    elif core_variants and (full is None or without is None):
        raise RuntimeError(
            "Fresh threaded controls require both full and without_dra variants"
        )

    variant_tasks = [
        (item, variant)
        for item in use_cases
        for variant in fresh_variants
        if not (resume and _pair_complete(output_root, variant, item))
    ]
    core_tasks = [] if reuse_core else [
        item for item in use_cases
        if not (
            resume
            and _pair_complete(output_root, full, item)
            and _pair_complete(output_root, without, item)
        )
    ]
    task_count = len(core_tasks) + len(variant_tasks)
    canonical_variants = build_variants(control)
    _atomic_json(output_root / "manifest.json", {
        "created_at": dt.datetime.now().isoformat(),
        "generation_version": CREST_GENERATION_VERSION,
        "ablation_generation_version": ABLATION_GENERATION_VERSION,
        "l1_evaluation_version": L1_EVALUATION_VERSION,
        "l2_evaluation_version": L2_EVALUATION_VERSION,
        "base_config": str(resolved_config),
        "output_root": str(output_root),
        "reuse_production_core": reuse_core,
        "production_output": str(production_output),
        "execution": {
            "mode": "shared_model_thread_pool",
            "workers": workers,
            "retrieval_concurrency": retrieval_concurrency,
            "reuse_frozen_evidence_for_compatible_variants": reuse_frozen_evidence,
        },
        "variants": [variant.metadata() for variant in canonical_variants],
        "generation_selection": [variant.metadata() for variant in variants],
        "use_cases": use_cases,
    })
    if not task_count:
        print("All threaded generation tasks are already complete.")
        return

    print(
        f"THREADED GENERATION: pending={task_count} "
        f"(core_pairs={len(core_tasks)}, variants={len(variant_tasks)}), workers={workers}, "
        f"shared_jina_retrieval_concurrency={retrieval_concurrency}"
    )
    # Generation, core synchronization, and evaluation share one stage lock.
    # This closes the start-up race that process scanning alone cannot prevent.
    with _exclusive_lock(output_root):
        runtimes = _build_project_runtimes(config, registry, retrieval_concurrency)
        failures: list[dict[str, Any]] = []
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(workers, task_count),
            thread_name_prefix="crest-ablation",
        ) as executor:
            future_map: dict[Any, dict[str, Any]] = {}
            for item in core_tasks:
                future = executor.submit(
                    _generate_core_pair_shared, config, output_root, item,
                    full, without, runtimes[str(item["project"])], registry, resume,
                )
                future_map[future] = {"kind": "core_pair", "item": item, "variant": None}
            for item, variant in variant_tasks:
                future = executor.submit(
                    _generate_variant_shared, config, output_root, item, variant,
                    runtimes[str(item["project"])], registry, resume,
                    production_output, reuse_frozen_evidence,
                )
                future_map[future] = {"kind": "variant", "item": item, "variant": variant}
            completed = 0
            for future in concurrent.futures.as_completed(future_map):
                descriptor = future_map[future]
                item = descriptor["item"]
                variant = descriptor["variant"]
                completed += 1
                try:
                    print(f"[{completed}/{task_count}] {future.result()}")
                except Exception as exc:
                    failure = {
                        "project": item["project"], "actor": item["actor"],
                        "use_case": item["use_case"],
                        "kind": descriptor["kind"],
                        "variant": variant.metadata() if variant else None,
                        "error": repr(exc),
                    }
                    failures.append(failure)
                    print(f"[{completed}/{task_count}] FAILED {failure}")
        if failures:
            _atomic_json(output_root / "threaded_generation_failures.json", failures)
            raise RuntimeError(
                f"{len(failures)} threaded tasks failed; rerun with --resume. "
                f"Details: {output_root / 'threaded_generation_failures.json'}"
            )
        failure_path = output_root / "threaded_generation_failures.json"
        if failure_path.exists():
            failure_path.unlink()


def generate(control: dict, base_config_path: Path, output_root: Path,
             variants: list[Variant], resume: bool, *, shard_index: int = 0,
             shard_count: int = 1, parallel_worker: bool = False,
             use_case_indices: list[int] | None = None,
             task_lock_id: str | None = None) -> None:
    if shard_count < 1 or not 0 <= shard_index < shard_count:
        raise ValueError(
            f"Invalid shard selection: index={shard_index}, count={shard_count}"
        )
    _assert_idle(allow_parallel_ablation=parallel_worker)
    from run import load_config, preflight

    config, resolved_config = load_config(str(base_config_path))
    if preflight(config, resolved_config, ["AUCMR (CREST full)"]) != 0:
        raise RuntimeError("Base AUCMR preflight failed")
    from services.model_registry import registry

    production_output = Path(str(config.get("paths", {}).get("output", "outputs")))
    if not production_output.is_absolute():
        production_output = (base_config_path.parent / production_output).resolve()
    reuse_core = bool(control.get("reuse_production_core", True))
    core_min_mtime = _production_core_min_mtime(control)
    all_use_cases = list(config.get("experiment", {}).get("use_cases", []))
    selected_indices = (
        set(use_case_indices) if use_case_indices is not None
        else set(range(len(all_use_cases)))
    )
    invalid_indices = sorted(
        index for index in selected_indices
        if index < 0 or index >= len(all_use_cases)
    )
    if invalid_indices:
        raise ValueError(f"Invalid use-case indices: {invalid_indices}")
    use_cases = [
        item for index, item in enumerate(all_use_cases)
        if index in selected_indices and index % shard_count == shard_index
    ]
    core_variants = [item for item in variants if item.key in {"full", "without_dra"}]
    if reuse_core:
        # Validate the complete cohort before writing or loading any models.  A
        # stale/incomplete production control is an error, never an implicit
        # request to spend hours regenerating AUCMR full.
        for item in use_cases:
            for variant in core_variants:
                _validated_production_pair(
                    production_output, variant, item, min_mtime=core_min_mtime,
                )

    canonical_variants = build_variants(control)
    manifest = {
        "created_at": dt.datetime.now().isoformat(),
        "generation_version": CREST_GENERATION_VERSION,
        "ablation_generation_version": ABLATION_GENERATION_VERSION,
        "l1_evaluation_version": L1_EVALUATION_VERSION,
        "l2_evaluation_version": L2_EVALUATION_VERSION,
        "base_config": str(resolved_config),
        "output_root": str(output_root),
        "reuse_production_core": reuse_core,
        "production_output": str(production_output) if reuse_core else None,
        "variants": [variant.metadata() for variant in canonical_variants],
        "generation_selection": [variant.metadata() for variant in variants],
        "use_cases": all_use_cases,
    }
    if not parallel_worker:
        _atomic_json(output_root / "manifest.json", manifest)
    worker_id_raw = task_lock_id or f"shard_{shard_index:02d}_of_{shard_count:02d}"
    worker_id = re.sub(r"[^A-Za-z0-9_-]+", "-", worker_id_raw).strip("-")
    if not worker_id:
        raise ValueError(f"Unsafe worker id: {worker_id_raw!r}")
    _atomic_json(
        output_root / "worker_manifests" / f"{worker_id}.json",
        {
            "pid": os.getpid(),
            "started_at": dt.datetime.now().isoformat(),
            "shard_index": shard_index,
            "shard_count": shard_count,
            "use_cases": use_cases,
            "variants": [variant.metadata() for variant in variants],
        },
    )

    full = next((item for item in variants if item.key == "full"), None)
    without = next((item for item in variants if item.key == "without_dra"), None)
    handled = (
        {item.key for item in core_variants}
        if reuse_core else ({"full", "without_dra"} if full and without else set())
    )
    if task_lock_id:
        safe_lock_id = re.sub(r"[^A-Za-z0-9_-]+", "-", task_lock_id).strip("-")
        lock_name = f".ablation-task-{safe_lock_id}.lock"
    else:
        lock_name = (
            ".ablation.lock" if shard_count == 1
            else f".ablation-shard-{shard_index:02d}-of-{shard_count:02d}.lock"
        )
    with _exclusive_lock(output_root, lock_name):
        print(
            f"SHARD {shard_index + 1}/{shard_count}: "
            f"{len(use_cases)} use cases, {len(variants)} configured runs"
        )
        for item in use_cases:
            if reuse_core:
                for variant in core_variants:
                    _reuse_production_pair(
                        production_output, output_root, variant, item, resume,
                        min_mtime=core_min_mtime,
                    )
            elif full and without:
                _generate_core_pair(config, output_root, item, full, without, registry, resume)
            for variant in variants:
                if variant.key in handled:
                    continue
                _generate_variant(config, output_root, item, variant, registry, resume)


def generate_parallel(control_path: Path, output_root: Path, *, workers: int,
                      variants: list[str] | None, resume: bool) -> None:
    """Launch disjoint use-case shards and wait for all workers."""
    if workers < 2 or workers > 8:
        raise ValueError("--workers must be between 2 and 8")
    _assert_idle()
    logs_dir = output_root / "worker_logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).resolve()
    processes: list[tuple[subprocess.Popen, Any, Path]] = []
    with _exclusive_lock(output_root, ".ablation-parallel.lock"):
        try:
            for shard_index in range(workers):
                log_path = logs_dir / f"generate_shard_{shard_index:02d}_of_{workers:02d}.log"
                log_handle = log_path.open("a" if resume else "w", encoding="utf-8")
                command = [
                    sys.executable, str(script), "generate",
                    "--control", str(control_path.resolve()),
                    "--shard-index", str(shard_index),
                    "--shard-count", str(workers),
                    "--parallel-worker",
                ]
                if resume:
                    command.append("--resume")
                if variants:
                    command.extend(["--variants", *variants])
                process = subprocess.Popen(
                    command, cwd=str(ROOT), stdout=log_handle,
                    stderr=subprocess.STDOUT, text=True,
                )
                processes.append((process, log_handle, log_path))
                print(
                    f"STARTED shard {shard_index + 1}/{workers}: "
                    f"pid={process.pid}, log={log_path}"
                )

            failures: list[tuple[int, int, Path]] = []
            while processes:
                remaining = []
                for process, handle, log_path in processes:
                    code = process.poll()
                    if code is None:
                        remaining.append((process, handle, log_path))
                        continue
                    handle.close()
                    print(f"FINISHED pid={process.pid}, exit={code}, log={log_path}")
                    if code != 0:
                        failures.append((process.pid, code, log_path))
                processes = remaining
                if failures:
                    raise RuntimeError(f"Parallel ablation worker failed: {failures}")
                if processes:
                    time.sleep(2)
        except (KeyboardInterrupt, Exception):
            for process, _handle, _log_path in processes:
                if process.poll() is None:
                    process.send_signal(signal.SIGINT)
            for process, handle, _log_path in processes:
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.terminate()
                handle.close()
            raise


def _metric_from_dict(data: dict):
    from eval.compute_metrics import SlotMetrics, UCMetrics

    values = dict(data)
    values["slot_metrics"] = {
        name: SlotMetrics(**metric) for name, metric in values.get("slot_metrics", {}).items()
    }
    return UCMetrics(**values)


def _metric_cache_path(output_root: Path, variant: Variant, item: dict,
                       run_nli: bool, run_l2: bool) -> Path:
    version = (
        f"{L1_EVALUATION_VERSION if run_nli else 'l1-off'}__"
        f"{L2_EVALUATION_VERSION if run_l2 else 'l2-off'}"
    )
    mode = re.sub(r"[^A-Za-z0-9_.-]+", "-", version)
    stem = _safe_stem(str(item["actor"]), str(item["use_case"]))
    main_path, _ = _paths(output_root, variant, item)
    revision = _read_json(main_path).get("artifact_revision_id", "missing-revision")
    return (
        output_root / "evaluation_cache" / mode / variant.directory
        / str(item["project"]) / f"{stem}-{revision}.json"
    )


def _valid_cohort(config: dict, base_config_path: Path, control: dict,
                  use_cases: list[dict]) -> tuple[list[dict], Path]:
    """Load the exact formal-paper cohort used by the current L1/L2 audits."""
    production_output = Path(str(config.get("paths", {}).get("output", "outputs")))
    if not production_output.is_absolute():
        production_output = (base_config_path.parent / production_output).resolve()
    cohort_name = str(
        control.get("evaluation", {}).get(
            "cohort_manifest", "evaluation_cohort_l1.json",
        )
    )
    cohort_path = Path(cohort_name)
    if not cohort_path.is_absolute():
        cohort_path = (production_output / cohort_path).resolve()
    cohort = _read_json(cohort_path)
    identities = {
        (str(item["project"]), str(item["actor"]), str(item["use_case"]))
        for item in cohort.get("valid_use_cases", [])
    }
    valid = [
        item for item in use_cases
        if (str(item["project"]), str(item["actor"]), str(item["use_case"]))
        in identities
    ]
    selected = {
        (str(item["project"]), str(item["actor"]), str(item["use_case"]))
        for item in valid
    }
    if not identities or selected != identities:
        missing = sorted(identities - selected)
        raise RuntimeError(
            f"Formal evaluation cohort does not match experiment use cases: "
            f"manifest={cohort_path}, missing={missing}"
        )
    expected_count = int(cohort.get("valid_count", len(identities)))
    if len(valid) != expected_count:
        raise RuntimeError(
            f"Formal cohort count mismatch: expected={expected_count}, actual={len(valid)}"
        )
    return valid, cohort_path


def _fast_fail_rate(output_root: Path, variant: Variant, use_cases: list[dict]) -> float:
    failures = 0
    for item in use_cases:
        main_path, _ = _paths(output_root, variant, item)
        data = _read_json(main_path)
        flags = data.get("quality_flags", []) or []
        failures += int(any(flag.get("flag_type") == "unresolvable_use_case" for flag in flags))
    return failures / len(use_cases) * 100 if use_cases else 0.0


def _mean_sd(values: list[float]) -> dict[str, float]:
    return {
        "mean": statistics.mean(values),
        "sd": statistics.stdev(values) if len(values) > 1 else 0.0,
    }


def _fmt(value: float, decimals: int) -> str:
    return f"{value:.{decimals}f}"


def _fmt_summary(item: dict, field: str, decimals: int) -> str:
    value = item[field]
    if isinstance(value, dict) and "mean" in value:
        return f"${_fmt(value['mean'], decimals)}\\pm{_fmt(value['sd'], decimals)}$"
    return _fmt(float(value), decimals)


def _write_table(output_root: Path, summary: dict) -> None:
    rows = [
        ("完整 AUCMR(CREST full)", "full"),
        ("$-$ Cockburn 依赖排序(5种随机排列)", "random_order"),
        ("$-$ 跨槽证据卡片复用", "without_cross_slot_reuse"),
        ("$-$ 完整区域阅读(单页阅读)", "single_page"),
        ("$-$ 强制证据约束(允许有/无引用断言)", "without_evidence_constraint"),
        ("$-$ DRA 跨槽遍历", "without_dra"),
    ]
    lines = []
    for label, key in rows:
        item = summary[key]
        lines.append(
            f"{label} & {_fmt_summary(item, 'l1_nli_g', 1)} & "
            f"{_fmt_summary(item, 'l2_total_per_uc', 2)} & "
            f"{_fmt_summary(item, 'goal_failure_rate', 1)} \\\\"
        )
    table = "\n".join(lines) + "\n"
    (output_root / "table_ablation_crest_rows.tex").write_text(table, encoding="utf-8")


def evaluate(control: dict, base_config_path: Path, output_root: Path,
             variants: list[Variant], resume: bool,
             run_nli: bool, run_l2: bool, workers: int = 1) -> None:
    _assert_idle()
    from run import load_config

    config, _ = load_config(str(base_config_path))
    use_cases = list(config.get("experiment", {}).get("use_cases", []))
    incomplete = [
        {
            "variant": variant.method,
            "project": item["project"],
            "actor": item["actor"],
            "use_case": item["use_case"],
        }
        for variant in variants
        for item in use_cases
        if not _pair_complete(output_root, variant, item)
    ]
    if incomplete:
        preview = incomplete[:10]
        raise RuntimeError(
            f"Ablation generation is incomplete or mismatched: "
            f"{len(incomplete)} of {len(variants) * len(use_cases)} result pairs; "
            f"first entries={json.dumps(preview, ensure_ascii=False)}"
        )
    full = next((variant for variant in variants if variant.key == "full"), None)
    if full is None:
        raise RuntimeError("Evaluation requires the full variant to define the shared cohort")
    valid, formal_cohort_path = _valid_cohort(
        config, base_config_path, control, use_cases,
    )

    from eval.compute_metrics import MetricsComputer
    if workers < 1 or workers > 256:
        raise ValueError("evaluation workers must be between 1 and 256")
    computer = MetricsComputer(nli_judge=None)
    grouped: dict[str, list[Any]] = {variant.method: [] for variant in variants}

    def correcting_l2_judge(attempt: int):
        """Same L2 rubric/model with an additional output-schema correction."""
        from eval.l2_judge import L2Judge

        class _CorrectingL2Judge(L2Judge):
            def _call_model(self, system_prompt: str, user_prompt: str):
                correction = (
                    f"\n\n=== OUTPUT-SCHEMA CORRECTION RETRY {attempt} ===\n"
                    "Re-evaluate the same R1-R7 rubric from scratch; do not change the "
                    "substantive judgment. Before returning, validate every object: "
                    "rule_violated must be one of the exact rule names allowed by the "
                    "system prompt; offending_slot must be one exact slot name shown in "
                    "the input; offending_claim_index must be -1 for a slot-wide or "
                    "missing-claim problem, otherwise it must be an existing bracketed "
                    "0-based claim index in that offending_slot. Empty slots are tracked "
                    "by ESC and must not be reported as L2 violations. Return only one "
                    "valid JSON array, with no markdown or commentary.\n"
                )
                return super()._call_model(system_prompt, user_prompt + correction)

        return _CorrectingL2Judge()

    def discard_empty_slot_l2_failures(metric: Any, main_path: Path) -> bool:
        """Apply the formal L2 rule that empty slots are ESC-only, not violations.

        This is deliberately narrow: it only resolves invalid judge items from
        a goal-fast-failed artifact when every observable offending slot in the
        failed response is absent or empty. Other parse/location failures remain
        fatal and continue to block aggregation.
        """
        failures = list(metric.l2_parse_failures or [])
        if not failures:
            return False
        artifact = _read_json(main_path)
        flags = artifact.get("quality_flags", []) or []
        if not any(
            flag.get("flag_type") == "unresolvable_use_case" for flag in flags
        ):
            return False
        slots = artifact.get("slots", {}) or {}
        observed_slots: list[str] = []
        for failure in failures:
            raw = str(failure.get("raw", ""))
            names = re.findall(r'"offending_slot"\s*:\s*"([^"]+)"', raw)
            if not names:
                return False
            observed_slots.extend(names)
        for slot_name in observed_slots:
            slot = slots.get(slot_name) or {}
            if (slot.get("claims") or []) or str(slot.get("raw_text") or "").strip():
                return False
        details = dict(metric.l2_details or {})
        details["discarded_empty_slot_judge_items"] = failures
        details["parse_failures"] = []
        details["normalization_rule"] = (
            "Formal L2 rubric: empty slots are counted by ESC and do not count "
            "as R1-R7 violations."
        )
        metric.l2_details = details
        metric.l2_parse_failures = []
        return True

    def compute_one(variant: Variant, item: dict):
        main_path, evidence_path = _paths(output_root, variant, item)
        if not main_path.is_file() or not evidence_path.is_file():
            raise FileNotFoundError(f"Incomplete ablation result pair: {main_path}")
        cache = _metric_cache_path(output_root, variant, item, run_nli, run_l2)
        cached_metric = None
        retry_nli = run_nli
        retry_l2 = run_l2
        if resume and cache.is_file():
            cached_metric = _metric_from_dict(_read_json(cache))
            normalized_cache = discard_empty_slot_l2_failures(
                cached_metric, main_path,
            )
            retry_nli = bool(
                run_nli and (
                    not cached_metric.nli_evaluated
                    or cached_metric.nli_parse_failures
                )
            )
            retry_l2 = bool(
                run_l2 and (
                    not cached_metric.l2_evaluated
                    or cached_metric.l2_parse_failures
                )
            )
            if not retry_nli and not retry_l2:
                if normalized_cache:
                    _atomic_json(cache, dataclasses.asdict(cached_metric))
                return variant, cached_metric
            print(
                f"RETRY INVALID CACHE: {variant.method} / "
                f"{item['actor']} / {item['use_case']} "
                f"(L1={retry_nli}, L2={retry_l2})"
            )
        # The official evaluator intentionally shares one MetricsComputer so
        # its thread-safe judge cache/coalescing is effective across workers.
        retry_attempts = 3 if cached_metric is not None and retry_l2 else 1
        fresh_metric = None
        for attempt in range(1, retry_attempts + 1):
            fresh_metric = computer.compute_all(
                str(main_path), str(evidence_path),
                run_nli=retry_nli, run_l2=retry_l2,
                l2_judge=(
                    correcting_l2_judge(attempt)
                    if cached_metric is not None and retry_l2 else None
                ),
            )
            discard_empty_slot_l2_failures(fresh_metric, main_path)
            if not retry_l2 or not fresh_metric.l2_parse_failures:
                break
            print(
                f"L2 FORMAT RETRY {attempt}/{retry_attempts} still invalid: "
                f"{variant.method} / {item['actor']} / {item['use_case']}"
            )
        assert fresh_metric is not None
        metric = cached_metric or fresh_metric
        if cached_metric is not None and retry_nli:
            metric.total_entailed_claims = fresh_metric.total_entailed_claims
            metric.nli_g = fresh_metric.nli_g
            metric.nli_details = fresh_metric.nli_details
            metric.nli_parse_failures = fresh_metric.nli_parse_failures
            metric.nli_evaluated = fresh_metric.nli_evaluated
        if cached_metric is not None and retry_l2:
            metric.l2_violations = fresh_metric.l2_violations
            metric.l2_parse_failures = fresh_metric.l2_parse_failures
            metric.l2_details = fresh_metric.l2_details
            metric.l2_evaluated = fresh_metric.l2_evaluated
        _atomic_json(cache, dataclasses.asdict(metric))
        return variant, metric

    with _exclusive_lock(output_root):
        jobs = [(variant, item) for variant in variants for item in valid]
        print(f"THREADED EVALUATION: jobs={len(jobs)}, workers={min(workers, len(jobs))}")
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(workers, len(jobs)),
            thread_name_prefix="crest-eval",
        ) as executor:
            futures = [executor.submit(compute_one, variant, item) for variant, item in jobs]
            for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
                variant, metric = future.result()
                grouped[variant.method].append(metric)
                print(f"EVAL [{index}/{len(jobs)}] {variant.method} / {metric.use_case_name}")

    aggregated: dict[str, dict] = {}
    for variant in variants:
        value = computer.compute_aggregate(grouped[variant.method], variant.method)
        if run_nli and not value.get("l1_valid", False):
            raise RuntimeError(f"NLI evaluation incomplete for {variant.method}: {value}")
        if run_l2 and not value.get("l2_valid", False):
            raise RuntimeError(f"L2 evaluation incomplete for {variant.method}: {value}")
        value["goal_failure_rate"] = round(_fast_fail_rate(output_root, variant, use_cases), 3)
        aggregated[variant.method] = value

    _atomic_json(output_root / "aggregated_by_run.json", aggregated)
    summary: dict[str, Any] = {}
    singular_keys = [
        "full", "without_dra", "without_cross_slot_reuse", "single_page",
        "without_evidence_constraint",
    ]
    for key in singular_keys:
        variant = next(item for item in variants if item.key == key)
        value = aggregated[variant.method]
        summary[key] = {
            "l1_nli_g": value["l1_nli_g"],
            "l2_total_per_uc": value["l2_total_per_uc"],
            "goal_failure_rate": value["goal_failure_rate"],
        }
    random_variants = [item for item in variants if item.key == "random_order"]
    if len(random_variants) < 3:
        raise RuntimeError("Random-order ablation requires at least three distinct permutations")
    random_runs = [aggregated[item.method] for item in random_variants]
    summary["random_order"] = {
        "n": len(random_runs),
        "seeds": [item.seed for item in random_variants],
        "orders": [list(item.slot_order or []) for item in random_variants],
        "l1_nli_g": _mean_sd([float(item["l1_nli_g"]) for item in random_runs]),
        "l2_total_per_uc": _mean_sd([float(item["l2_total_per_uc"]) for item in random_runs]),
        "goal_failure_rate": _mean_sd([float(item["goal_failure_rate"]) for item in random_runs]),
        "per_run": random_runs,
    }
    summary["versions"] = {
        "generation": CREST_GENERATION_VERSION,
        "ablation_generation": ABLATION_GENERATION_VERSION,
        "l1": L1_EVALUATION_VERSION,
        "l2": L2_EVALUATION_VERSION,
    }
    _atomic_json(output_root / "ablation_summary.json", summary)
    _atomic_json(output_root / "evaluation_cohort.json", {
        "selection_rule": "exact formal L1/L2 paper cohort",
        "source_manifest": str(formal_cohort_path),
        "candidate_count": len(use_cases), "valid_count": len(valid),
        "valid_use_cases": valid,
        "generation_version": CREST_GENERATION_VERSION,
        "l1_evaluation_version": L1_EVALUATION_VERSION,
        "l2_evaluation_version": L2_EVALUATION_VERSION,
    })
    _atomic_json(output_root / "evaluation_manifest.json", {
        "created_at": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(),
        "generation_version": CREST_GENERATION_VERSION,
        "ablation_generation_version": ABLATION_GENERATION_VERSION,
        "l1_evaluation_version": L1_EVALUATION_VERSION,
        "l2_evaluation_version": L2_EVALUATION_VERSION,
        "run_nli": run_nli,
        "run_l2_judge": run_l2,
        "workers": min(workers, len(variants) * len(valid)),
        "result_pairs": len(variants) * len(valid),
        "judge_models": {
            "l1_text": config.get("models", {}).get("nli_judge", {}).get("model_name"),
            "l1_vision": config.get("models", {}).get("nli_vision_judge", {}).get("model_name"),
            "l2": config.get("models", {}).get("l2_judge", {}).get("model_name"),
        },
    })
    _write_table(output_root, summary)
    print(output_root / "ablation_summary.json")


def _production_reuse_status(control: dict, base_config_path: Path,
                             variants: list[Variant]) -> dict[str, Any]:
    enabled = bool(control.get("reuse_production_core", True))
    status: dict[str, Any] = {"enabled": enabled, "valid_pairs": 0, "errors": []}
    if not enabled:
        return status
    config = _read_json(base_config_path)
    production_output = Path(str(config.get("paths", {}).get("output", "outputs")))
    if not production_output.is_absolute():
        production_output = (base_config_path.parent / production_output).resolve()
    status["production_output"] = str(production_output)
    min_mtime = _production_core_min_mtime(control)
    status["min_mtime"] = control.get("production_core_min_mtime")
    core_variants = [item for item in variants if item.key in {"full", "without_dra"}]
    for item in config.get("experiment", {}).get("use_cases", []):
        for variant in core_variants:
            try:
                _validated_production_pair(
                    production_output, variant, item, min_mtime=min_mtime,
                )
                status["valid_pairs"] += 1
            except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
                status["errors"].append(str(exc))
    status["ready"] = not status["errors"]
    return status


def _frozen_evidence_status(control: dict, base_config_path: Path) -> dict[str, Any]:
    enabled = bool(control.get("reuse_frozen_evidence_for_compatible_variants", True))
    status: dict[str, Any] = {
        "enabled": enabled,
        "compatible_variants": [
            "random_order", "without_cross_slot_reuse",
            "without_evidence_constraint",
        ],
        "valid_pools": 0,
        "fresh_fallbacks": [],
    }
    if not enabled:
        return status
    from run import load_config, load_v4_seed_evidence_cards

    config, resolved = load_config(str(base_config_path))
    production_output = Path(str(config.get("paths", {}).get("output", "outputs")))
    if not production_output.is_absolute():
        production_output = (resolved.parent / production_output).resolve()
    for item in config.get("experiment", {}).get("use_cases", []):
        try:
            load_v4_seed_evidence_cards(production_output, item)
            status["valid_pools"] += 1
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            status["fresh_fallbacks"].append({
                "project": item["project"],
                "actor": item["actor"],
                "use_case": item["use_case"],
                "reason": str(exc),
            })
    return status


def print_plan(control: dict, base_config_path: Path, output_root: Path,
               variants: list[Variant]) -> None:
    conflicts, concurrent = _running_experiments()
    payload = {
        "base_config": str(base_config_path),
        "output_root": str(output_root),
        "versions": {
            "generation": CREST_GENERATION_VERSION,
            "ablation_generation": ABLATION_GENERATION_VERSION,
            "l1": L1_EVALUATION_VERSION,
            "l2": L2_EVALUATION_VERSION,
        },
        "active_conflicts": conflicts,
        "concurrent_non_blocking_runs": concurrent,
        "production_core_reuse": _production_reuse_status(
            control, base_config_path, variants,
        ),
        "frozen_evidence_reuse": _frozen_evidence_status(
            control, base_config_path,
        ),
        "variants": [variant.metadata() for variant in variants],
        "task_matrix": {
            "use_cases": len(_read_json(base_config_path).get("experiment", {}).get("use_cases", [])),
            "configured_runs": len(variants),
            "result_pairs": (
                len(_read_json(base_config_path).get("experiment", {}).get("use_cases", []))
                * len(variants)
            ),
        },
        "execution": control.get("execution", {}),
        "next_commands": [
            "python ablation/crest_ablation.py generate-threaded --resume",
            "python ablation/crest_ablation.py sync-core --resume",
            "python ablation/crest_ablation.py evaluate --resume",
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Isolated CREST component ablations")
    parser.add_argument(
        "command",
        choices=(
            "plan", "generate", "generate-parallel", "generate-threaded",
            "sync-core", "evaluate", "all",
        ),
    )
    parser.add_argument("--control", default=str(DEFAULT_CONTROL))
    parser.add_argument("--variants", nargs="+")
    parser.add_argument("--random-seeds", nargs="+", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--workers", type=int)
    parser.add_argument("--retrieval-workers", type=int)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--use-case-indices", nargs="+", type=int)
    parser.add_argument("--task-lock-id")
    parser.add_argument("--parallel-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--run-nli", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--run-l2", action=argparse.BooleanOptionalAction, default=None)
    args = parser.parse_args()

    control, base_config_path, output_root = load_control(Path(args.control))
    execution = control.get("execution", {})
    selected_variants = args.variants
    if args.command == "generate-threaded" and selected_variants is None:
        selected_variants = list(execution.get("threaded_generation_variants", []))
    elif args.command == "sync-core":
        selected_variants = ["full", "without_dra"]
    variants = build_variants(control, selected_variants, args.random_seeds)
    generation_workers = int(
        args.workers if args.workers is not None
        else execution.get("generation_workers", 144)
    )
    retrieval_workers = int(
        args.retrieval_workers if args.retrieval_workers is not None
        else execution.get("retrieval_workers", 1)
    )
    evaluation_workers = int(
        args.workers if args.workers is not None
        else execution.get("evaluation_workers", 180)
    )
    evaluation = control.get("evaluation", {})
    run_nli = bool(evaluation.get("run_nli", True)) if args.run_nli is None else args.run_nli
    run_l2 = bool(evaluation.get("run_l2_judge", True)) if args.run_l2 is None else args.run_l2

    if args.command == "plan":
        print_plan(control, base_config_path, output_root, variants)
        return 0
    if args.command == "generate-threaded":
        generate_threaded(
            control, base_config_path, output_root, variants, args.resume,
            workers=generation_workers, retrieval_concurrency=retrieval_workers,
        )
        return 0
    if args.command == "generate-parallel":
        generate_parallel(
            Path(args.control), output_root, workers=generation_workers,
            variants=args.variants, resume=args.resume,
        )
        return 0
    if (
        args.command in {"generate", "all"}
        and control.get("reuse_frozen_evidence_for_compatible_variants", True)
        and any(variant.key not in {"full", "without_dra"} for variant in variants)
    ):
        raise RuntimeError(
            "Current component ablations require the frozen-evidence-aware "
            "generate-threaded entry point; use `generate-threaded --resume`"
        )
    if args.command in {"generate", "sync-core", "all"}:
        generate(
            control, base_config_path, output_root, variants,
            (True if args.command == "sync-core" else args.resume),
            shard_index=args.shard_index, shard_count=args.shard_count,
            parallel_worker=args.parallel_worker,
            use_case_indices=args.use_case_indices,
            task_lock_id=args.task_lock_id,
        )
    if args.command in {"evaluate", "all"}:
        evaluate(
            control, base_config_path, output_root, variants, args.resume,
            run_nli=run_nli, run_l2=run_l2, workers=evaluation_workers,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
