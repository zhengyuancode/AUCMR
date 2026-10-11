"""Build the ``{project}_text`` ColBERT index from PDF documents.

The pipeline intentionally keeps every intermediate artifact on disk:

PDF -> MinerU content list -> page text with LVLM captions -> chunks -> Milvus.

External services are imported lazily so this module can be unit-tested without
loading the embedding model or connecting to Milvus.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence


logger = logging.getLogger(__name__)


_PROXY_ENV_NAMES = (
    "ALL_PROXY", "all_proxy",
    "HTTP_PROXY", "http_proxy",
    "HTTPS_PROXY", "https_proxy",
)


def _mineru_subprocess_env(source: dict[str, str] | None = None) -> dict[str, str]:
    """Return an HTTPX-compatible environment for the MinerU child process.

    Clash and similar clients commonly export ``socks://`` proxy URLs, while
    HTTPX accepts the explicit ``socks5://`` scheme.  Keep the proxy in use and
    normalize only that scheme instead of mutating the parent process.
    """
    env = dict(os.environ if source is None else source)
    normalized: list[str] = []
    for name in _PROXY_ENV_NAMES:
        value = env.get(name)
        if value and value.lower().startswith("socks://"):
            env[name] = f"socks5://{value[len('socks://') :]}"
            normalized.append(name)
    if normalized:
        logger.info(
            "Normalized socks:// to socks5:// for MinerU proxy variables: %s",
            ", ".join(normalized),
        )
    return env


def resolve_path(base: Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def find_content_list(parse_dir: Path, pdf_stem: str) -> Path | None:
    """Locate MinerU output across old and new directory layouts."""
    if not parse_dir.exists():
        return None
    exact = list(parse_dir.rglob(f"{pdf_stem}_content_list.json"))
    candidates = exact or list(parse_dir.rglob("*_content_list.json"))
    if not candidates:
        return None
    # MinerU's traditional ``<stem>/auto/<stem>_content_list.json`` wins.
    return sorted(
        candidates,
        key=lambda p: (
            p.stem != f"{pdf_stem}_content_list",
            "auto" not in {part.lower() for part in p.parts},
            len(p.parts),
            str(p),
        ),
    )[0]


def run_mineru(pdf: Path, parse_dir: Path, mineru_config: dict[str, Any]) -> Path:
    parse_dir.mkdir(parents=True, exist_ok=True)
    command = str(mineru_config.get("command", "mineru"))
    args = [command, "-p", str(pdf), "-o", str(parse_dir)]
    source = mineru_config.get("source")
    if source:
        args.extend(["--source", str(source)])
    args.extend(str(value) for value in mineru_config.get("extra_args", []))
    logger.info("Running MinerU for %s", pdf)
    subprocess.run(args, check=True, env=_mineru_subprocess_env())
    content_list = find_content_list(parse_dir, pdf.stem)
    if content_list is None:
        raise FileNotFoundError(
            f"MinerU completed but no *_content_list.json was found under {parse_dir}"
        )
    return content_list


def ensure_page_images(pdf: Path, page_dir: Path, required_pages: int | None = None) -> None:
    if required_pages and all((page_dir / f"{page}.png").is_file() for page in range(1, required_pages + 1)):
        return
    if required_pages is None and any(page_dir.glob("*.png")):
        return
    from utils.pdf_image import pdfToImage

    logger.info("Rendering PDF pages to %s", page_dir)
    pdfToImage(str(pdf), str(page_dir))


def _clean_text(value: Any) -> str:
    if value is None or value == [] or value == {}:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        return "\n".join(part for item in value if (part := _clean_text(item)))
    if isinstance(value, dict):
        return "\n".join(part for item in value.values() if (part := _clean_text(item)))
    return str(value).strip()


def _mineru_image_path(content_dir: Path, value: Any) -> Path | None:
    raw = _clean_text(value)
    if not raw:
        return None
    path = Path(raw)
    if path.is_absolute():
        if path.is_file():
            return path
        # Old MinerU outputs occasionally prefix a content-list-relative path
        # with '/'. Only accept it as a real absolute path when it exists.
        return content_dir / raw.lstrip("/\\")
    return content_dir / raw.lstrip("/\\")


class CaptionCache:
    def __init__(self, path: Path):
        self.path = path
        if path.is_file():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            self.data = loaded if isinstance(loaded, dict) else {}
        else:
            self.data: dict[str, str] = {}

    @staticmethod
    def key(image_path: Path, kind: str, language: str) -> str:
        stat = image_path.stat()
        material = f"{image_path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{kind}|{language}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def caption(
        self,
        image_path: Path,
        kind: str,
        language: str,
        captioner: Callable[[Path, str, str], str],
    ) -> str:
        cache_key = self.key(image_path, kind, language)
        if cache_key not in self.data:
            generated = str(captioner(image_path, kind, language)).strip()
            if not generated:
                raise RuntimeError(f"LVLM returned an empty caption for {image_path}")
            self.data[cache_key] = generated
            self.save()
        cached = str(self.data[cache_key]).strip()
        if not cached:
            raise RuntimeError(f"caption cache contains an empty caption for {image_path}")
        return cached

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def default_captioner(image_path: Path, kind: str, language: str) -> str:
    from config.settings import LVLM_ENDPOINT, chat_completion

    if kind == "page":
        subject = "entire document page"
    elif kind == "table":
        subject = "table, including its headers, values, units, and relationships"
    elif kind == "equation":
        subject = "equation and the meaning of its visible symbols"
    else:
        subject = "figure, including visible text and relationships"
    prompt = (
        f"Convert this {subject} into retrieval-ready prose. Preserve names, numbers, "
        f"labels, and logical relationships. Answer only in {language}; do not add facts "
        "that are not visible."
    )
    return str(
        chat_completion(
            LVLM_ENDPOINT,
            [{"role": "user", "content": prompt}],
            image_paths=[str(image_path)],
            think=False,
        )
    )


def merge_content_by_page(
    items: Sequence[dict[str, Any]],
    content_dir: Path,
    page_dir: Path,
    language: str,
    cache: CaptionCache,
    captioner: Callable[[Path, str, str], str] = default_captioner,
    strict_images: bool = True,
    total_pages: int | None = None,
) -> list[dict[str, Any]]:
    """Merge MinerU text and generated visual captions in source order."""
    grouped: dict[int, list[str]] = defaultdict(list)
    max_page = -1
    for item in items:
        page_idx = int(item.get("page_idx", item.get("page_index", 0)))
        max_page = max(max_page, page_idx)
        item_type = str(item.get("type", "text")).lower()
        text = _clean_text(item.get("text"))
        parts: list[str] = []
        image_path = _mineru_image_path(content_dir, item.get("img_path"))

        if item_type in {"image", "figure", "table", "equation", "interline_equation"}:
            kind = "equation" if "equation" in item_type else ("table" if item_type == "table" else "image")
            if image_path and image_path.is_file():
                generated = cache.caption(image_path, kind, language, captioner)
                if generated:
                    parts.append(generated)
            elif image_path and strict_images:
                raise FileNotFoundError(
                    f"MinerU block references a missing image on page {page_idx + 1}: {image_path}"
                )
            mineru_caption = _clean_text(
                item.get("table_caption") if item_type == "table" else item.get("image_caption")
            )
            if mineru_caption and mineru_caption not in parts:
                parts.append(mineru_caption)
            if item_type == "table":
                table_body = _clean_text(item.get("table_body"))
                if table_body and table_body not in parts:
                    parts.append(table_body)
            footnote = _clean_text(
                item.get("table_footnote") if item_type == "table" else item.get("image_footnote")
            )
            if footnote and footnote not in parts:
                parts.append(footnote)
            if text and text not in parts:
                parts.append(text)
        elif text:
            # Includes title/list/code and newer MinerU text-like block types.
            parts.append(text)
        else:
            logger.debug("Ignoring unsupported empty MinerU block: %s", item_type)
        grouped[page_idx].extend(parts)

    if total_pages is not None:
        max_page = max(max_page, int(total_pages) - 1)
    pages: list[dict[str, Any]] = []
    for page_idx in range(max_page + 1):
        text = "\n".join(part.strip() for part in grouped[page_idx] if part.strip()).strip()
        page_path = page_dir / f"{page_idx + 1}.png"
        if not text and page_path.is_file():
            text = cache.caption(page_path, "page", language, captioner)
        pages.append({
            "page_num": page_idx + 1,
            "page_path": str(page_path),
            "text": text,
        })
    return pages


BREAK_CHARS = frozenset(".。!！?？;；,，\n")


def chunk_pages(
    pages: Sequence[dict[str, Any]],
    file_name: str,
    chunk_size: int = 800,
    overlap: int = 0,
) -> list[dict[str, Any]]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("chunk_overlap must satisfy 0 <= overlap < chunk_size")
    chunks: list[dict[str, Any]] = []
    chunk_id = 0
    for page in pages:
        text = str(page.get("text", "")).strip()
        start = 0
        while start < len(text):
            end = min(start + chunk_size, len(text))
            if end < len(text):
                lower = start + chunk_size // 2
                boundary = next(
                    (i + 1 for i in range(end - 1, lower - 1, -1) if text[i] in BREAK_CHARS),
                    end,
                )
            else:
                boundary = end
            value = text[start:boundary].strip()
            if value:
                chunks.append({
                    "chunk_id": chunk_id,
                    "chunk": value,
                    "page_num": int(page["page_num"]),
                    "page_path": str(page["page_path"]),
                    "file_name": file_name,
                })
                chunk_id += 1
            if boundary >= len(text):
                break
            start = max(start + 1, boundary - overlap)
    return chunks


def normalize_multivector(value: Any) -> list[list[float]]:
    """Normalize Jina v4 outputs for either string or one-item-list input."""
    if hasattr(value, "detach"):
        value = value.detach().float().cpu().numpy()
    if hasattr(value, "tolist"):
        value = value.tolist()
    elif isinstance(value, (list, tuple)):
        converted = []
        for item in value:
            if hasattr(item, "detach"):
                item = item.detach().float().cpu().numpy()
            if hasattr(item, "tolist"):
                item = item.tolist()
            converted.append(item)
        value = converted
    if not isinstance(value, list) or not value:
        raise ValueError("embedder returned an empty multi-vector")
    # A one-item text batch is commonly returned as [tokens, dimension].
    if len(value) == 1 and isinstance(value[0], list) and value[0] and isinstance(value[0][0], list):
        value = value[0]
    if not isinstance(value[0], list) or not value[0] or isinstance(value[0][0], list):
        raise ValueError("unexpected Jina multi-vector shape")
    dimension = len(value[0])
    vectors = [[float(component) for component in vector] for vector in value]
    if any(len(vector) != dimension for vector in vectors):
        raise ValueError("embedder returned inconsistent vector dimensions")
    return vectors


def colbert_maxsim(query_vectors: Any, passage_vectors: Any) -> float:
    """Compute ``sum_i max_j(q_i dot d_j)`` with shape validation."""
    import numpy as np

    query = np.asarray(query_vectors, dtype=np.float32)
    passage = np.asarray(passage_vectors, dtype=np.float32)
    if query.ndim != 2 or passage.ndim != 2 or not len(query) or not len(passage):
        raise ValueError("ColBERT vectors must be non-empty 2-D matrices")
    if query.shape[1] != passage.shape[1]:
        raise ValueError(
            f"ColBERT dimension mismatch: query={query.shape[1]}, passage={passage.shape[1]}"
        )
    return float(np.dot(query, passage.T).max(axis=1).sum())


def rows_for_chunk(chunk: dict[str, Any], vectors: Sequence[Sequence[float]]) -> list[dict[str, Any]]:
    rows = []
    for seq_id, vector in enumerate(vectors):
        rows.append({
            "token_dense": list(vector),
            "seq_id": seq_id,
            "chunk_id": int(chunk["chunk_id"]),
            "page_num": int(chunk["page_num"]),
            "page_path": str(chunk["page_path"]) if seq_id == 0 else "",
            "file_name": str(chunk["file_name"]),
            "text": str(chunk["chunk"]) if seq_id == 0 else "",
        })
    return rows


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def prepare_document(
    document: dict[str, Any],
    config: dict[str, Any],
    config_dir: Path,
    force_mineru: bool = False,
    captioner: Callable[[Path, str, str], str] = default_captioner,
) -> list[dict[str, Any]]:
    pipeline_cfg = config["text_pipeline"]
    pdf = resolve_path(config_dir, document["pdf"])
    parse_dir = resolve_path(config_dir, document["parse_dir"])
    page_dir = resolve_path(config_dir, document["page_images"])
    work_root = resolve_path(config_dir, config["paths"].get("text_work", "data/text_pipeline"))
    work_dir = work_root / str(document["project"]) / str(document["file_name"])

    content_list = None if force_mineru else find_content_list(parse_dir, pdf.stem)
    if content_list is None:
        content_list = run_mineru(pdf, parse_dir, pipeline_cfg.get("mineru", {}))
    items = json.loads(content_list.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        raise ValueError(f"MinerU content list must be a JSON array: {content_list}")
    from fitz import open as open_pdf
    with open_pdf(str(pdf)) as pdf_document:
        pdf_pages = len(pdf_document)
    required_pages = max(pdf_pages, 1 + max(
        (int(item.get("page_idx", item.get("page_index", 0))) for item in items),
        default=-1,
    ))
    ensure_page_images(pdf, page_dir, required_pages)

    cache = CaptionCache(work_dir / "caption_cache.json")
    pages = merge_content_by_page(
        items, content_list.parent, page_dir,
        str(pipeline_cfg.get("language", "English")), cache, captioner,
        bool(pipeline_cfg.get("strict_images", True)),
        required_pages,
    )
    chunks = chunk_pages(
        pages,
        str(document["file_name"]),
        int(pipeline_cfg.get("chunk_size", 800)),
        int(pipeline_cfg.get("chunk_overlap", 0)),
    )
    _write_json(work_dir / "pages.json", pages)
    _write_json(work_dir / "chunks.json", {"file_name": document["file_name"], "chunks": chunks})
    _write_json(work_dir / "source.json", {
        "pdf": str(pdf),
        "mineru_content_list": str(content_list),
        "page_images": str(page_dir),
    })
    return chunks


def _encode_chunk(embedder: Any, text: str) -> list[list[float]]:
    import torch

    with torch.inference_mode():
        encoded = embedder.encode_text(
            texts=text,
            task="retrieval",
            prompt_name="passage",
            return_multivector=True,
        )
    return normalize_multivector(encoded)


def build_text_indexes(
    config: dict[str, Any],
    config_path: Path,
    recreate: bool = False,
    force_mineru: bool = False,
    projects: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    selected = set(projects or [])
    documents = [
        item for item in config["text_pipeline"]["documents"]
        if not selected or str(item["project"]) in selected
    ]
    if selected - {str(item["project"]) for item in documents}:
        raise ValueError(f"Unknown text-pipeline project(s): {sorted(selected)}")

    from config.milvus import MilvusColbertRetriever, client as milvus_client
    from services.model_registry import registry

    template = str(config["services"]["text_milvus"]["collection_template"])
    project_names = sorted({str(document["project"]) for document in documents})
    existing = [
        template.format(project=project)
        for project in project_names
        if milvus_client.has_collection(collection_name=template.format(project=project))
    ]
    if existing and not recreate:
        raise RuntimeError(
            f"text collection(s) already exist: {existing}; rerun with --recreate-text to replace them"
        )

    prepared: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for document in documents:
        prepared[str(document["project"])].extend(
            prepare_document(document, config, config_path.parent, force_mineru)
        )

    expected_dim = int(config["text_pipeline"].get("embedding_dimension", 128))
    insert_batch_rows = int(config["text_pipeline"].get("insert_batch_rows", 2000))
    summaries: list[dict[str, Any]] = []
    for project, chunks in prepared.items():
        if not chunks:
            raise RuntimeError(f"No non-empty text chunks were produced for project {project}")
        collection = template.format(project=project)
        retriever = MilvusColbertRetriever(milvus_client, collection)
        first_vectors = _encode_chunk(registry.embeder, chunks[0]["chunk"])
        actual_dim = len(first_vectors[0])
        if actual_dim != expected_dim:
            raise ValueError(
                f"Jina multi-vector dimension is {actual_dim}, but text_pipeline.embedding_dimension={expected_dim}"
            )
        retriever.create_collection_text_colbert(actual_dim, recreate=recreate)

        pending: list[dict[str, Any]] = []
        vector_rows = 0
        for index, chunk in enumerate(chunks):
            vectors = first_vectors if index == 0 else _encode_chunk(registry.embeder, chunk["chunk"])
            if len(vectors[0]) != actual_dim:
                raise ValueError("Jina embedding dimension changed within one indexing run")
            rows = rows_for_chunk(chunk, vectors)
            pending.extend(rows)
            vector_rows += len(rows)
            if len(pending) >= insert_batch_rows:
                retriever.insert_text_colbert_rows(pending)
                pending = []
        if pending:
            retriever.insert_text_colbert_rows(pending)
        retriever.create_index_text_colbert()
        milvus_client.load_collection(collection_name=collection)
        summary = {
            "project": project,
            "collection": collection,
            "documents": len({chunk["file_name"] for chunk in chunks}),
            "chunks": len(chunks),
            "vector_rows": vector_rows,
            "dimension": actual_dim,
        }
        summaries.append(summary)
        logger.info("Built text index: %s", summary)
    return summaries
