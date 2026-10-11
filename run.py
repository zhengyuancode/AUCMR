"""Single server entry point for AUCMR generation and automatic evaluation."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import importlib.util
import json
import logging
import os
import re
import shutil
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "experiment.json"
FULL = "AUCMR (CREST full)"
WITHOUT_DRA = "CREST w/o cross-slot"
BASELINES = {
    "LVLM-Direct",
    "TextRAG-Elaborate",
    "VDocRAG-Elaborate",
    "ViDoRAG-Elaborate",
    "ReAct-Elaborate",
}
ALL_METHODS = {FULL, WITHOUT_DRA, *BASELINES}
ACTIVE_GENERATION_DIRS = ("aucmr_full", "crest_without_dra", "baselines")
LEGACY_EVALUATION_FILES = (
    "evaluation_cohort.json",
    "evaluation_audit.json",
    "aggregated_metrics.json",
    "table_l1.tex",
    "table_l2_consistency.tex",
)
L1_EVALUATION_VERSION = "l1-contextual-source-grounding-v10-method-aware-text-or-cited-image-qwen37"
L2_EVALUATION_VERSION = "l2-r1-r7-unified-qwen35-v3-deduplicated"
CREST_GENERATION_VERSION = "crest-monotonic-atomic-dra-v6-self-validated-collector"
# Frozen paired v4 w/o-DRA cards are the only accepted source for the v5
# re-authoring fast path.  Never seed from a full result: it may contain DRA
# retrieval after the common baseline pool and would bias the paired comparison.
V4_EVIDENCE_REUSE_GENERATION_VERSION = "crest-monotonic-atomic-dra-v4"
DEFAULT_REUSE_EVIDENCE_SUBDIR = Path(
    "history/prompt-v1-pre-rewrite/crest_without_dra"
)
EVALUATION_STAGE_FILES = {
    "l1": (
        "evaluation_cohort_l1.json",
        "evaluation_audit_l1.json",
        "aggregated_metrics_l1.json",
        "table_l1.tex",
        "evaluation_manifest_l1.json",
    ),
    "l2": (
        "evaluation_cohort_l2.json",
        "evaluation_audit_l2.json",
        "aggregated_metrics_l2.json",
        "table_l2_consistency.tex",
        "evaluation_manifest_l2.json",
    ),
}
EVALUATION_FILES = tuple(dict.fromkeys(
    (*LEGACY_EVALUATION_FILES, *EVALUATION_STAGE_FILES["l1"], *EVALUATION_STAGE_FILES["l2"])
))


@dataclass
class GuideAction:
    stage: str
    status: str
    message: str
    command: str | None = None
    branches: list[str] | None = None


def load_config(path: str) -> tuple[dict, Path]:
    config_path = Path(path).expanduser().resolve()
    with config_path.open("r", encoding="utf-8") as f:
        config = json.load(f)
    if not isinstance(config, dict):
        raise ValueError("The config root must be a JSON object")
    os.environ["AUCMR_CONFIG"] = str(config_path)
    # Configure the Hub endpoint before transformers/peft/huggingface_hub are
    # imported.  This keeps model downloads reproducible on servers where the
    # official Hub is unreachable, while still allowing an explicit shell
    # environment variable to take precedence.
    hf_endpoint = str(config.get("runtime", {}).get("hf_endpoint", "")).strip()
    if hf_endpoint:
        os.environ.setdefault("HF_ENDPOINT", hf_endpoint.rstrip("/"))
    return config, config_path


def resolve_path(config_path: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (config_path.parent / path).resolve()


def selected_methods(config: dict, cli_methods: list[str] | None) -> list[str]:
    methods = cli_methods or config.get("experiment", {}).get("methods", [])
    unknown = set(methods) - ALL_METHODS
    if unknown:
        raise ValueError(f"Unknown method(s): {sorted(unknown)}")
    return methods


def select_use_cases(config: dict, cli_use_cases: list[str] | None) -> None:
    """Limit this invocation to exact configured use-case names."""
    if not cli_use_cases:
        return
    requested = {name.casefold(): name for name in cli_use_cases}
    configured = config.get("experiment", {}).get("use_cases", [])
    selected = [
        item for item in configured
        if str(item.get("use_case", "")).casefold() in requested
    ]
    found = {str(item.get("use_case", "")).casefold() for item in selected}
    missing = [requested[key] for key in requested.keys() - found]
    if missing:
        available = sorted(str(item.get("use_case", "")) for item in configured)
        raise ValueError(
            f"Unknown use case name(s): {missing}. Configured names: {available}"
        )
    config["experiment"]["use_cases"] = selected


def select_use_case_identities(config: dict, identities: set[tuple[str, str, str]]) -> None:
    """Limit this invocation to exact (project, use_case, actor) identities."""
    configured = config.get("experiment", {}).get("use_cases", [])
    selected = [
        item for item in configured
        if (
            str(item.get("project", "")),
            str(item.get("use_case", "")),
            str(item.get("actor", "")),
        ) in identities
    ]
    found = {
        (str(item.get("project", "")), str(item.get("use_case", "")), str(item.get("actor", "")))
        for item in selected
    }
    missing = sorted(identities - found)
    if missing:
        raise ValueError(f"Paper-cohort identities are not configured: {missing}")
    if len(selected) != len(identities):
        raise RuntimeError(
            f"Paper-cohort selection is ambiguous: requested {len(identities)}, selected {len(selected)}"
        )
    config["experiment"]["use_cases"] = selected


def preflight(config: dict, config_path: Path, methods: list[str]) -> int:
    errors: list[str] = []
    warnings: list[str] = []
    for key in ("output", "images", "embedder", "linucb", "pca"):
        if key not in config.get("paths", {}):
            errors.append(f"missing paths.{key}")
    for key in ("llm", "lvlm", "nli_judge", "nli_vision_judge", "l2_judge"):
        model = config.get("models", {}).get(key, {})
        for field in ("base_url", "model_name", "api_key_env"):
            if not model.get(field):
                errors.append(f"missing models.{key}.{field}")
        if str(model.get("model_name", "")).startswith("CHANGE_ME"):
            warnings.append(f"models.{key}.model_name still uses a placeholder")
        env_name = model.get("api_key_env")
        if env_name and not os.environ.get(env_name):
            warnings.append(f"environment variable {env_name} is empty")
    registry_methods = {FULL, WITHOUT_DRA, "ViDoRAG-Elaborate", "ReAct-Elaborate"}
    needed_assets: set[str] = set()
    if set(methods) & registry_methods:
        needed_assets.update(("linucb", "pca", "embedder"))
    if "TextRAG-Elaborate" in methods:
        needed_assets.add("embedder")
    for key in needed_assets:
        value = config.get("paths", {}).get(key)
        if value and not resolve_path(config_path, value).exists():
            errors.append(f"paths.{key} does not exist: {resolve_path(config_path, value)}")
    if {"linucb", "pca"} <= needed_assets and not any(
        message.startswith("paths.linucb") or message.startswith("paths.pca")
        for message in errors
    ):
        try:
            from models.LinUCB import OnlineLinUCB, load_pca_assets, validate_artifact_pair

            cmab_model = OnlineLinUCB.load_model(
                resolve_path(config_path, config["paths"]["linucb"])
            )
            cmab_pca = load_pca_assets(
                resolve_path(config_path, config["paths"]["pca"])
            )
            validate_artifact_pair(cmab_model, cmab_pca)
        except Exception as exc:
            errors.append(f"CMAB-TopN artifacts are incompatible: {exc}")
    use_cases = config.get("experiment", {}).get("use_cases", [])
    if not use_cases:
        errors.append("experiment.use_cases is empty")
    for i, item in enumerate(use_cases):
        for field in ("project", "actor", "use_case"):
            if not item.get(field):
                errors.append(f"experiment.use_cases[{i}].{field} is missing")
        if {"LVLM-Direct", "VDocRAG-Elaborate"} & set(methods):
            image_dir = resolve_path(config_path, config["paths"]["images"]) / item.get("project", "")
            if not image_dir.is_dir():
                errors.append(f"page-image directory does not exist: {image_dir}")

    required = ["openai", "json_repair", "PIL"]
    if set(methods) & (registry_methods | {"TextRAG-Elaborate"}):
        required.extend(["numpy", "scipy", "torch", "transformers", "pymilvus"])
    if "VDocRAG-Elaborate" in methods:
        required.extend(["numpy", "torch", "transformers", "peft"])
    for module in required:
        if importlib.util.find_spec(module) is None:
            errors.append(f"missing Python dependency: {module}")
    if "TextRAG-Elaborate" in methods and not config.get("services", {}).get("text_milvus", {}).get("collection_template"):
        errors.append("TextRAG-Elaborate requires services.text_milvus.collection_template")
    vendor_root = config_path.parent / "eval" / "vendor"
    if "VDocRAG-Elaborate" in methods:
        if not (vendor_root / "VDocRAG" / "src" / "vdocrag").is_dir():
            errors.append("VDocRAG official vendor source is missing")
        for field in ("base_model", "retriever_lora", "device", "dtype"):
            if not config.get("baselines", {}).get("vdocrag", {}).get(field):
                errors.append(f"VDocRAG-Elaborate requires baselines.vdocrag.{field}")
        for module in ("peft", "PIL"):
            if importlib.util.find_spec(module) is None:
                errors.append(f"VDocRAG-Elaborate missing Python dependency: {module}")
        vdocrag = config.get("baselines", {}).get("vdocrag", {})
        if str(vdocrag.get("dtype", "")) not in {"float16", "bfloat16", "float32"}:
            errors.append("baselines.vdocrag.dtype must be float16, bfloat16, or float32")
        attention = vdocrag.get("attn_implementation") or "eager"
        if attention not in {"eager", "sdpa", "flash_attention_2"}:
            errors.append(
                "baselines.vdocrag.attn_implementation must be eager, sdpa, or flash_attention_2"
            )
        if vdocrag.get("attn_implementation") == "flash_attention_2" and importlib.util.find_spec("flash_attn") is None:
            errors.append(
                "baselines.vdocrag.attn_implementation=flash_attention_2 requires flash-attn; "
                "set it to null to use the model default"
            )
    if "ViDoRAG-Elaborate" in methods:
        if not (vendor_root / "ViDoRAG" / "vidorag_agents.py").is_file():
            errors.append("ViDoRAG official vendor source is missing")
        else:
            try:
                # Exercise both namespaces together.  A file-existence check
                # alone cannot detect collisions between ViDoRAG's ``utils``
                # package and AUCMR's own retrieval utilities.
                importlib.import_module("eval.vendor.vidorag_adapter")
                importlib.import_module("eval.vendor.ViDoRAG.vidorag_agents")
                importlib.import_module("utils.retrieval")
            except ImportError as exc:
                errors.append(f"ViDoRAG-Elaborate import check failed: {exc}")
        vidorag = config.get("baselines", {}).get("vidorag", {})
        candidate_top_k = int(vidorag.get("candidate_top_k", 10))
        if not 1 <= candidate_top_k <= 20:
            errors.append("baselines.vidorag.candidate_top_k must be between 1 and 20")
        if int(vidorag.get("max_rounds", 5)) <= 0:
            errors.append("baselines.vidorag.max_rounds must be positive")

    print(f"Config: {config_path}")
    print(f"Methods: {', '.join(methods)}")
    print(f"Use cases: {len(use_cases)}")
    for item in warnings:
        print(f"WARNING: {item}")
    for item in errors:
        print(f"ERROR: {item}")
    print("Preflight: PASS" if not errors else f"Preflight: FAIL ({len(errors)} error(s))")
    return 0 if not errors else 2


def text_preflight(config: dict, config_path: Path, projects: list[str] | None = None) -> int:
    """Validate text-index inputs without invoking MinerU, models, or Milvus."""
    errors: list[str] = []
    warnings: list[str] = []
    text_cfg = config.get("text_pipeline", {})
    documents = text_cfg.get("documents", [])
    selected = set(projects or [])
    if not documents:
        errors.append("text_pipeline.documents is empty")
    configured_projects = {str(item.get("project", "")) for item in documents}
    unknown = selected - configured_projects
    if unknown:
        errors.append(f"unknown text-pipeline project(s): {sorted(unknown)}")
    documents = [item for item in documents if not selected or item.get("project") in selected]

    template = config.get("services", {}).get("text_milvus", {}).get("collection_template")
    if not template or "{project}" not in str(template):
        errors.append("services.text_milvus.collection_template must contain {project}")
    if not config.get("services", {}).get("milvus", {}).get("uri"):
        errors.append("missing services.milvus.uri")
    for field in ("text_work", "embedder"):
        if not config.get("paths", {}).get(field):
            errors.append(f"missing paths.{field}")
    embedder = config.get("paths", {}).get("embedder")
    if embedder and not resolve_path(config_path, embedder).exists():
        errors.append(f"paths.embedder does not exist: {resolve_path(config_path, embedder)}")

    mineru_needed = False
    identities: set[tuple[str, str]] = set()
    from pipelines.text_index import find_content_list
    for i, item in enumerate(documents):
        for field in ("project", "file_name", "pdf", "page_images", "parse_dir"):
            if not item.get(field):
                errors.append(f"text_pipeline.documents[{i}].{field} is missing")
        if not all(item.get(field) for field in ("project", "file_name", "pdf", "parse_dir")):
            continue
        identity = (str(item["project"]), str(item["file_name"]))
        if identity in identities:
            errors.append(f"duplicate text document identity: {identity}")
        identities.add(identity)
        pdf = resolve_path(config_path, item["pdf"])
        parse_dir = resolve_path(config_path, item["parse_dir"])
        if not pdf.is_file():
            errors.append(f"PDF does not exist: {pdf}")
        if find_content_list(parse_dir, pdf.stem) is None:
            mineru_needed = True

    configured_names: dict[str, set[str]] = {}
    for item in documents:
        configured_names.setdefault(str(item.get("project", "")), set()).add(
            str(item.get("file_name", ""))
        )
    for use_case in config.get("experiment", {}).get("use_cases", []):
        project = str(use_case.get("project", ""))
        if project not in configured_names:
            continue
        expected = set(use_case.get("file_names") or [project])
        missing = expected - configured_names[project]
        if missing:
            errors.append(
                f"project {project} is missing text_pipeline documents for file_names: {sorted(missing)}"
            )

    command = str(text_cfg.get("mineru", {}).get("command", "mineru"))
    if mineru_needed and shutil.which(command) is None:
        errors.append(f"MinerU command is not available: {command}")
    for module in ("openai", "fitz", "torch", "transformers", "pymilvus"):
        if importlib.util.find_spec(module) is None:
            errors.append(f"missing Python dependency: {module}")
    try:
        chunk_size = int(text_cfg.get("chunk_size", 800))
        overlap = int(text_cfg.get("chunk_overlap", 0))
        if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
            raise ValueError
        if int(text_cfg.get("embedding_dimension", 128)) <= 0:
            raise ValueError
        if int(text_cfg.get("insert_batch_rows", 2000)) <= 0:
            raise ValueError
        if int(text_cfg.get("coarse_candidates", 100)) <= 0:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("invalid numeric value in text_pipeline chunk/vector/batch/search settings")
    lvlm = config.get("models", {}).get("lvlm", {})
    if not lvlm.get("base_url"):
        errors.append("missing models.lvlm.base_url")
    if not lvlm.get("model_name"):
        errors.append("missing models.lvlm.model_name")
    if str(lvlm.get("model_name", "")).startswith("CHANGE_ME"):
        warnings.append("models.lvlm.model_name still uses a placeholder")
    key_env = lvlm.get("api_key_env")
    if key_env and not os.environ.get(key_env):
        warnings.append(f"environment variable {key_env} is empty")

    print(f"Config: {config_path}")
    print(f"Text projects: {', '.join(sorted(selected or configured_projects))}")
    print(f"Documents: {len(documents)}")
    for item in warnings:
        print(f"WARNING: {item}")
    for item in errors:
        print(f"ERROR: {item}")
    print("Text preflight: PASS" if not errors else f"Text preflight: FAIL ({len(errors)} error(s))")
    return 0 if not errors else 2


def run_text_build(config: dict, config_path: Path, projects: list[str] | None,
                   recreate: bool, force_mineru: bool) -> None:
    from pipelines.text_index import build_text_indexes

    summaries = build_text_indexes(
        config, config_path, recreate=recreate,
        force_mineru=force_mineru, projects=projects,
    )
    print(json.dumps(summaries, ensure_ascii=False, indent=2))


def run_prepare_images(config: dict, config_path: Path, projects: list[str] | None) -> None:
    """Render every configured PDF page before image indexing or LVLM baselines."""
    selected = set(projects or [])
    documents = [
        item for item in config.get("text_pipeline", {}).get("documents", [])
        if not selected or str(item.get("project")) in selected
    ]
    configured = {str(item.get("project")) for item in documents}
    unknown = selected - configured
    if unknown:
        raise ValueError(f"Unknown page-image project(s): {sorted(unknown)}")
    if not documents:
        raise ValueError("No configured documents selected for page rendering")

    from fitz import open as open_pdf
    from pipelines.text_index import ensure_page_images

    summaries = []
    for document in documents:
        pdf = resolve_path(config_path, str(document["pdf"]))
        image_dir = resolve_path(config_path, str(document["page_images"]))
        if not pdf.is_file():
            raise FileNotFoundError(f"PDF does not exist: {pdf}")
        with open_pdf(str(pdf)) as pdf_document:
            expected = len(pdf_document)
        ensure_page_images(pdf, image_dir, expected)
        missing = [page for page in range(1, expected + 1) if not (image_dir / f"{page}.png").is_file()]
        if missing:
            raise RuntimeError(
                f"Page rendering incomplete for {document['project']}: "
                f"{len(missing)}/{expected} pages missing"
            )
        summaries.append({
            "project": str(document["project"]),
            "pdf": str(pdf),
            "page_images": str(image_dir),
            "pages": expected,
        })
    print(json.dumps(summaries, ensure_ascii=False, indent=2))


def run_image_build(
    config: dict, config_path: Path, projects: list[str] | None,
    recreate_empty: bool = False,
) -> None:
    """Submit missing page-image collections without ever overwriting one."""
    selected = set(projects or [])
    documents = [
        item for item in config.get("text_pipeline", {}).get("documents", [])
        if not selected or str(item.get("project")) in selected
    ]
    configured = {str(item.get("project")) for item in documents}
    unknown = selected - configured
    if unknown:
        raise ValueError(f"Unknown image project(s): {sorted(unknown)}")
    if not documents:
        raise ValueError("No configured documents selected for image indexing")

    # A zero-row collection may still have a running/failed bulk-import job. It
    # must be inspected explicitly instead of being silently appended to.
    for project in sorted(configured):
        state = _collection_status(config, project, set())
        if not state.get("reachable"):
            raise RuntimeError(f"Milvus is unreachable: {state.get('error')}")
        if state.get("exists"):
            if recreate_empty and int(state.get("rows", 0)) == 0:
                from config.milvus import client as milvus_client
                milvus_client.drop_collection(collection_name=project)
                print(f"Dropped verified empty image collection: {project}")
            else:
                raise RuntimeError(
                    f"Refusing to import into existing image collection {project!r} "
                    f"(row_count={state.get('rows', 0)}). Check the existing bulk-import job."
                )

    run_prepare_images(config, config_path, projects)

    from pipelines.preprocess_minio_multi_img import preprocess_multiImg
    from services.model_registry import registry

    image_cfg = config.get("image_pipeline", {})

    for document in documents:
        project = str(document["project"])
        image_dir = resolve_path(config_path, str(document["page_images"]))
        if not image_dir.is_dir():
            raise FileNotFoundError(f"Page-image directory does not exist: {image_dir}")
        job_root = resolve_path(
            config_path, str(config.get("paths", {}).get("image_work", "data/image_pipeline_jobs")),
        )
        job_root.mkdir(parents=True, exist_ok=True)
        job_path = job_root / f"{project}.json"
        payload = {
            "project": project,
            "file_name": str(document["file_name"]),
            "status": "encoding",
            "started_at": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(),
            "pages": len(list(image_dir.glob("*.png"))),
        }
        job_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        try:
            job_id = preprocess_multiImg(
                output_dir=str(image_dir),
                pdf_name=str(document["file_name"]),
                embeder=registry.embeder,
                collection_name=project,
                embedding_batch_size=int(image_cfg.get("embedding_batch_size", 1)),
                commit_every_pages=int(image_cfg.get("commit_every_pages", 10)),
                max_pixels=image_cfg.get("max_pixels"),
            )
        except BaseException as exc:
            payload.update({
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "recovery_command": (
                    f"python run.py build-image --projects {project} --recreate-empty"
                ),
            })
            job_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
            )
            raise
        payload.update({
            "status": "submitted",
            "job_id": str(job_id),
            "monitor_command": (
                f"python -m pipelines.check_progress {job_id} "
                f"--project {project} --watch"
            ),
        })
        job_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def run_vdocrag_build(config: dict, projects: list[str] | None) -> None:
    """Build/reuse official VDocRetriever page-embedding caches."""
    from eval.vendor.vdocrag_adapter import corpus_embeddings, get_runtime

    selected = set(projects or [])
    use_cases = config.get("experiment", {}).get("use_cases", [])
    known = {str(item["project"]) for item in use_cases}
    unknown = selected - known
    if unknown:
        raise ValueError(f"Unknown VDocRAG project(s): {sorted(unknown)}")
    runtime = get_runtime()
    summaries = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for item in use_cases:
        project = str(item["project"])
        if selected and project not in selected:
            continue
        file_names = [str(value) for value in (item.get("file_names") or [project])]
        identity = (project, tuple(file_names))
        if identity in seen:
            continue
        seen.add(identity)
        paths, embeddings = corpus_embeddings(project, file_names, runtime)
        summaries.append({
            "project": project,
            "file_names": file_names,
            "pages": len(paths),
            "embedding_shape": list(embeddings.shape),
        })
    print(json.dumps(summaries, ensure_ascii=False, indent=2))


def _collection_status(config: dict, name: str, required_fields: set[str]) -> dict:
    try:
        from pymilvus import MilvusClient

        milvus = config.get("services", {}).get("milvus", {})
        client = MilvusClient(uri=str(milvus.get("uri", "")), token=milvus.get("token") or None)
        if not client.has_collection(collection_name=name, timeout=5):
            return {"reachable": True, "exists": False, "rows": 0, "schema_ok": False}
        description = client.describe_collection(collection_name=name, timeout=5)
        fields = {
            str(field.get("name")) for field in description.get("fields", [])
            if isinstance(field, dict)
        }
        stats = client.get_collection_stats(collection_name=name, timeout=5)
        rows = int(stats.get("row_count", 0))
        return {
            "reachable": True, "exists": True, "rows": rows,
            "schema_ok": required_fields <= fields,
            "missing_fields": sorted(required_fields - fields),
        }
    except Exception as exc:
        return {"reachable": False, "error": str(exc)}


def _page_image_status(config_path: Path, document: dict) -> dict:
    pdf = resolve_path(config_path, str(document["pdf"]))
    image_dir = resolve_path(config_path, str(document["page_images"]))
    if not pdf.is_file():
        return {"complete": False, "error": f"PDF does not exist: {pdf}"}
    try:
        from fitz import open as open_pdf
        with open_pdf(str(pdf)) as pdf_document:
            expected = len(pdf_document)
    except Exception as exc:
        return {"complete": False, "error": str(exc)}
    present = sum((image_dir / f"{page}.png").is_file() for page in range(1, expected + 1))
    return {
        "complete": present == expected,
        "expected_pages": expected,
        "present_pages": present,
        "missing_pages": expected - present,
        "directory": str(image_dir),
    }


def _configured_use_case_ids(config: dict) -> set[tuple[str, str, str]]:
    return {
        (str(item["project"]), str(item["use_case"]), str(item["actor"]))
        for item in config.get("experiment", {}).get("use_cases", [])
    }


def _iter_active_result_pairs(output_root: Path):
    """Yield paired generation artifacts from active method directories only.

    In particular, never scan ``outputs/history`` or ad-hoc development folders.
    Baseline image composites and watermarked-page caches are also excluded.
    """
    for directory in ACTIVE_GENERATION_DIRS:
        method_root = output_root / directory
        if not method_root.is_dir():
            continue
        for path in method_root.rglob("*.json"):
            if path.name.endswith("-evidence.json"):
                continue
            relative = path.relative_to(method_root)
            if directory == "baselines" and any(
                part.startswith("_") for part in relative.parts[:-1]
            ):
                continue
            evidence = path.with_name(path.stem + "-evidence.json")
            if evidence.is_file() and _result_pair_is_valid(path, evidence):
                yield path, evidence


def _result_pair_is_valid(main_path: Path, evidence_path: Path) -> bool:
    """Reject malformed or half-committed result/evidence pairs."""
    try:
        main = json.loads(main_path.read_text(encoding="utf-8"))
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    main_revision = main.get("artifact_revision_id")
    evidence_revision = evidence.get("artifact_revision_id")
    if main_revision or evidence_revision:
        return bool(main_revision and main_revision == evidence_revision)
    # Backward-compatible acceptance for complete artifacts written before the
    # revision marker was introduced.
    return True


def _result_counts(config: dict, output_root: Path) -> dict[str, dict]:
    expected = _configured_use_case_ids(config)
    projects = {item[0] for item in expected}
    found: dict[str, set[tuple[str, str, str]]] = {}
    for path, _evidence in _iter_active_result_pairs(output_root):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        method = data.get("method")
        use_case = data.get("use_case_name")
        actor = data.get("actor")
        project = next((parent.name for parent in path.parents if parent.name in projects), None)
        if method in {FULL, WITHOUT_DRA} and data.get("generation_version") != CREST_GENERATION_VERSION:
            # Existing artifacts remain on disk and recoverable, but --resume
            # must not mistake outputs from the pre-verifier algorithm for the
            # current formal generation.
            continue
        if method and use_case and actor and project:
            found.setdefault(str(method), set()).add((project, str(use_case), str(actor)))
    return {
        method: {
            "complete": len(found.get(method, set()) & expected),
            "expected": len(expected),
            "missing": [
                {"project": item[0], "use_case": item[1], "actor": item[2]}
                for item in sorted(expected - found.get(method, set()))
            ],
        }
        for method in selected_methods(config, None)
    }


def _latest_generation_mtime(output_root: Path) -> float:
    """Return the newest paired generation artifact timestamp, ignoring reports."""
    newest = 0.0
    for path, evidence in _iter_active_result_pairs(output_root):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("method"):
            newest = max(newest, path.stat().st_mtime, evidence.stat().st_mtime)
    return newest


def archive_outputs(config: dict, config_path: Path, label: str | None = None) -> Path:
    """Move active generation/evaluation artifacts into a versioned history folder.

    Expensive reusable preprocessing caches under ``baselines/_composites`` and
    ``baselines/_watermarked`` remain active.  The operation is refused when the
    destination already exists, so an earlier archive cannot be overwritten.
    """
    output_root = resolve_path(config_path, config["paths"]["output"])
    raw_label = label or dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw_label).strip("-.")
    if not safe_label:
        raise ValueError("Archive label must contain at least one letter or digit")
    history_root = output_root / "history"
    target = history_root / safe_label
    if target.exists():
        raise FileExistsError(f"Archive destination already exists: {target}")

    moves: list[tuple[Path, Path]] = []
    for directory in ("aucmr_full", "crest_without_dra"):
        source = output_root / directory
        if source.is_dir():
            moves.append((source, target / directory))

    baseline_root = output_root / "baselines"
    if baseline_root.is_dir():
        for source in sorted(baseline_root.iterdir()):
            if source.is_dir() and not source.name.startswith("_"):
                if any(source.rglob("*.json")):
                    moves.append((source, target / "baselines" / source.name))

    for name in EVALUATION_FILES:
        source = output_root / name
        if source.is_file():
            moves.append((source, target / name))

    if not moves:
        raise RuntimeError(f"No active generation/evaluation artifacts found under {output_root}")

    moved: list[dict[str, str]] = []
    for source, destination in moves:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        moved.append({
            "from": str(source.relative_to(output_root)),
            "to": str(destination.relative_to(output_root)),
        })

    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(config_path, target / "experiment.json")
    manifest = {
        "archived_at": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(),
        "label": safe_label,
        "source_output_root": str(output_root),
        "config_source": str(config_path),
        "moved": moved,
        "preserved_caches": [
            "baselines/_composites",
            "baselines/_watermarked",
        ],
    }
    (target / "archive_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(target)
    return target


def _evaluation_stage_current(
    output_root: Path, stage: str, latest_generation: float,
) -> tuple[bool, dict[str, Any]]:
    """Return whether one independently versioned evaluation stage is current."""
    if stage not in EVALUATION_STAGE_FILES:
        raise ValueError(f"Unknown evaluation stage: {stage}")
    required = [output_root / name for name in EVALUATION_STAGE_FILES[stage]]
    manifest_path = output_root / f"evaluation_manifest_{stage}.json"
    expected_version = (
        L1_EVALUATION_VERSION if stage == "l1" else L2_EVALUATION_VERSION
    )
    manifest: dict[str, Any] = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
    files_current = all(
        path.is_file() and path.stat().st_mtime >= latest_generation
        for path in required
    )
    version_current = manifest.get("version") == expected_version
    state = {
        "version": manifest.get("version"),
        "expected_version": expected_version,
        "files": {path.name: path.is_file() for path in required},
        "files_current": files_current,
        "version_current": version_current,
    }
    return files_current and version_current, state


def guide(config: dict, config_path: Path, json_output: bool = False) -> int:
    """Read-only workflow state machine; never invokes models or changes data."""
    actions: list[GuideAction] = []
    details: dict[str, Any] = {}
    details["hf_endpoint"] = os.environ.get("HF_ENDPOINT", "https://huggingface.co")
    projects = sorted({str(item["project"]) for item in config["experiment"]["use_cases"]})

    required_modules = (
        "numpy", "scipy", "sklearn", "torch", "transformers", "openai",
        "pymilvus", "minio", "json_repair", "PIL", "fitz", "peft", "accelerate",
    )
    missing_modules = [name for name in required_modules if importlib.util.find_spec(name) is None]
    details["missing_python_modules"] = missing_modules
    environment_blocked = bool(missing_modules)
    if missing_modules:
        installable = [name for name in missing_modules if name != "torch"]
        command = (
            "python -m pip install -r requirements-rocm.txt" if installable else
            "python -c \"import torch; print(torch.__version__, torch.version.hip, torch.cuda.is_available())\""
        )
        branches = ["安装后再次运行 python run.py guide"]
        if "torch" in missing_modules:
            branches.append("torch 缺失时先按服务器 ROCm 版本安装官方 ROCm PyTorch，不能安装默认 CUDA wheel")
        else:
            branches.append("requirements-rocm.txt 不会重装已可用的 ROCm PyTorch")
        actions.append(GuideAction(
            "environment", "BLOCKED", f"缺少 Python 模块：{', '.join(missing_modules)}",
            command, branches,
        ))
    else:
        try:
            import torch
            details["torch"] = {
                "version": torch.__version__, "hip": torch.version.hip,
                "gpu_available": torch.cuda.is_available(),
            }
            if not torch.version.hip or not torch.cuda.is_available():
                environment_blocked = True
                actions.append(GuideAction(
                    "environment", "BLOCKED", "当前 Python 未检测到可用的 ROCm GPU。",
                    "python -c \"import torch; print(torch.__version__, torch.version.hip, torch.cuda.is_available())\"",
                    ["hip 应非空且最后一项应为 True", "修复 ROCm 环境后再次运行 guide"],
                ))
        except Exception as exc:
            environment_blocked = True
            actions.append(GuideAction("environment", "BLOCKED", f"PyTorch 检查失败：{exc}"))

    env_names = sorted({
        str(model.get("api_key_env"))
        for model in config.get("models", {}).values() if model.get("api_key_env")
    })
    missing_env = [name for name in env_names if not os.environ.get(name)]
    details["missing_api_key_env"] = missing_env
    if missing_env:
        actions.append(GuideAction(
            "credentials", "BLOCKED", f"尚未设置环境变量：{', '.join(missing_env)}",
            "export AUCMR_DEEPSEEK_API_KEY='...' && export AUCMR_DASHSCOPE_API_KEY='...'",
            ["不要把真实密钥写入 experiment.json", "设置后再次运行 guide"],
        ))

    paths_ok = True
    documents = config.get("text_pipeline", {}).get("documents", [])
    for document in documents:
        path = resolve_path(config_path, document["pdf"])
        if not path.is_file():
            paths_ok = False
            actions.append(GuideAction("inputs", "BLOCKED", f"输入不存在：{path}"))
    for field in ("embedder", "linucb", "pca"):
        path = resolve_path(config_path, config["paths"][field])
        if not path.exists():
            paths_ok = False
            actions.append(GuideAction("inputs", "BLOCKED", f"资产不存在：{path}"))
    details["input_paths_ok"] = paths_ok

    page_image_states = {
        str(document["project"]): _page_image_status(config_path, document)
        for document in documents
        if str(document.get("project", "")) in projects
    } if paths_ok and importlib.util.find_spec("fitz") is not None else {}
    details["page_images"] = page_image_states
    incomplete_page_images = [
        project for project in projects
        if not page_image_states.get(project, {}).get("complete", False)
    ]

    if environment_blocked or missing_env or not paths_ok:
        next_action = actions[0]
    elif incomplete_page_images:
        project = incomplete_page_images[0]
        state = page_image_states[project]
        next_action = GuideAction(
            "page-images", "READY",
            f"页面截图不完整：{project}（{state.get('present_pages', 0)}/"
            f"{state.get('expected_pages', '?')}）。先从配置中的 PDF 补齐全部页面。",
            f"python run.py prepare-images --projects {project}",
            ["完成后再次运行 guide；已存在的完整页面会复用"],
        )
    else:
        image_states = {
            project: _collection_status(
                config, project, {"patch_dense", "page_num", "page_path", "file_name"},
            ) for project in projects
        }
        details["image_collections"] = image_states
        unreachable = [name for name, state in image_states.items() if not state.get("reachable")]
        missing_images = [
            name for name, state in image_states.items()
            if state.get("reachable") and not state.get("exists")
        ]
        wrong_schema_images = [
            name for name, state in image_states.items()
            if state.get("reachable") and state.get("exists") and not state.get("schema_ok")
        ]
        empty_images = [
            name for name, state in image_states.items()
            if state.get("reachable") and state.get("exists")
            and state.get("schema_ok") and state.get("rows", 0) <= 0
        ]
        if unreachable:
            next_action = GuideAction(
                "milvus", "BLOCKED", f"无法连接 Milvus：{image_states[unreachable[0]].get('error')}",
                None, ["确认 Milvus 已启动且监听 127.0.0.1:19530", "修复后再次运行 guide"],
            )
        elif missing_images:
            project = missing_images[0]
            next_action = GuideAction(
                "image-index", "READY",
                f"图像 collection 不存在：{project}。从已准备的页面图片安全创建。",
                f"python run.py build-image --projects {project}",
                [
                    "先逐页编码并每 10 页写入 MinIO；146 页可能运行数小时，期间不要重复执行",
                    "全部编码完成后才会打印 job ID；按提示监控 bulk-import，Completed 后再运行 guide",
                    "统一入口发现 collection 已存在时会拒绝追加，不会覆盖",
                ],
            )
        elif wrong_schema_images:
            project = wrong_schema_images[0]
            next_action = GuideAction(
                "image-index", "BLOCKED",
                f"图像 collection {project} 已有数据但 schema 不符合 AUCMR："
                f"缺少 {image_states[project].get('missing_fields', [])}。为避免破坏数据，不自动重建。",
                None, ["确认是否连到了正确的 Milvus 实例/collection", "需要重建时先备份并人工确认"],
            )
        elif empty_images:
            project = empty_images[0]
            job_file = resolve_path(
                config_path,
                str(config.get("paths", {}).get("image_work", "data/image_pipeline_jobs")),
            ) / f"{project}.json"
            job = {}
            if job_file.is_file():
                try:
                    job = json.loads(job_file.read_text(encoding="utf-8"))
                except Exception:
                    job = {}
            if job.get("status") == "failed" and job.get("recovery_command"):
                next_action = GuideAction(
                    "image-import-recovery", "READY",
                    f"图像构建已明确失败且 collection {project} 仍为空："
                    f"{job.get('error_type', 'Error')}: {job.get('error', '')}",
                    str(job["recovery_command"]),
                    ["该命令只允许删除 row_count=0 的 collection；有数据时仍会拒绝"],
                )
            else:
                status = str(job.get("status", "unknown"))
                next_action = GuideAction(
                    "image-import", "BLOCKED",
                    f"图像 collection {project} 已创建但 row_count=0；"
                    f"当前留档阶段={status}，等待或排查原任务。",
                    job.get("monitor_command"),
                    [
                        "encoding 阶段会逐页占用 GPU，尚未产生 Milvus job ID；不要另开重复构建",
                        "submitted 后按 monitor_command 等待 Completed",
                        "没有 job 留档时先确认 MinIO 对象和运行进程，不要直接重复导入",
                    ],
                )
        else:
            text_states = {}
            template = str(config["services"]["text_milvus"]["collection_template"])
            for project in projects:
                name = template.format(project=project)
                text_states[project] = _collection_status(
                    config, name,
                    {"token_dense", "seq_id", "chunk_id", "page_num", "page_path", "file_name", "text"},
                )
            details["text_collections"] = text_states
            invalid_text = [
                project for project, state in text_states.items()
                if not state.get("reachable") or not state.get("exists")
                or not state.get("schema_ok") or state.get("rows", 0) <= 0
            ]
            if invalid_text:
                project = invalid_text[0]
                collection = template.format(project=project)
                mineru_command = str(config["text_pipeline"]["mineru"].get("command", "mineru"))
                if shutil.which(mineru_command) is None:
                    next_action = GuideAction(
                        "mineru", "READY", "文本 collection 尚未建立，先安装 CPU 版 MinerU。",
                        "uv pip install \"mineru[pipeline]\"",
                        [
                            "安装后执行 mineru --version",
                            "再检查 ROCm：python -c \"import torch; print(torch.version.hip, torch.cuda.is_available())\"",
                            "然后再次运行 python run.py guide",
                        ],
                    )
                else:
                    next_action = GuideAction(
                        "text-index", "READY", f"MinerU 已就绪，下一步构建 {collection}。",
                        f"python run.py build-text --projects {project}",
                        [
                            "若提示 collection 已存在，先运行 guide 复核，不要加 --recreate-text",
                            "中断后可重复同一命令；caption_cache 会复用",
                        ],
                    )
            else:
                cache_root = resolve_path(config_path, config["paths"]["vendor_cache"]) / "vdocrag"
                missing_cache = [project for project in projects if not (cache_root / f"{project}.npz").is_file()]
                details["vdocrag_cache_missing"] = missing_cache
                if missing_cache:
                    project = missing_cache[0]
                    next_action = GuideAction(
                        "vdocrag-index", "READY", "文本和图像索引已就绪；预构建 VDocRAG 页面向量。",
                        f"python run.py build-vdocrag --projects {project}",
                        [
                            "首次会从 Hugging Face 下载权重；中断后会复用已下载分片",
                            "AMD ROCm 使用 eager attention，不需要安装 flash_attn",
                            "完成后再次运行 guide",
                        ],
                    )
                else:
                    output_root = resolve_path(config_path, config["paths"]["output"])
                    counts = _result_counts(config, output_root)
                    details["generation_results"] = counts
                    pair = [FULL, WITHOUT_DRA]
                    if any(counts[name]["complete"] < counts[name]["expected"] for name in pair):
                        cohort_options = (
                            " --paper-cohort --generation-workers 14"
                            if config.get("experiment", {}).get("_paper_cohort_selected")
                            else ""
                        )
                        next_action = GuideAction(
                            "generate-crest", "READY", "基础索引齐全；以冻结的 v4 w/o 证据卡成对重写 AUCMR v5 与 w/o cross-slot。",
                            'python run.py generate --resume --reuse-evidence'
                            + cohort_options
                            + ' --methods "AUCMR (CREST full)" "CREST w/o cross-slot"',
                            ["完成或中断后运行 guide 查看结果缺口"],
                        )
                    else:
                        baseline_order = [
                            "LVLM-Direct", "TextRAG-Elaborate", "VDocRAG-Elaborate",
                            "ViDoRAG-Elaborate", "ReAct-Elaborate",
                        ]
                        incomplete = next((
                            name for name in baseline_order
                            if counts[name]["complete"] < counts[name]["expected"]
                        ), None)
                        if incomplete:
                            next_action = GuideAction(
                                "generate-baseline", "READY",
                                f"下一项生成 {incomplete}（{counts[incomplete]['complete']}/{counts[incomplete]['expected']} 已完成）。",
                                f'python run.py generate --resume --methods "{incomplete}"',
                                ["完成或中断后再次运行 guide"],
                            )
                        else:
                            latest_generation = _latest_generation_mtime(output_root)
                            evaluation_config = config.get("experiment", {}).get("evaluation", {})
                            l1_workers = max(1, int(evaluation_config.get("l1_workers", 1)))
                            l2_workers = max(1, int(evaluation_config.get("l2_workers", 1)))
                            l1_current, l1_state = _evaluation_stage_current(
                                output_root, "l1", latest_generation
                            )
                            l2_current, l2_state = _evaluation_stage_current(
                                output_root, "l2", latest_generation
                            )
                            details["evaluation"] = {
                                "l1": l1_state,
                                "l2": l2_state,
                            }
                            if not l1_current:
                                method_count = len(selected_methods(config, None))
                                use_case_count = len(_configured_use_case_ids(config))
                                cohort_option = (
                                    " --paper-cohort"
                                    if config.get("experiment", {}).get("_paper_cohort_selected")
                                    else ""
                                )
                                next_action = GuideAction(
                                    "evaluate-l1", "READY",
                                    f"{method_count} 种方法 × {use_case_count} 个候选用例结果齐全；"
                                    "先运行最新 L1 引用、联合蕴含与槽位适配评测。",
                                    "python run.py evaluate-l1" + cohort_option,
                                    [
                                        f"评测版本：{L1_EVALUATION_VERSION}",
                                        f"并发度：experiment.evaluation.l1_workers={l1_workers}",
                                        "LVLM-Direct 按论文定义不进入 L1，因此不会发起无效 NLI 调用",
                                        "N_all、N_cite、N_ent 使用同一模型生成断言总体",
                                        "N_ent 要求联合引用证据蕴含断言，且该断言实质适配当前 Cockburn 槽位；卡片方法仅给卡片文本，图像方法仅给已引用页面图像",
                                        "复合断言仍按全部有效引用证据的并集联合判定，不要求单证据独立蕴含",
                                        "输出到 outputs/evaluation_audit_l1.json、aggregated_metrics_l1.json 和 table_l1.tex",
                                        "裁判失败不会触发生成；修复接口后重复 evaluate-l1",
                                    ],
                                )
                            elif not l2_current:
                                cohort_option = (
                                    " --paper-cohort"
                                    if config.get("experiment", {}).get("_paper_cohort_selected")
                                    else ""
                                )
                                next_action = GuideAction(
                                    "evaluate-l2", "READY",
                                    "L1 已按最新口径完成；单独运行 L2 的 R1--R7 一致性裁判。",
                                    "python run.py evaluate-l2" + cohort_option,
                                    [
                                        f"评测版本：{L2_EVALUATION_VERSION}",
                                        f"并发度：experiment.evaluation.l2_workers={l2_workers}",
                                        "不会重新调用 L1/NLI 裁判，也不会覆盖 L1 结果",
                                        "输出到 outputs/evaluation_audit_l2.json、aggregated_metrics_l2.json 和 table_l2_consistency.tex",
                                        "裁判失败后可重复 evaluate-l2；生成结果不会重跑",
                                    ],
                                )
                            else:
                                next_action = GuideAction(
                                    "complete", "DONE", "自动生成、最新 L1 与独立 L2 评测全部完成。",
                                    None,
                                    [
                                        "检查 outputs/evaluation_cohort_l1.json 与 evaluation_cohort_l2.json",
                                        "检查 outputs/evaluation_audit_l1.json 与 evaluation_audit_l2.json",
                                        "论文表在 outputs/table_l1.tex 与 table_l2_consistency.tex",
                                        "L3/L4 仍需人工标注与专家评审",
                                    ],
                                )

    payload = {"next": asdict(next_action), "checks": details}
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"[{next_action.status}] 阶段：{next_action.stage}")
        print(next_action.message)
        if next_action.command:
            print(f"\n下一条命令：\n{next_action.command}")
        if next_action.branches:
            print("\n完成条件 / 异常分支：")
            for branch in next_action.branches:
                print(f"- {branch}")
        guide_command = (
            "python run.py guide --paper-cohort"
            if config.get("experiment", {}).get("_paper_cohort_selected")
            else "python run.py guide"
        )
        print(f"\n完成上述动作后，再运行：{guide_command}")
    return 0


def safe_stem(actor: str, use_case: str) -> str:
    return re.sub(r"[^\w\-.]+", "", f"{actor}_{use_case}".replace(" ", "_")) or "unnamed"


def evidence_payload(uc: Any, method: str) -> dict:
    refs = {
        ref for slot in uc.slots.values() for claim in slot.claims for ref in claim.evidence_refs
    }
    cards = {card.card_id: card for card in uc.all_evidence_cards}
    return {
        "use_case_name": uc.use_case_name,
        "actor": uc.actor,
        "method": method,
        "evidence_summary": uc.evidence_summary,
        "referenced_card_ids": sorted(refs),
        "evidence_cards": [card.to_dict() for card in cards.values()],
    }


def write_crest_result(uc: Any, method: str, output_root: Path, project: str) -> str:
    method_dir = "aucmr_full" if method == FULL else "crest_without_dra"
    out_dir = output_root / method_dir / project
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = safe_stem(uc.actor, uc.use_case_name)
    main_path = out_dir / f"{stem}.json"
    ev_path = out_dir / f"{stem}-evidence.json"
    main_data = uc.to_dict()
    main_data["method"] = method
    main_data["generation_version"] = CREST_GENERATION_VERSION
    artifact_revision_id = uuid.uuid4().hex
    main_data["artifact_revision_id"] = artifact_revision_id
    evidence_data = evidence_payload(uc, method)
    evidence_data["generation_version"] = CREST_GENERATION_VERSION
    evidence_data["artifact_revision_id"] = artifact_revision_id
    # A killed parallel worker must not leave a truncated JSON that a later
    # --resume invocation mistakes for a completed result.
    staged = []
    for path, payload in ((main_path, main_data), (ev_path, evidence_data)):
        temp_path = path.with_name(
            f".{path.name}.tmp-{os.getpid()}-{threading.get_ident()}"
        )
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        staged.append((temp_path, path))
    # Evidence first, main file last as the commit marker.  If interruption
    # occurs between the two replaces, the revision IDs make the pair invalid.
    os.replace(staged[1][0], staged[1][1])
    os.replace(staged[0][0], staged[0][1])
    return str(main_path)


def load_crest_result(main_path: Path, evidence_path: Path) -> Any:
    """Rehydrate a saved CREST artifact for a DRA-only revision run."""
    from crest.schemas import (
        Claim, EvidenceCard, QualityFlag, RevisionRecord, SlotContent, UseCase,
    )

    main = json.loads(main_path.read_text(encoding="utf-8"))
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    # Saved artifacts keep per-slot pre-constraint candidates at the document
    # root so final Detail-UC slots remain schema-clean.  A DRA-only reload
    # must restore that audit state onto SlotContent; otherwise serializing the
    # revised UC preserves only slots that DRA happened to touch, and L1's
    # N_all no longer represents the delivered assertion population.
    audit_slots = (
        (main.get("citation_audit") or {}).get("pre_constraint_slots") or {}
    )
    slots = {}
    for slot_name, raw_slot in (main.get("slots") or {}).items():
        coverage = raw_slot.get("coverage_state") or {}
        audit_draft = audit_slots.get(slot_name)
        slots[slot_name] = SlotContent(
            slot=raw_slot.get("slot", slot_name),
            claims=[Claim(
                content=claim.get("content", ""),
                evidence_refs=list(claim.get("evidence_refs") or []),
                step_number=claim.get("step_number"),
                is_cross_page=bool(claim.get("is_cross_page", False)),
            ) for claim in (raw_slot.get("claims") or [])],
            raw_text=raw_slot.get("raw_text", ""),
            evidence_card_ids=list(raw_slot.get("evidence_card_ids") or []),
            scope_contract=dict(raw_slot.get("scope_contract") or {}),
            covered_aspect_ids=list(coverage.get("covered_aspect_ids") or []),
            missing_aspects=list(coverage.get("missing_aspects") or []),
            aspect_status_known="coverage_state" in raw_slot,
            pre_constraint_draft=(
                dict(audit_draft) if isinstance(audit_draft, dict) else {}
            ),
        )
    return UseCase(
        use_case_name=main.get("use_case_name", ""),
        actor=main.get("actor", ""),
        slots=slots,
        quality_flags=[QualityFlag(**flag) for flag in (main.get("quality_flags") or [])],
        revision_history=[
            RevisionRecord(**record) for record in (main.get("revision_history") or [])
        ],
        all_evidence_cards=[
            EvidenceCard(**card) for card in (evidence.get("evidence_cards") or [])
        ],
        evidence_summary=dict(main.get("evidence_summary") or {}),
        runtime_stats=dict(main.get("runtime_stats") or {}),
    )


def load_v4_seed_evidence_cards(output_root: Path, item: dict) -> tuple[list[Any], Path]:
    """Load one frozen paired v4 w/o-DRA card pool for v5 re-authoring.

    The validation is deliberately strict: reuse is an experimental acceleration
    only when every v5 paired result starts from the same archived, pre-DRA v4
    pool.  Missing/mismatched artifacts fail loudly rather than silently falling
    back to a newly retrieved (and non-comparable) pool.
    """
    from crest.schemas import EvidenceCard

    project = str(item["project"])
    stem = safe_stem(str(item["actor"]), str(item["use_case"]))
    source_dir = output_root / DEFAULT_REUSE_EVIDENCE_SUBDIR / project
    main_path = source_dir / f"{stem}.json"
    evidence_path = source_dir / f"{stem}-evidence.json"
    if not main_path.is_file() or not evidence_path.is_file():
        raise FileNotFoundError(
            "Frozen v4 w/o-DRA evidence is required for --reuse-evidence but is missing: "
            f"{main_path} / {evidence_path}"
        )
    try:
        main = json.loads(main_path.read_text(encoding="utf-8"))
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot read frozen v4 evidence for {project}/{stem}: {exc}") from exc
    for label, payload in (("main", main), ("evidence", evidence)):
        if payload.get("generation_version") != V4_EVIDENCE_REUSE_GENERATION_VERSION:
            raise RuntimeError(
                f"{label} source for {project}/{stem} is not the paired v4 generation "
                f"({payload.get('generation_version')!r})"
            )
        if payload.get("method") != WITHOUT_DRA:
            raise RuntimeError(
                f"{label} source for {project}/{stem} is not {WITHOUT_DRA!r}"
            )
    if main.get("actor") != item["actor"] or main.get("use_case_name") != item["use_case"]:
        raise RuntimeError(f"Frozen v4 source identity mismatch for {project}/{stem}")
    if evidence.get("actor") != item["actor"] or evidence.get("use_case_name") != item["use_case"]:
        raise RuntimeError(f"Frozen v4 evidence identity mismatch for {project}/{stem}")
    if main.get("artifact_revision_id") != evidence.get("artifact_revision_id"):
        raise RuntimeError(f"Frozen v4 main/evidence revision mismatch for {project}/{stem}")

    raw_cards = evidence.get("evidence_cards")
    if not isinstance(raw_cards, list) or not raw_cards:
        raise RuntimeError(f"Frozen v4 source contains no evidence cards for {project}/{stem}")
    try:
        cards = [EvidenceCard(**raw) for raw in raw_cards if isinstance(raw, dict)]
    except TypeError as exc:
        raise RuntimeError(f"Invalid frozen v4 evidence card schema for {project}/{stem}: {exc}") from exc
    card_ids = [card.card_id for card in cards]
    if not cards or any(not card_id for card_id in card_ids) or len(card_ids) != len(set(card_ids)):
        raise RuntimeError(f"Frozen v4 evidence cards have missing/duplicate IDs for {project}/{stem}")
    return cards, evidence_path


def run_revision(
    config: dict,
    config_path: Path,
    resume: bool = False,
    revision_workers: int = 1,
) -> None:
    """Run only DRA from saved w/o artifacts, sharing one local retrieval stack.

    Remote LLM/LVLM waits are independent across use cases.  The Jina/LinUCB/PCA
    retrieval path is local and stateful, so it is initialized once and guarded
    by a lock; this makes DRA repair parallel without spawning duplicate Jina
    model instances.
    """
    from services.model_registry import registry
    from crest import CRESTOrchestrator
    from crest.agents import cross_slot_revision

    output_root = resolve_path(config_path, config["paths"]["output"])
    items = list(config["experiment"]["use_cases"])
    retrieval_lock = threading.Lock()
    contexts: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
    for item in items:
        key = (str(item["project"]), tuple(item.get("file_names") or []))
        if key in contexts:
            continue
        registry.set_collection(item["project"], item.get("file_names"))
        base_adapter = registry.get_retrieval_adapter()

        def locked_adapter(queries, file_names, topk, _adapter=base_adapter):
            with retrieval_lock:
                return _adapter(queries, file_names, topk)

        contexts[key] = {
            "retrieval_fn": locked_adapter,
            "file_names": list(registry.file_names),
            "topk": registry.topk,
            "total_doc_pages": registry.total_pages,
        }

    def revise_one(item: dict) -> None:
        stem = safe_stem(item["actor"], item["use_case"])
        source_dir = output_root / "crest_without_dra" / item["project"]
        main_path = source_dir / f"{stem}.json"
        evidence_path = source_dir / f"{stem}-evidence.json"
        target_path = output_root / "aucmr_full" / item["project"] / f"{stem}.json"
        target_evidence = target_path.with_name(f"{stem}-evidence.json")
        if resume and target_path.exists() and target_evidence.exists() and _result_pair_is_valid(
            target_path, target_evidence,
        ):
            print(f"SKIP complete: {item['actor']} / {item['use_case']}")
            return
        if not main_path.exists() or not evidence_path.exists():
            raise FileNotFoundError(
                f"DRA source artifact missing for {item['actor']} / {item['use_case']}: "
                f"{main_path} and {evidence_path} are required"
            )

        key = (str(item["project"]), tuple(item.get("file_names") or []))
        context = contexts[key]
        provider = registry.get_model_provider(think=False)
        full = load_crest_result(main_path, evidence_path)
        dra_started = time.perf_counter()
        dra_stats_before = provider.stats_snapshot()
        full = cross_slot_revision(
            actor=item["actor"], use_case_name=item["use_case"], uc=full,
            file_names=context["file_names"], topk=context["topk"],
            retrieval_fn=context["retrieval_fn"], provider=provider,
            max_rounds=int(config["algorithm"].get("dra_max_rounds", 2)),
        )
        dra_stats = provider.stats_delta(dra_stats_before, provider.stats_snapshot())
        base_runtime = dict(full.runtime_stats or {})
        base_runtime["dra_elapsed_seconds"] = round(time.perf_counter() - dra_started, 3)
        base_runtime["elapsed_seconds"] = round(
            float(base_runtime.get("elapsed_seconds", 0.0))
            + base_runtime["dra_elapsed_seconds"], 3,
        )
        for stat_key, value in dra_stats.items():
            base_runtime[f"dra_{stat_key}"] = value
            if stat_key in {"llm_calls", "lvlm_calls", "llm_seconds", "lvlm_seconds"}:
                base_runtime[stat_key] = round(
                    float(base_runtime.get(stat_key, 0.0)) + float(value), 3
                )
        base_runtime["dra_revision_workers"] = revision_workers
        full.runtime_stats = base_runtime
        orchestrator = CRESTOrchestrator(
            retrieval_fn=context["retrieval_fn"], file_names=context["file_names"],
            topk=context["topk"], provider=provider,
            total_doc_pages=context["total_doc_pages"], dra_max_rounds=0,
        )
        orchestrator._build_evidence_summary(full)
        print(write_crest_result(full, FULL, output_root, item["project"]))

    workers = max(1, min(int(revision_workers), len(items) or 1))
    _ = registry.llm_client
    if workers == 1:
        for item in items:
            revise_one(item)
        return

    print(
        f"DRA revision: {len(items)} use cases, workers={workers}, "
        "one shared Jina embedder"
    )
    failures = []
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="dra-revision") as pool:
        future_map = {pool.submit(revise_one, item): item for item in items}
        for future in as_completed(future_map):
            item = future_map[future]
            try:
                future.result()
            except Exception as exc:
                failures.append({
                    "project": item["project"], "actor": item["actor"],
                    "use_case": item["use_case"], "error": str(exc),
                })
    if failures:
        raise RuntimeError(
            "Parallel DRA revision failed for one or more use cases: "
            + json.dumps(failures, ensure_ascii=False)
        )


def run_generation(
    config: dict,
    config_path: Path,
    methods: list[str],
    resume: bool = False,
    reuse_evidence: bool = False,
) -> None:
    output_root = resolve_path(config_path, config["paths"]["output"])
    use_cases = config["experiment"]["use_cases"]
    missing_by_method: dict[str, set[tuple[str, str, str]]] = {}
    if resume:
        counts = _result_counts(config, output_root)
        missing_by_method = {
            method: {
                (str(item["project"]), str(item["use_case"]), str(item["actor"]))
                for item in counts.get(method, {}).get("missing", [])
            }
            for method in methods
        }

    def needs(method: str, item: dict) -> bool:
        if not resume:
            return True
        identity = (str(item["project"]), str(item["use_case"]), str(item["actor"]))
        return identity in missing_by_method.get(method, {identity})

    crest_methods = [method for method in methods if method in {FULL, WITHOUT_DRA}]
    if crest_methods:
        from services.model_registry import registry
        from crest import CRESTOrchestrator
        from crest.agents import cross_slot_revision

        # Build one retrieval closure per (project, file filter) while keeping a
        # single shared Jina/LinUCB/PCA instance.  The closures capture their own
        # Milvus retriever; a lock serializes the stateful local retrieval path,
        # while remote LLM/LVLM waits across use cases remain concurrent.
        retrieval_lock = threading.Lock()
        contexts: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
        for item in use_cases:
            key = (str(item["project"]), tuple(item.get("file_names") or []))
            if key in contexts:
                continue
            registry.set_collection(item["project"], item.get("file_names"))
            base_adapter = registry.get_retrieval_adapter()

            def locked_adapter(queries, file_names, topk, _adapter=base_adapter):
                with retrieval_lock:
                    return _adapter(queries, file_names, topk)

            contexts[key] = {
                "retrieval_fn": locked_adapter,
                "file_names": list(registry.file_names),
                "topk": registry.topk,
                "total_doc_pages": registry.total_pages,
            }

        def run_crest_item(item: dict) -> None:
            needed_crest = [method for method in crest_methods if needs(method, item)]
            if not needed_crest:
                print(f"SKIP complete: {item['actor']} / {item['use_case']}")
                return
            key = (str(item["project"]), tuple(item.get("file_names") or []))
            context = contexts[key]
            retrieval_fn = context["retrieval_fn"]
            file_names = context["file_names"]
            topk = context["topk"]
            total_doc_pages = context["total_doc_pages"]
            provider = registry.get_model_provider(think=False)
            seed_cards = None
            seed_path = None
            if reuse_evidence:
                seed_cards, seed_path = load_v4_seed_evidence_cards(output_root, item)
                print(
                    f"REUSE v4 w/o-DRA evidence: {item['actor']} / {item['use_case']} "
                    f"({len(seed_cards)} cards from {seed_path})"
                )

            def author_base(orchestrator: Any) -> Any:
                if seed_cards is None:
                    return orchestrator.elaborate(item["actor"], item["use_case"])
                base = orchestrator.elaborate_from_evidence_cards(
                    item["actor"], item["use_case"], seed_cards,
                )
                base.runtime_stats["evidence_reuse_source"] = str(seed_path)
                return base

            if set(needed_crest) == {FULL, WITHOUT_DRA}:
                orchestrator = CRESTOrchestrator(
                    retrieval_fn=retrieval_fn,
                    file_names=file_names,
                    topk=topk,
                    provider=provider,
                    total_doc_pages=total_doc_pages,
                    dra_max_rounds=0,
                )
                without_dra = author_base(orchestrator)
                print(write_crest_result(without_dra, WITHOUT_DRA, output_root, item["project"]))
                full = copy.deepcopy(without_dra)
                is_unresolvable = any(
                    flag.flag_type == "unresolvable_use_case" for flag in full.quality_flags
                )
                if not is_unresolvable:
                    dra_started = time.perf_counter()
                    dra_stats_before = provider.stats_snapshot()
                    full = cross_slot_revision(
                        actor=item["actor"], use_case_name=item["use_case"], uc=full,
                        file_names=file_names, topk=topk,
                        retrieval_fn=retrieval_fn, provider=provider,
                        max_rounds=int(config["algorithm"].get("dra_max_rounds", 2)),
                    )
                    dra_stats = provider.stats_delta(
                        dra_stats_before, provider.stats_snapshot()
                    )
                    base_runtime = dict(full.runtime_stats or {})
                    base_runtime["dra_elapsed_seconds"] = round(
                        time.perf_counter() - dra_started, 3
                    )
                    base_runtime["elapsed_seconds"] = round(
                        float(base_runtime.get("elapsed_seconds", 0.0))
                        + base_runtime["dra_elapsed_seconds"],
                        3,
                    )
                    for key, value in dra_stats.items():
                        base_runtime[f"dra_{key}"] = value
                        if key in {"llm_calls", "lvlm_calls", "llm_seconds", "lvlm_seconds"}:
                            base_runtime[key] = round(
                                float(base_runtime.get(key, 0.0)) + float(value), 3
                            )
                    full.runtime_stats = base_runtime
                orchestrator._build_evidence_summary(full)
                print(write_crest_result(full, FULL, output_root, item["project"]))
            else:
                method = needed_crest[0]
                orchestrator = CRESTOrchestrator(
                    retrieval_fn=retrieval_fn, file_names=file_names,
                    topk=topk, provider=provider,
                    total_doc_pages=total_doc_pages, dra_max_rounds=0,
                )
                uc = author_base(orchestrator)
                if method == FULL and not any(
                    flag.flag_type == "unresolvable_use_case" for flag in uc.quality_flags
                ):
                    dra_started = time.perf_counter()
                    dra_stats_before = provider.stats_snapshot()
                    uc = cross_slot_revision(
                        actor=item["actor"], use_case_name=item["use_case"], uc=uc,
                        file_names=file_names, topk=topk,
                        retrieval_fn=retrieval_fn, provider=provider,
                        max_rounds=int(config["algorithm"].get("dra_max_rounds", 2)),
                    )
                    dra_stats = provider.stats_delta(
                        dra_stats_before, provider.stats_snapshot()
                    )
                    base_runtime = dict(uc.runtime_stats or {})
                    base_runtime["dra_elapsed_seconds"] = round(
                        time.perf_counter() - dra_started, 3
                    )
                    base_runtime["elapsed_seconds"] = round(
                        float(base_runtime.get("elapsed_seconds", 0.0))
                        + base_runtime["dra_elapsed_seconds"], 3,
                    )
                    for key, value in dra_stats.items():
                        base_runtime[f"dra_{key}"] = value
                        if key in {"llm_calls", "lvlm_calls", "llm_seconds", "lvlm_seconds"}:
                            base_runtime[key] = round(
                                float(base_runtime.get(key, 0.0)) + float(value), 3
                            )
                    uc.runtime_stats = base_runtime
                orchestrator._build_evidence_summary(uc)
                print(write_crest_result(uc, method, output_root, item["project"]))

        generation_workers = max(
            1, min(int(config.get("experiment", {}).get("generation_workers", 1)), len(use_cases) or 1)
        )
        # Initialize the shared HTTP client before worker threads start, just as
        # the retrieval-context build above initializes the single Jina stack.
        _ = registry.llm_client
        if generation_workers == 1:
            for item in use_cases:
                run_crest_item(item)
        else:
            print(
                f"CREST generation: {len(use_cases)} use cases, workers={generation_workers}, "
                "one shared Jina embedder"
            )
            failures = []
            with ThreadPoolExecutor(
                max_workers=generation_workers, thread_name_prefix="crest-use-case"
            ) as pool:
                future_map = {pool.submit(run_crest_item, item): item for item in use_cases}
                for future in as_completed(future_map):
                    item = future_map[future]
                    try:
                        future.result()
                    except Exception as exc:
                        failures.append({
                            "project": item["project"], "actor": item["actor"],
                            "use_case": item["use_case"], "error": str(exc),
                        })
            if failures:
                raise RuntimeError(
                    "Parallel CREST generation failed for one or more use cases: "
                    + json.dumps(failures, ensure_ascii=False)
                )

    baseline_methods = [method for method in methods if method in BASELINES]
    if baseline_methods:
        from eval.baselines import get_all_baselines
        runners = get_all_baselines(str(output_root / "baselines"), baseline_methods)
        for baseline in runners:
            for item in use_cases:
                if not needs(baseline.name, item):
                    print(f"SKIP complete: {baseline.name} / {item['actor']} / {item['use_case']}")
                    continue
                print(baseline.run_and_save(
                    item["use_case"], item["actor"], item["project"], item.get("file_names")
                ))


def _write_evaluation_outputs(
    config: dict,
    runner: Any,
    results: dict,
    report_root: Path,
    stage: str,
) -> Path:
    """Evaluate exactly one stage and write versioned, non-overlapping reports."""
    from eval.run_experiments import ExperimentRunner, generate_latex_table_l1, generate_latex_table_l3

    if stage not in {"l1", "l2"}:
        raise ValueError(f"Evaluation stage must be 'l1' or 'l2', got {stage!r}")
    report_root.mkdir(parents=True, exist_ok=True)
    stage_results = results
    if stage == "l1":
        # LVLM-Direct intentionally has no stable source identifiers and is
        # excluded from the paper L1 table.  Do not pay for NLI calls whose
        # outputs can never enter L1.
        stage_results = {
            key: paths for key, paths in results.items()
            if not key or key[0] != "LVLM-Direct"
        }
    evaluation = config.get("experiment", {}).get("evaluation", {})
    workers = int(evaluation.get(f"{stage}_workers", 1))
    method_metrics = runner.compute_all_metrics(
        stage_results,
        run_nli=stage == "l1",
        run_l2=stage == "l2",
        max_workers=max(1, workers),
    )
    complete_audit = runner.evaluation_audit(method_metrics)
    stage_audit = {
        method: [
            {
                "use_case": row["use_case"],
                "actor": row["actor"],
                stage: row[stage],
            }
            for row in rows
        ]
        for method, rows in complete_audit.items()
    }
    audit_path = report_root / f"evaluation_audit_{stage}.json"
    audit_path.write_text(
        json.dumps(stage_audit, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    complete_aggregated = runner.aggregate_and_report(method_metrics)
    allowed_prefix = "l1_" if stage == "l1" else "l2_"
    aggregated = {
        method: {
            key: value for key, value in metrics.items()
            if key in {"method", "n_ucs"}
            or key.startswith(allowed_prefix)
            or (stage == "l2" and key.startswith("consistency_"))
        }
        for method, metrics in complete_aggregated.items()
    }
    report = report_root / f"aggregated_metrics_{stage}.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(aggregated, ensure_ascii=False, indent=2), encoding="utf-8")
    if stage == "l1":
        (report_root / "table_l1.tex").write_text(
            generate_latex_table_l1(aggregated), encoding="utf-8",
        )
        version = L1_EVALUATION_VERSION
    else:
        (report_root / "table_l2_consistency.tex").write_text(
            generate_latex_table_l3(aggregated), encoding="utf-8",
        )
        version = L2_EVALUATION_VERSION
    manifest = {
        "stage": stage,
        "version": version,
        "created_at": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(),
        "run_nli": stage == "l1",
        "run_l2_judge": stage == "l2",
        "result_pairs": len(stage_results),
        "max_workers": max(1, workers),
        "excluded_methods": ["LVLM-Direct"] if stage == "l1" else [],
        "audit": audit_path.name,
        "aggregated_metrics": report.name,
        "judge_models": (
            {
                "text": config.get("models", {}).get("nli_judge", {}).get("model_name"),
                "vision": config.get("models", {}).get("nli_vision_judge", {}).get("model_name"),
            }
            if stage == "l1"
            else {
                "consistency": config.get("models", {}).get("l2_judge", {}).get("model_name"),
            }
        ),
    }
    (report_root / f"evaluation_manifest_{stage}.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(report)
    return report


def run_evaluation(config: dict, config_path: Path, stage: str) -> None:
    from eval.run_experiments import ExperimentRunner

    if stage not in {"l1", "l2"}:
        raise ValueError(f"Evaluation stage must be 'l1' or 'l2', got {stage!r}")
    output_root = resolve_path(config_path, config["paths"]["output"])
    runner = ExperimentRunner(output_dir=str(output_root), baseline_names=[])
    results = {}
    for directory in ACTIVE_GENERATION_DIRS:
        active_dir = output_root / directory
        if active_dir.is_dir():
            results.update(runner.collect_crest_results(str(active_dir)))
    methods = selected_methods(config, None)
    results, cohort = runner.select_paper_cohort(results, methods)
    (output_root / f"evaluation_cohort_{stage}.json").write_text(
        json.dumps(cohort, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    _write_evaluation_outputs(config, runner, results, output_root, stage)


def run_partial_evaluation(
    config: dict,
    config_path: Path,
    methods: list[str],
    anchor_method: str = "ViDoRAG-Elaborate",
    label: str | None = None,
    stage: str = "both",
) -> None:
    """Evaluate a reproducible paired cohort anchored on an incomplete method.

    This is deliberately separate from the formal ``evaluate`` command.  Every
    method is evaluated on the same use-case identities available for the
    anchor at invocation time, and both JSON pairs are copied before any judge
    call.  A background generator can therefore continue without changing this
    backup experiment halfway through evaluation.
    """
    from eval.run_experiments import ExperimentRunner

    if stage not in {"l1", "l2", "both"}:
        raise ValueError(f"Unknown partial evaluation stage: {stage}")
    if anchor_method not in methods:
        raise ValueError(
            f"Partial-evaluation anchor {anchor_method!r} is not in selected methods: {methods}"
        )
    output_root = resolve_path(config_path, config["paths"]["output"])
    runner = ExperimentRunner(output_dir=str(output_root), baseline_names=[])
    all_results = {}
    for directory in ACTIVE_GENERATION_DIRS:
        active_dir = output_root / directory
        if active_dir.is_dir():
            all_results.update(runner.collect_crest_results(str(active_dir)))

    full_results = {
        key[1:]: paths for key, paths in all_results.items() if key[0] == FULL
    }
    if not full_results:
        raise RuntimeError("Cannot define a partial paper cohort: AUCMR full results are missing")

    valid_identities = set()
    rejected = []
    for identity, (uc_path, _ev_path) in full_results.items():
        with open(uc_path, encoding="utf-8") as handle:
            data = json.load(handle)
        flags = data.get("quality_flags", []) or []
        if any(flag.get("flag_type") == "unresolvable_use_case" for flag in flags):
            rejected.append(identity)
        else:
            valid_identities.add(identity)

    identities_by_method = {
        method: {key[1:] for key in all_results if key[0] == method}
        for method in methods
    }
    anchor_available = valid_identities & identities_by_method.get(anchor_method, set())
    if not anchor_available:
        raise RuntimeError(
            f"No valid completed results are available for partial anchor {anchor_method}"
        )
    paired_identities = set(anchor_available)
    for method in methods:
        paired_identities &= identities_by_method.get(method, set())
    if not paired_identities:
        raise RuntimeError("The selected methods have no common completed use-case cohort")

    if label:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", label):
            raise ValueError("Partial evaluation label may contain only letters, digits, '.', '_' and '-'")
        run_label = label
    else:
        run_label = "vidorag_partial_" + dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_root = output_root / "backup_evaluations" / run_label
    if report_root.exists():
        raise FileExistsError(f"Backup evaluation directory already exists: {report_root}")
    snapshot_root = report_root / "inputs"

    selected = {
        key: paths
        for key, paths in all_results.items()
        if key[0] in methods and key[1:] in paired_identities
    }
    snapshot_results = {}
    snapshot_files = []
    for key, (uc_path_value, ev_path_value) in sorted(selected.items()):
        method, project, _use_case, _actor = key
        method_dir = re.sub(r"[^A-Za-z0-9._-]+", "_", method).strip("_") or "method"
        destination = snapshot_root / method_dir / project
        destination.mkdir(parents=True, exist_ok=True)
        uc_path = Path(uc_path_value)
        ev_path = Path(ev_path_value)
        uc_copy = destination / uc_path.name
        ev_copy = destination / ev_path.name
        shutil.copy2(uc_path, uc_copy)
        shutil.copy2(ev_path, ev_copy)
        snapshot_results[key] = (str(uc_copy), str(ev_copy))
        snapshot_files.extend([
            str(uc_copy.relative_to(report_root)),
            str(ev_copy.relative_to(report_root)),
        ])

    configured_identities = _configured_use_case_ids(config)
    per_project = {}
    for project, _use_case, _actor in paired_identities:
        per_project[project] = per_project.get(project, 0) + 1
    manifest = {
        "experiment_type": "paired_partial_backup",
        "created_at": dt.datetime.now().astimezone().isoformat(),
        "anchor_method": anchor_method,
        "selection_rule": (
            "AUCMR-valid use cases completed by the anchor and every selected method "
            "at snapshot time"
        ),
        "is_formal_full_cohort": paired_identities == valid_identities,
        "configured_use_case_count": len(configured_identities),
        "aucmr_valid_count": len(valid_identities),
        "anchor_available_count": len(anchor_available),
        "paired_count": len(paired_identities),
        "paired_count_by_project": dict(sorted(per_project.items())),
        "methods": methods,
        "available_count_by_method": {
            method: len(valid_identities & identities_by_method.get(method, set()))
            for method in methods
        },
        "paired_use_cases": [
            {"project": item[0], "use_case": item[1], "actor": item[2]}
            for item in sorted(paired_identities)
        ],
        "anchor_cases_excluded_by_other_methods": [
            {"project": item[0], "use_case": item[1], "actor": item[2]}
            for item in sorted(anchor_available - paired_identities)
        ],
        "aucmr_rejected_use_cases": [
            {"project": item[0], "use_case": item[1], "actor": item[2]}
            for item in sorted(rejected)
        ],
        "snapshot_files": snapshot_files,
        "reporting_note": (
            "Time-constrained backup only. Report n and project composition with every mean; "
            "replace with the formal full-cohort evaluation when generation completes."
        ),
    }
    (report_root / "evaluation_cohort.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(
        f"Partial paired cohort frozen: n={len(paired_identities)}, "
        f"projects={dict(sorted(per_project.items()))}, directory={report_root}"
    )
    stages = ("l1", "l2") if stage == "both" else (stage,)
    for current_stage in stages:
        (report_root / f"evaluation_cohort_{current_stage}.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        _write_evaluation_outputs(
            config, runner, snapshot_results, report_root, current_stage,
        )


def run_current_without_dra_check(config: dict, config_path: Path) -> Path:
    """Freeze and judge only the currently completed v4 w/o-cross-slot outputs.

    This diagnostic is intentionally isolated from the formal paper reports.
    L1 and L2 are computed in one pass so each frozen use case is loaded once
    and identical judge inputs share the evaluator's single-flight cache.
    """
    from eval.run_experiments import (
        ExperimentRunner,
        generate_latex_table_l1,
        generate_latex_table_l3,
    )

    output_root = resolve_path(config_path, config["paths"]["output"])
    source_root = output_root / "crest_without_dra"
    runner = ExperimentRunner(output_dir=str(output_root), baseline_names=[])
    collected = runner.collect_crest_results(str(source_root))

    selected = {}
    skipped_stale = []
    for key, paths in sorted(collected.items()):
        if key[0] != WITHOUT_DRA:
            continue
        uc_path, ev_path = map(Path, paths)
        try:
            data = json.loads(uc_path.read_text(encoding="utf-8"))
        except Exception as exc:
            skipped_stale.append({"path": str(uc_path), "reason": f"read failed: {exc}"})
            continue
        if data.get("generation_version") != CREST_GENERATION_VERSION:
            skipped_stale.append({
                "path": str(uc_path),
                "reason": f"stale generation version: {data.get('generation_version')}",
            })
            continue
        if not _result_pair_is_valid(uc_path, ev_path):
            skipped_stale.append({"path": str(uc_path), "reason": "invalid main/evidence pair"})
            continue
        selected[key] = (uc_path, ev_path)

    if not selected:
        raise RuntimeError(
            f"No completed {CREST_GENERATION_VERSION} {WITHOUT_DRA} result pairs are available"
        )

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_root = output_root / "backup_evaluations" / f"crest_without_dra_v4_check_{stamp}"
    snapshot_root = report_root / "inputs"
    snapshot_results = {}
    snapshot_files = []
    for key, (uc_path, ev_path) in selected.items():
        _method, project, _use_case, _actor = key
        destination = snapshot_root / project
        destination.mkdir(parents=True, exist_ok=True)
        uc_copy = destination / uc_path.name
        ev_copy = destination / ev_path.name
        shutil.copy2(uc_path, uc_copy)
        shutil.copy2(ev_path, ev_copy)
        if not _result_pair_is_valid(uc_copy, ev_copy):
            raise RuntimeError(f"Snapshot pair became inconsistent: {uc_copy}")
        snapshot_results[key] = (str(uc_copy), str(ev_copy))
        snapshot_files.extend([
            str(uc_copy.relative_to(report_root)),
            str(ev_copy.relative_to(report_root)),
        ])

    evaluation = config.get("experiment", {}).get("evaluation", {})
    workers = max(
        1,
        min(
            len(snapshot_results),
            int(evaluation.get("l1_workers", 1)),
            int(evaluation.get("l2_workers", 1)),
        ),
    )
    print(
        f"Frozen current {WITHOUT_DRA} cohort: n={len(snapshot_results)}, "
        f"workers={workers}, directory={report_root}"
    )
    method_metrics = runner.compute_all_metrics(
        snapshot_results,
        run_nli=True,
        run_l2=True,
        max_workers=workers,
    )
    aggregated = runner.aggregate_and_report(method_metrics)
    audit = runner.evaluation_audit(method_metrics)

    l1_aggregated = {
        method: {
            key: value for key, value in row.items()
            if key in {"method", "n_ucs"} or key.startswith("l1_")
        }
        for method, row in aggregated.items()
    }
    l2_aggregated = {
        method: {
            key: value for key, value in row.items()
            if key in {"method", "n_ucs"}
            or key.startswith("l2_")
            or key.startswith("consistency_")
        }
        for method, row in aggregated.items()
    }
    l1_audit = {
        method: [
            {"use_case": row["use_case"], "actor": row["actor"], "l1": row["l1"]}
            for row in rows
        ]
        for method, rows in audit.items()
    }
    l2_audit = {
        method: [
            {"use_case": row["use_case"], "actor": row["actor"], "l2": row["l2"]}
            for row in rows
        ]
        for method, rows in audit.items()
    }
    files = {
        "evaluation_audit.json": audit,
        "evaluation_audit_l1.json": l1_audit,
        "evaluation_audit_l2.json": l2_audit,
        "aggregated_metrics.json": aggregated,
        "aggregated_metrics_l1.json": l1_aggregated,
        "aggregated_metrics_l2.json": l2_aggregated,
    }
    for name, payload in files.items():
        (report_root / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    (report_root / "table_l1.tex").write_text(
        generate_latex_table_l1(l1_aggregated), encoding="utf-8",
    )
    (report_root / "table_l2_consistency.tex").write_text(
        generate_latex_table_l3(l2_aggregated), encoding="utf-8",
    )
    manifest = {
        "experiment_type": "current_without_dra_diagnostic",
        "created_at": dt.datetime.now().astimezone().isoformat(),
        "method": WITHOUT_DRA,
        "generation_version": CREST_GENERATION_VERSION,
        "frozen_count": len(snapshot_results),
        "configured_paper_count": 14,
        "is_formal_full_cohort": len(snapshot_results) == 14,
        "workers": workers,
        "run_nli": True,
        "run_l2": True,
        "l1_evaluation_version": L1_EVALUATION_VERSION,
        "l2_evaluation_version": L2_EVALUATION_VERSION,
        "judge_models": {
            "l1_text": config.get("models", {}).get("nli_judge", {}).get("model_name"),
            "l1_vision": config.get("models", {}).get("nli_vision_judge", {}).get("model_name"),
            "l2": config.get("models", {}).get("l2_judge", {}).get("model_name"),
        },
        "use_cases": [
            {"project": key[1], "use_case": key[2], "actor": key[3]}
            for key in snapshot_results
        ],
        "snapshot_files": snapshot_files,
        "skipped_non_v4_or_invalid": skipped_stale,
        "reporting_note": (
            "Diagnostic snapshot of currently completed CREST w/o cross-slot v4 results; "
            "report n explicitly if the 14-case generation was still in progress."
        ),
    }
    (report_root / "evaluation_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"CURRENT_WITHOUT_DRA_CHECK={report_root}")
    return report_root


def main() -> int:
    parser = argparse.ArgumentParser(description="AUCMR unified server experiment entry point")
    parser.add_argument(
        "command",
        choices=(
            "guide", "preflight", "text-preflight", "prepare-images", "build-image", "build-text", "build-vdocrag",
            "generate", "revise", "evaluate-l1", "evaluate-l2", "evaluate",
            "evaluate-partial-l1", "evaluate-partial-l2", "evaluate-partial",
            "evaluate-current-without-dra",
            "archive", "all",
        ),
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--methods", nargs="+", help="Override experiment.methods for this run")
    parser.add_argument(
        "--use-cases", nargs="+",
        help="Run only these exact experiment.use_cases names for this invocation",
    )
    parser.add_argument(
        "--paper-cohort", action="store_true",
        help="Use the valid use cases recorded in outputs/evaluation_cohort_l1.json",
    )
    parser.add_argument(
        "--generation-workers", type=int,
        help="Concurrent CREST use cases; shares one local Jina/LinUCB/PCA stack",
    )
    parser.add_argument(
        "--revision-workers", type=int,
        help="Concurrent DRA-only use cases; shares one local Jina/LinUCB/PCA stack",
    )
    parser.add_argument(
        "--reuse-evidence", action="store_true",
        help=(
            "Re-author CREST from archived paired v4 w/o-DRA evidence cards, "
            "skipping query design/retrieval/evidence collection"
        ),
    )
    parser.add_argument("--skip-preflight", action="store_true")
    parser.add_argument(
        "--resume", action="store_true",
        help="Skip generation items that already have both result and evidence JSON files",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable output for guide")
    parser.add_argument(
        "--label", help="Archive label for `run.py archive` (letters, digits, '.', '_' and '-')",
    )
    parser.add_argument(
        "--partial-anchor", default="ViDoRAG-Elaborate",
        help="Method whose currently completed results anchor `evaluate-partial`",
    )
    parser.add_argument(
        "--projects", nargs="+", help="Limit build-image/build-text/build-vdocrag to these projects",
    )
    parser.add_argument(
        "--recreate-text", action="store_true",
        help="Drop and rebuild an existing {project}_text collection",
    )
    parser.add_argument(
        "--force-mineru", action="store_true",
        help="Run MinerU again even when a content-list JSON already exists",
    )
    parser.add_argument(
        "--recreate-empty", action="store_true",
        help="Drop and rebuild an existing zero-row image collection after verifying no import job is active",
    )
    args = parser.parse_args()
    config, config_path = load_config(args.config)
    if args.paper_cohort and args.use_cases:
        parser.error("--paper-cohort and --use-cases are mutually exclusive")
    selected_cli_use_cases = args.use_cases
    selected_paper_identities: set[tuple[str, str, str]] | None = None
    if args.paper_cohort:
        cohort_path = resolve_path(config_path, config["paths"]["output"]) / "evaluation_cohort_l1.json"
        # A prior `archive` intentionally moves active reports out of outputs/.
        # Recover the fixed formal v4 cohort directly instead of requiring users
        # to copy an old report back before generation or either evaluation stage.
        if not cohort_path.is_file():
            archived_cohort = (
                resolve_path(config_path, config["paths"]["output"])
                / DEFAULT_REUSE_EVIDENCE_SUBDIR.parent
                / "evaluation_cohort_l1.json"
            )
            if archived_cohort.is_file():
                cohort_path = archived_cohort
                print(f"Using archived v4 paper cohort: {cohort_path}")
        if not cohort_path.is_file():
            raise FileNotFoundError(
                f"Paper cohort file is missing: {cohort_path}. Run evaluation once or use --use-cases."
            )
        cohort = json.loads(cohort_path.read_text(encoding="utf-8"))
        valid_rows = cohort.get("valid_use_cases", [])
        selected_paper_identities = {
            (
                str(row.get("project", "")),
                str(row.get("use_case", "")),
                str(row.get("actor", "")),
            )
            for row in valid_rows
            if isinstance(row, dict)
            and str(row.get("project", "")).strip()
            and str(row.get("use_case", "")).strip()
            and str(row.get("actor", "")).strip()
        }
        if not selected_paper_identities:
            raise RuntimeError(f"Paper cohort contains no valid use cases: {cohort_path}")
    if selected_paper_identities is not None:
        select_use_case_identities(config, selected_paper_identities)
        config.setdefault("experiment", {})["_paper_cohort_selected"] = True
    else:
        select_use_cases(config, selected_cli_use_cases)
    if args.generation_workers is not None:
        if args.generation_workers < 1:
            parser.error("--generation-workers must be >= 1")
        config.setdefault("experiment", {})["generation_workers"] = args.generation_workers
    if args.revision_workers is not None:
        if args.revision_workers < 1:
            parser.error("--revision-workers must be >= 1")
        config.setdefault("experiment", {})["revision_workers"] = args.revision_workers
    logging.basicConfig(
        level=getattr(logging, str(config.get("runtime", {}).get("log_level", "INFO")).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    methods = selected_methods(config, args.methods)
    if args.command == "guide":
        return guide(config, config_path, json_output=args.json)
    if args.command == "text-preflight":
        return text_preflight(config, config_path, args.projects)
    if args.command == "prepare-images":
        run_prepare_images(config, config_path, args.projects)
        return 0
    if args.command == "build-image":
        run_image_build(config, config_path, args.projects, recreate_empty=args.recreate_empty)
        return 0
    if args.command == "build-text":
        if not args.skip_preflight:
            status = text_preflight(config, config_path, args.projects)
            if status and config.get("runtime", {}).get("fail_fast", True):
                return status
        run_text_build(
            config, config_path, args.projects, args.recreate_text, args.force_mineru,
        )
        return 0
    if args.command == "build-vdocrag":
        vdocrag_methods = ["VDocRAG-Elaborate"]
        if not args.skip_preflight:
            status = preflight(config, config_path, vdocrag_methods)
            if status and config.get("runtime", {}).get("fail_fast", True):
                return status
        run_vdocrag_build(config, args.projects)
        return 0
    if args.command == "preflight":
        return preflight(config, config_path, methods)
    if args.command == "archive":
        archive_outputs(config, config_path, args.label)
        return 0
    if args.command == "evaluate-current-without-dra":
        if not args.skip_preflight:
            status = preflight(config, config_path, [WITHOUT_DRA])
            if status and config.get("runtime", {}).get("fail_fast", True):
                return status
        run_current_without_dra_check(config, config_path)
        return 0
    if not args.skip_preflight:
        status = preflight(config, config_path, methods)
        if status and config.get("runtime", {}).get("fail_fast", True):
            return status
    if args.command in ("generate", "all"):
        run_generation(
            config, config_path, methods, resume=args.resume,
            reuse_evidence=args.reuse_evidence,
        )
    if args.command == "revise":
        run_revision(
            config, config_path, resume=args.resume,
            revision_workers=int(config.get("experiment", {}).get("revision_workers", 1)),
        )
    if args.command in {
        "evaluate-partial-l1", "evaluate-partial-l2", "evaluate-partial",
    }:
        partial_stage = {
            "evaluate-partial-l1": "l1",
            "evaluate-partial-l2": "l2",
            "evaluate-partial": "both",
        }[args.command]
        run_partial_evaluation(
            config, config_path, methods,
            anchor_method=args.partial_anchor, label=args.label, stage=partial_stage,
        )
    if args.command in ("evaluate-l1", "evaluate", "all"):
        run_evaluation(config, config_path, "l1")
    if args.command in ("evaluate-l2", "evaluate", "all"):
        run_evaluation(config, config_path, "l2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
