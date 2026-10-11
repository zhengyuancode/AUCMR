"""Isolated CREST component-ablation runner.

This entry point deliberately uses ``ablation_runtime`` and
``outputs_ablation``.  It never imports or writes the production ``crest``
package/output tree, and refuses to run while a normal AUCMR generation or
another ablation process is active.
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import datetime as dt
import json
import os
import random
import re
import statistics
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_CONTROL = Path(__file__).with_name("experiment.json")
COCKBURN_ORDER = [
    "goal", "primary_actor", "preconditions", "trigger", "main_flow",
    "alternative_flow", "exception_flow", "postconditions",
]


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


def _read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def load_control(path: Path) -> tuple[dict, Path, Path]:
    path = path.expanduser().resolve()
    control = _read_json(path)
    base = (path.parent / str(control["base_config"])).resolve()
    output = (path.parent / str(control["output_root"])).resolve()
    return control, base, output


def build_variants(control: dict, selected: Iterable[str] | None = None) -> list[Variant]:
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
        for seed in control.get("random_order_seeds", []):
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
        data = _read_json(main_path)
        return (
            data.get("method") == variant.method
            and data.get("ablation", {}).get("key") == variant.key
            and data.get("ablation", {}).get("seed") == variant.seed
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


def _validated_production_pair(production_root: Path, variant: Variant,
                               item: dict) -> tuple[dict, dict]:
    main_path, evidence_path = _production_paths(production_root, variant, item)
    if not main_path.is_file() or not evidence_path.is_file():
        raise FileNotFoundError(
            f"Reusable production pair is incomplete: {main_path} / {evidence_path}"
        )
    main = _read_json(main_path)
    evidence = _read_json(evidence_path)
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
                           variant: Variant, item: dict, resume: bool) -> None:
    target_main, target_evidence = _paths(output_root, variant, item)
    if resume and _pair_complete(output_root, variant, item):
        existing_main = _read_json(target_main)
        existing_evidence = _read_json(target_evidence)
        main_source = existing_main.get("ablation_source", {})
        evidence_source = existing_evidence.get("ablation_source", {})
        if (
            main_source.get("kind") == "validated_production_artifact"
            and evidence_source.get("kind") == "validated_production_artifact"
        ):
            print(f"SKIP reused: {variant.method} / {item['actor']} / {item['use_case']}")
            return
    main, evidence = _validated_production_pair(production_root, variant, item)
    provenance = {
        "kind": "validated_production_artifact",
        "source_directory": (
            "outputs/aucmr_full" if variant.key == "full"
            else "outputs/crest_without_dra"
        ),
    }
    main = copy.deepcopy(main)
    evidence = copy.deepcopy(evidence)
    main["ablation"] = variant.metadata()
    main["ablation_source"] = provenance
    evidence["ablation"] = variant.metadata()
    evidence["ablation_source"] = provenance
    _atomic_json(target_main, main)
    _atomic_json(target_evidence, evidence)
    print(f"REUSED {target_main}")


def _write_result(uc: Any, variant: Variant, output_root: Path, project: str) -> None:
    item = {"project": project, "actor": uc.actor, "use_case": uc.use_case_name}
    main_path, evidence_path = _paths(output_root, variant, item)
    main = uc.to_dict()
    main["method"] = variant.method
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
        "ablation": variant.metadata(),
        "evidence_summary": uc.evidence_summary,
        "referenced_card_ids": sorted(referenced),
        "evidence_cards": [card.to_dict() for card in cards.values()],
    }
    _atomic_json(main_path, main)
    _atomic_json(evidence_path, evidence)
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


def _assert_idle() -> None:
    conflicts = _active_conflicts()
    if conflicts:
        raise RuntimeError(
            "Refusing to start an ablation while another AUCMR generation/revision is active: "
            + json.dumps(conflicts, ensure_ascii=False)
        )


@contextmanager
def _exclusive_lock(output_root: Path):
    output_root.mkdir(parents=True, exist_ok=True)
    lock = output_root / ".ablation.lock"
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

    old_single = agents.ABLATION_SINGLE_PAGE_ONLY
    agents.ABLATION_SINGLE_PAGE_ONLY = variant.single_page_only
    try:
        yield
    finally:
        agents.ABLATION_SINGLE_PAGE_ONLY = old_single


def _is_unresolvable(uc: Any) -> bool:
    return any(flag.flag_type == "unresolvable_use_case" for flag in uc.quality_flags)


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


def generate(control: dict, base_config_path: Path, output_root: Path,
             variants: list[Variant], resume: bool) -> None:
    _assert_idle()
    from run import load_config, preflight

    config, resolved_config = load_config(str(base_config_path))
    if preflight(config, resolved_config, ["AUCMR (CREST full)"]) != 0:
        raise RuntimeError("Base AUCMR preflight failed")
    from services.model_registry import registry

    production_output = Path(str(config.get("paths", {}).get("output", "outputs")))
    if not production_output.is_absolute():
        production_output = (base_config_path.parent / production_output).resolve()
    reuse_core = bool(control.get("reuse_production_core", True))
    core_variants = [item for item in variants if item.key in {"full", "without_dra"}]
    if reuse_core:
        # Validate the complete cohort before writing or loading any models.  A
        # stale/incomplete production control is an error, never an implicit
        # request to spend hours regenerating AUCMR full.
        for item in config.get("experiment", {}).get("use_cases", []):
            for variant in core_variants:
                _validated_production_pair(production_output, variant, item)

    manifest = {
        "created_at": dt.datetime.now().isoformat(),
        "base_config": str(resolved_config),
        "output_root": str(output_root),
        "reuse_production_core": reuse_core,
        "production_output": str(production_output) if reuse_core else None,
        "variants": [variant.metadata() for variant in variants],
        "use_cases": config.get("experiment", {}).get("use_cases", []),
    }
    _atomic_json(output_root / "manifest.json", manifest)

    full = next((item for item in variants if item.key == "full"), None)
    without = next((item for item in variants if item.key == "without_dra"), None)
    handled = (
        {item.key for item in core_variants}
        if reuse_core else ({"full", "without_dra"} if full and without else set())
    )
    with _exclusive_lock(output_root):
        for item in config.get("experiment", {}).get("use_cases", []):
            if reuse_core:
                for variant in core_variants:
                    _reuse_production_pair(
                        production_output, output_root, variant, item, resume,
                    )
            elif full and without:
                _generate_core_pair(config, output_root, item, full, without, registry, resume)
            for variant in variants:
                if variant.key in handled:
                    continue
                _generate_variant(config, output_root, item, variant, registry, resume)


def _metric_from_dict(data: dict):
    from eval.compute_metrics import SlotMetrics, UCMetrics

    values = dict(data)
    values["slot_metrics"] = {
        name: SlotMetrics(**metric) for name, metric in values.get("slot_metrics", {}).items()
    }
    return UCMetrics(**values)


def _metric_cache_path(output_root: Path, variant: Variant, item: dict,
                       run_nli: bool, run_l2: bool) -> Path:
    mode = f"nli{int(run_nli)}_l2{int(run_l2)}"
    stem = _safe_stem(str(item["actor"]), str(item["use_case"]))
    return output_root / "evaluation_cache" / mode / variant.directory / str(item["project"]) / f"{stem}.json"


def _valid_cohort(output_root: Path, full: Variant, use_cases: list[dict]) -> list[dict]:
    valid = []
    for item in use_cases:
        main_path, _ = _paths(output_root, full, item)
        if not main_path.is_file():
            raise FileNotFoundError(f"Full result missing: {main_path}")
        data = _read_json(main_path)
        flags = data.get("quality_flags", []) or []
        if not any(flag.get("flag_type") == "unresolvable_use_case" for flag in flags):
            valid.append(item)
    if not valid:
        raise RuntimeError("The full AUCMR run rejected every candidate use case")
    return valid


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
        ("完整AUCMR（CREST full）", "full"),
        ("$-$ Cockburn依赖排序（5种随机排列）", "random_order"),
        ("$-$ 跨槽证据卡片复用", "without_cross_slot_reuse"),
        ("$-$ 完整区域阅读（单页阅读）", "single_page"),
        ("$-$ 强制证据约束", "without_evidence_constraint"),
        ("$-$ DRA跨槽遍历", "without_dra"),
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
             run_nli: bool, run_l2: bool) -> None:
    _assert_idle()
    from run import load_config

    config, _ = load_config(str(base_config_path))
    use_cases = list(config.get("experiment", {}).get("use_cases", []))
    full = next((variant for variant in variants if variant.key == "full"), None)
    if full is None:
        raise RuntimeError("Evaluation requires the full variant to define the shared cohort")
    valid = _valid_cohort(output_root, full, use_cases)

    from eval.compute_metrics import MetricsComputer
    computer = MetricsComputer(nli_judge=None)
    grouped: dict[str, list[Any]] = {}
    with _exclusive_lock(output_root):
        for variant in variants:
            metrics = []
            for item in valid:
                main_path, evidence_path = _paths(output_root, variant, item)
                if not main_path.is_file() or not evidence_path.is_file():
                    raise FileNotFoundError(f"Incomplete ablation result pair: {main_path}")
                cache = _metric_cache_path(output_root, variant, item, run_nli, run_l2)
                if resume and cache.is_file():
                    metric = _metric_from_dict(_read_json(cache))
                else:
                    metric = computer.compute_all(
                        str(main_path), str(evidence_path),
                        run_nli=run_nli, run_l2=run_l2,
                    )
                    _atomic_json(cache, dataclasses.asdict(metric))
                metrics.append(metric)
            grouped[variant.method] = metrics

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
    _atomic_json(output_root / "ablation_summary.json", summary)
    _atomic_json(output_root / "evaluation_cohort.json", {
        "selection_rule": "full AUCMR results without unresolvable_use_case",
        "candidate_count": len(use_cases), "valid_count": len(valid),
        "valid_use_cases": valid,
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
    core_variants = [item for item in variants if item.key in {"full", "without_dra"}]
    for item in config.get("experiment", {}).get("use_cases", []):
        for variant in core_variants:
            try:
                _validated_production_pair(production_output, variant, item)
                status["valid_pairs"] += 1
            except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
                status["errors"].append(str(exc))
    status["ready"] = not status["errors"]
    return status


def print_plan(control: dict, base_config_path: Path, output_root: Path,
               variants: list[Variant]) -> None:
    conflicts, concurrent = _running_experiments()
    payload = {
        "base_config": str(base_config_path),
        "output_root": str(output_root),
        "active_conflicts": conflicts,
        "concurrent_non_blocking_runs": concurrent,
        "production_core_reuse": _production_reuse_status(
            control, base_config_path, variants,
        ),
        "variants": [variant.metadata() for variant in variants],
        "next_commands": [
            "python ablation/crest_ablation.py generate --resume",
            "python ablation/crest_ablation.py evaluate --resume",
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Isolated CREST component ablations")
    parser.add_argument("command", choices=("plan", "generate", "evaluate", "all"))
    parser.add_argument("--control", default=str(DEFAULT_CONTROL))
    parser.add_argument("--variants", nargs="+")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--run-nli", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--run-l2", action=argparse.BooleanOptionalAction, default=None)
    args = parser.parse_args()

    control, base_config_path, output_root = load_control(Path(args.control))
    variants = build_variants(control, args.variants)
    evaluation = control.get("evaluation", {})
    run_nli = bool(evaluation.get("run_nli", True)) if args.run_nli is None else args.run_nli
    run_l2 = bool(evaluation.get("run_l2_judge", True)) if args.run_l2 is None else args.run_l2

    if args.command == "plan":
        print_plan(control, base_config_path, output_root, variants)
        return 0
    if args.command in {"generate", "all"}:
        generate(control, base_config_path, output_root, variants, args.resume)
    if args.command in {"evaluate", "all"}:
        evaluate(
            control, base_config_path, output_root, variants, args.resume,
            run_nli=run_nli, run_l2=run_l2,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
