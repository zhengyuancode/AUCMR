"""Official VDocRAG retriever adapted to AUCMR's slot-wise experiment.

VDocRAG-Elaborate uses the released VDocRetriever Phi-3+LoRA weights for page
retrieval.  The shared AUCMR slot generator is intentionally retained, matching
the experiment definition in ``samples/main-zh.tex``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional


logger = logging.getLogger("vdocrag_adapter")
_VENDOR_ROOT = Path(__file__).resolve().parent / "VDocRAG"
_VENDOR_SRC = _VENDOR_ROOT / "src"
if str(_VENDOR_SRC) not in sys.path:
    sys.path.insert(0, str(_VENDOR_SRC))

STATUS = "official_vdocretriever"
_RUNTIME = None


def _natural_key(path: Path) -> tuple:
    return tuple(int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name))


def page_image_paths(images_root: Path, project: str, file_names: Iterable[str]) -> List[Path]:
    """Resolve page images without duplicating a project/file directory."""
    directories: List[Path] = []
    for name in [project, *file_names]:
        directory = images_root / str(name)
        if directory.is_dir() and directory not in directories:
            directories.append(directory)
    paths: List[Path] = []
    for directory in directories:
        paths.extend(
            path for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        )
    return sorted(dict.fromkeys(paths), key=lambda p: (str(p.parent), _natural_key(p)))


def _cfg(path: str, default=None):
    from config.settings import _cfg as get_config
    return get_config(path, default)


def _configured_path(value: str) -> Path:
    from config.settings import _CONFIG_PATH
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (_CONFIG_PATH.parent / path).resolve()


class VDocRetrieverRuntime:
    def __init__(self, config: Dict[str, Any]):
        import torch
        from transformers import AutoProcessor
        from vdocrag.vdocretriever.modeling import VDocRetriever

        self.config = config
        self.device = str(config.get("device", "cuda:0"))
        dtype_name = str(config.get("dtype", "bfloat16"))
        if not hasattr(torch, dtype_name):
            raise ValueError(f"Unknown torch dtype for VDocRAG: {dtype_name}")
        dtype = getattr(torch, dtype_name)
        base_model = str(config["base_model"])
        lora = str(config["retriever_lora"])
        common = {
            "trust_remote_code": True,
            "torch_dtype": dtype,
            "use_cache": False,
            "local_files_only": bool(config.get("local_files_only", True)),
        }
        # Phi-3 Vision's remote config currently defaults to FlashAttention2.
        # That backend requires the external NVIDIA-oriented ``flash_attn``
        # package and therefore fails before model construction on AMD ROCm.
        # Explicit eager attention is slower but is implemented by the released
        # Phi-3 Vision code and is portable across ROCm/CUDA/CPU.  A configured
        # value can still opt into SDPA or FlashAttention2 on a compatible host.
        attention = config.get("attn_implementation") or "eager"
        # Phi-3 Vision's remote configuration consumes the private config
        # attribute used by the official VDocRAG encoder entry point. Passing
        # only the newer public kwarg leaves its baked-in FlashAttention2 value
        # intact with the transformers version used by the experiment server.
        common["_attn_implementation"] = str(attention)
        self.processor = AutoProcessor.from_pretrained(
            base_model,
            trust_remote_code=True,
            local_files_only=bool(config.get("local_files_only", True)),
        )
        self.model = VDocRetriever.load(
            base_model,
            lora_name_or_path=lora,
            pooling=str(config.get("pooling", "eos")),
            normalize=bool(config.get("normalize", True)),
            **common,
        ).to(self.device)
        self.model.eval()

    def encode_query(self, query: str):
        import torch

        template = str(self.config.get(
            "query_template",
            "Instruct: I'm looking for an image that answers the question.\nQuery: {query}</s>",
        ))
        inputs = self.processor(
            [template.format(query=query)], return_tensors="pt", padding="longest",
            max_length=int(self.config.get("query_max_length", 256)), truncation=True,
        ).to(self.device)
        with torch.inference_mode():
            return self.model(query=inputs, use_cache=False).q_reps.float().cpu().numpy()[0]

    def encode_pages(self, paths: List[Path]):
        import numpy as np
        import torch
        from PIL import Image

        prompt = str(self.config.get("document_prompt", "<|image_1|>\nWhat is shown in this image?</s>"))
        side = int(self.config.get("image_size", 1344))
        vectors = []
        # The official quick start processes each page independently. This also
        # avoids padding incompatibilities between different page aspect ratios.
        for path in paths:
            with Image.open(path) as source:
                image = source.convert("RGB").resize((side, side))
                inputs = self.processor(
                    prompt, images=image, return_tensors="pt", padding="longest",
                    max_length=int(self.config.get("document_max_length", 4096)),
                    truncation=True,
                ).to(self.device)
            with torch.inference_mode():
                vector = self.model(document=inputs, use_cache=False).p_reps.float().cpu().numpy()[0]
            vectors.append(vector)
        return np.asarray(vectors, dtype=np.float32)


def get_runtime() -> VDocRetrieverRuntime:
    global _RUNTIME
    if _RUNTIME is None:
        config = dict(_cfg("baselines.vdocrag", {}) or {})
        for field in ("base_model", "retriever_lora"):
            if not config.get(field):
                raise RuntimeError(f"Missing baselines.vdocrag.{field}")
        _RUNTIME = VDocRetrieverRuntime(config)
    return _RUNTIME


def _corpus_fingerprint(paths: List[Path], config: Dict[str, Any]) -> str:
    payload = {
        "model": config.get("base_model"),
        "lora": config.get("retriever_lora"),
        "image_size": config.get("image_size", 1344),
        "pages": [(str(path.resolve()), path.stat().st_size, path.stat().st_mtime_ns) for path in paths],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def corpus_embeddings(project: str, file_names: List[str], runtime: VDocRetrieverRuntime):
    import numpy as np

    images_root = _configured_path(str(_cfg("paths.images", "data/images")))
    paths = page_image_paths(images_root, project, file_names)
    if not paths:
        raise FileNotFoundError(f"No page images found for project={project}, file_names={file_names}")
    cache_root = _configured_path(str(_cfg("paths.vendor_cache", "data/vendor_cache"))) / "vdocrag"
    cache_root.mkdir(parents=True, exist_ok=True)
    cache_path = cache_root / f"{project}.npz"
    fingerprint = _corpus_fingerprint(paths, runtime.config)
    if cache_path.is_file():
        with np.load(cache_path, allow_pickle=False) as cached:
            if str(cached["fingerprint"].item()) == fingerprint:
                cached_paths = [Path(value) for value in cached["paths"].tolist()]
                embeddings = cached["embeddings"].astype(np.float32, copy=True)
                return cached_paths, embeddings
    logger.info("Encoding %d pages with official VDocRetriever", len(paths))
    embeddings = runtime.encode_pages(paths)
    np.savez_compressed(
        cache_path,
        fingerprint=np.asarray(fingerprint),
        paths=np.asarray([str(path) for path in paths]),
        embeddings=embeddings,
    )
    return paths, embeddings


def retrieve_pages(query: str, project: str, file_names: List[str], top_k: int) -> List[str]:
    import numpy as np

    runtime = get_runtime()
    paths, documents = corpus_embeddings(project, file_names, runtime)
    query_vector = runtime.encode_query(query)
    if documents.ndim != 2 or documents.shape[1] != query_vector.shape[0]:
        raise ValueError("VDocRetriever query/document embedding dimensions do not match")
    scores = documents @ query_vector
    order = np.argsort(-scores)[:min(int(top_k), len(paths))]
    return [str(paths[int(index)]) for index in order]


def _generate_slot(
    slot_name: str, use_case_name: str, actor: str, filled_slots: Dict[str, Any],
    page_paths: List[str],
) -> Optional[Dict[str, Any]]:
    from config.settings import LVLM_ENDPOINT, chat_completion
    from eval.baselines import (
        SLOT_DEFINITIONS, SLOT_GENERATION_PROMPT, _enforce_evidence_whitelist,
        _format_pages_as_context, _normalize_slot_response, _parse_json_with_repair,
        get_prompt_with_prior_context,
    )

    context_block = _format_pages_as_context(page_paths)
    prompt = SLOT_GENERATION_PROMPT.format(
        slot_name=slot_name,
        slot_definition=SLOT_DEFINITIONS.get(slot_name, ""),
        use_case_name=use_case_name,
        actor=actor,
        context_block=context_block,
    )
    prompt = get_prompt_with_prior_context(prompt, use_case_name, actor, filled_slots, slot_name)
    response = chat_completion(
        LVLM_ENDPOINT, [{"role": "user", "content": prompt}], image_paths=page_paths,
    )
    normalized = _normalize_slot_response(slot_name, _parse_json_with_repair(response))
    allowed = [line.split(":", 1)[0] for line in context_block.splitlines()]
    normalized = _enforce_evidence_whitelist(normalized, allowed)
    # Preserve the private pre-constraint audit draft even if the strict final
    # whitelist removes every claim; the shared orchestrator emits no_evidence.
    return normalized


def fill_slot(
    slot_name: str,
    use_case_name: str,
    actor: str,
    project: str,
    file_names: List[str],
    filled_slots: Dict[str, Any],
    watermark_fn: Optional[Callable[[List[str]], List[str]]] = None,
) -> Optional[Dict[str, Any]]:
    if slot_name == "primary_actor":
        return None
    query = (
        f"Detailed description of the '{slot_name}' for the use case "
        f"'{use_case_name}' performed by {actor}"
    )
    top_k = int(_cfg("baselines.vdocrag.top_k", 5))
    pages = retrieve_pages(query, project, file_names, top_k)
    if watermark_fn:
        pages = watermark_fn(pages)
    return _generate_slot(slot_name, use_case_name, actor, filled_slots, pages)
