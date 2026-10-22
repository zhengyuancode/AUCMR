"""
Baseline 方法实现（v2：共享外层编排器 + 论文 7 项 L1-L4 归因/弃答契约）。

论文 770-774 行（共享外层编排器）:
  为全部外部配置提供统一的外层任务编排器。该编排器按照相同的 Cockburn 风格模板的
  槽位顺序发起精化请求，并在生成第 i 个槽位时，把已经生成的 1…i-1 个槽位作为只读
  上下文附加到提示词中；各配置则保留自身的内层求解过程。

论文 785-787 行（统一引用契约）:
  每个原子陈述都必须引用至少一个稳定且对模型可见的标识符，该标识符必须来自推理期间
  实际暴露的证据，引用超出对应证据白名单的标识符视为无效。
  提示词中均指示证据不足时留空或只写有把握的断言。

每个 baseline 提供:
  - 一个 `slot_filler(slot, filled_slots, context) -> SlotContent-like dict` 接口
  - 由 shared_orchestrator.run_shared_orchestrator 调度，按 Cockburn 槽位顺序逐个调用

Baselines:
  1. LVLM-Direct:        把 PDF 全部页面图像按 batch 喂给 LVLM，按 slot 单独调用
  2. TextRAG-Elaborate:  ColBERT 文本检索（OCR/Caption）+ 单次生成（per-slot）
  3. VDocRAG-Elaborate:  视觉文档检索（page image）+ 单次生成（per-slot）
  4. ViDoRAG-Elaborate:  seeker-inspector-answer 三阶段 + 单次生成（per-slot）
                         【复现: 拉 https://github.com/.../ViDoRAG，注入 shared orchestrator】
  5. ReAct-Elaborate:    ReAct 思考-行动-观察循环（per-slot）
                         【复现: 拉官方 ReAct 实现，注入 shared orchestrator】
"""

import json
import logging
import os
import sys
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from config.settings import _cfg, _CONFIG_PATH

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("baselines")

# 确保 project root 可导入
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from eval.shared_orchestrator import (
    run_shared_orchestrator,
    get_prompt_with_prior_context,
    COCKBURN_SLOT_ORDER,
)

def _page_number(path: str) -> Optional[int]:
    """Extract a page number from POSIX or Windows image paths."""
    name = os.path.basename(str(path).replace("\\", "/"))
    match = re.fullmatch(r"(\d+)\.(?:png|jpe?g|gif|bmp|tiff?)", name, re.IGNORECASE)
    if match:
        return int(match.group(1))
    match = re.search(r"page[_-]?(\d+)", name, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _init_registry(project: str, file_names: Optional[List[str]] = None):
    """Ensure the model registry is initialized for the given project."""
    try:
        from services.model_registry import registry
        registry.set_collection(project=project, file_names=file_names)
        logger.info(f"Registry initialized for project={project}")
    except Exception as e:
        logger.error(f"Registry init failed for {project}: {e}")
        raise


# ============================================================
# 单槽位 prompt 模板（带归因契约）
# ============================================================

SLOT_GENERATION_PROMPT = """You are a requirements engineering analyst. Generate ONLY the "{slot_name}" slot of a Cockburn fully-dressed use case for the use case named "{use_case_name}" with primary actor "{actor}".

Slot definition: {slot_definition}

Output a JSON object. For simple slots (goal, primary_actor, trigger) use {{"raw_text": "...", "evidence_refs": ["P<page_num>"]}}. For list/sequential slots (preconditions, main_flow, alternative_flow, exception_flow, postconditions) use {{"claims": [{{"content": "...", "evidence_refs": ["P<page_num>"], "step_number": <int or omit>}}]}}.

Citation contract (mandatory):
- Every claim MUST carry a non-empty `evidence_refs` list referencing page identifiers (e.g. "P3") drawn ONLY from the provided context below.
- If no evidence in context supports a claim, OMIT that claim entirely (do not invent).
- If NO claim can be supported, return an empty object.

Use the exact terminology from the document. Do NOT fabricate facts.

Available context (page identifiers in form P<page_num>):
{context_block}
"""


SLOT_DEFINITIONS = {
    "goal": "A single sentence describing what the use case accomplishes.",
    "primary_actor": "(unused — this slot is forced from user input by the orchestrator)",
    "preconditions": "A numbered list of conditions that must be true before the use case starts.",
    "trigger": "A single sentence describing the event that initiates the use case.",
    "main_flow": "A numbered list of steps describing the normal interaction sequence.",
    "alternative_flow": "A numbered list of alternative paths branching from main flow steps.",
    "exception_flow": "A numbered list of exception paths (error handling) with explicit exit.",
    "postconditions": "A numbered list of conditions that must be true after the use case completes successfully.",
}


# ============================================================
# 公共 helper：调用 Qwen3-Max / Qwen3-VL
# ============================================================

def _call_llm_text(prompt: str) -> str:
    """走 LLM_ENDPOINT（Qwen3-Max）调一次纯文本。"""
    from config.settings import LLM_ENDPOINT, chat_completion
    return chat_completion(
        LLM_ENDPOINT,
        [{"role": "user", "content": prompt}],
    )


def _call_lvlm(prompt: str, image_paths: List[str]) -> str:
    """走 LVLM_ENDPOINT（Qwen3-VL）调一次多模态。"""
    from config.settings import LVLM_ENDPOINT, chat_completion
    return chat_completion(
        LVLM_ENDPOINT,
        [{"role": "user", "content": prompt}],
        image_paths=image_paths,
    )


def _parse_json_with_repair(raw: str) -> Optional[Any]:
    try:
        import json_repair
        return json_repair.loads(raw)
    except Exception:
        return None


def _unwrap_slot_payload(parsed: Any, slot_name: Optional[str] = None) -> Any:
    """Unwrap common envelopes or the requested slot without weakening citations."""
    if not isinstance(parsed, dict):
        return parsed
    if any(key in parsed for key in ("raw_text", "claims", "content", "evidence_refs")):
        return parsed
    envelope_keys = ("answer", "result", "output")
    if slot_name:
        envelope_keys += (slot_name,)
    for key in envelope_keys:
        nested = parsed.get(key)
        if isinstance(nested, dict):
            return nested
    return parsed


def _build_pre_constraint_draft(slot_name: str, parsed: Any) -> Dict[str, Any]:
    """Return the parsed candidate before citation filtering/whitelisting.

    The normal output path remains strict; this private audit field exists solely
    so CitCov can observe uncited or invalidly cited candidate claims instead of
    measuring the post-filter survivor set.
    """
    parsed = _unwrap_slot_payload(parsed, slot_name)
    claims: List[Dict[str, Any]] = []
    if not isinstance(parsed, dict):
        return {"slot": slot_name, "claims": claims}
    raw_claims = parsed.get("claims") or []
    if isinstance(raw_claims, list):
        for item in raw_claims:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            refs = item.get("evidence_refs") or []
            claims.append({
                "content": content,
                "evidence_refs": list(refs) if isinstance(refs, list) else [],
                **({"step_number": item["step_number"]}
                   if isinstance(item.get("step_number"), int) else {}),
            })
    if not claims:
        content = parsed.get("raw_text", "")
        if not isinstance(content, str) or not content.strip():
            content = parsed.get("content", "")
        if isinstance(content, str) and content.strip():
            refs = parsed.get("evidence_refs") or []
            claims.append({
                "content": content.strip(),
                "evidence_refs": list(refs) if isinstance(refs, list) else [],
            })
    return {"slot": slot_name, "claims": claims}


def _normalize_slot_response(slot_name: str, parsed: Any) -> Dict[str, Any]:
    """把 LLM 返回的 dict 规范化成 SlotContent-like dict。"""
    parsed = _unwrap_slot_payload(parsed, slot_name)
    if not isinstance(parsed, dict):
        return {"slot": slot_name, "claims": [], "raw_text": ""}
    pre_constraint_draft = _build_pre_constraint_draft(slot_name, parsed)
    # 简单 slot
    if slot_name in ("goal", "trigger", "primary_actor"):
        rt = parsed.get("raw_text", "")
        refs = parsed.get("evidence_refs") or []
        # A model may serialize a semantically simple slot as a one-item claims
        # array.  Treat that shape as equivalent instead of measuring JSON-style
        # compliance.  Citation validation remains unchanged downstream.
        if not isinstance(rt, str) or not rt.strip() or not refs:
            raw_claims = parsed.get("claims") or []
            if isinstance(raw_claims, list):
                candidate = next((
                    item for item in raw_claims
                    if isinstance(item, dict) and str(item.get("content", "")).strip()
                ), None)
                if candidate is not None:
                    rt = str(candidate.get("content", "")).strip()
                    refs = candidate.get("evidence_refs") or []
        # Also accept a single-claim object returned without a claims wrapper.
        if (not isinstance(rt, str) or not rt.strip()) and str(parsed.get("content", "")).strip():
            rt = str(parsed.get("content", "")).strip()
            refs = parsed.get("evidence_refs") or []
        if not isinstance(refs, list):
            refs = []
        if isinstance(rt, str) and rt.strip():
            rt = rt.strip()
            return {
                "slot": slot_name,
                "claims": [{"content": rt, "evidence_refs": refs}] if refs else [],
                "raw_text": rt,
                "evidence_card_ids": refs,
                "_pre_constraint_draft": pre_constraint_draft,
            }
        return {"slot": slot_name, "claims": [], "raw_text": "", "_pre_constraint_draft": pre_constraint_draft}
    # 列表/有序 slot
    raw_claims = parsed.get("claims") or []
    if not raw_claims and str(parsed.get("raw_text", "")).strip():
        raw_claims = [{
            "content": str(parsed["raw_text"]).strip(),
            "evidence_refs": parsed.get("evidence_refs") or [],
        }]
    elif not raw_claims and str(parsed.get("content", "")).strip():
        raw_claims = [parsed]
    if not isinstance(raw_claims, list):
        raw_claims = []
    claims = []
    for c in raw_claims:
        if not isinstance(c, dict):
            continue
        content = c.get("content", "")
        refs = c.get("evidence_refs") or []
        if not isinstance(refs, list):
            refs = []
        if not content or not refs:
            continue  # 契约：缺引用就丢弃
        step = c.get("step_number")
        claims.append({
            "content": content,
            "evidence_refs": refs,
            **({"step_number": step} if isinstance(step, int) else {}),
        })
    return {
        "slot": slot_name,
        "claims": claims,
        "raw_text": "",
        "evidence_card_ids": [r for c in claims for r in c["evidence_refs"]],
        "_pre_constraint_draft": pre_constraint_draft,
    }


def _normalize_uncited_slot_response(slot_name: str, parsed: Any) -> Dict[str, Any]:
    """Normalize LVLM-Direct output, for which page citations are not exposed."""
    parsed = _unwrap_slot_payload(parsed, slot_name)
    if not isinstance(parsed, dict):
        return {"slot": slot_name, "claims": [], "raw_text": ""}
    if slot_name in ("goal", "trigger", "primary_actor"):
        raw_text = str(parsed.get("raw_text", "")).strip()
        if not raw_text:
            raw_claims = parsed.get("claims") or []
            if isinstance(raw_claims, list):
                candidate = next((
                    item for item in raw_claims
                    if isinstance(item, dict) and str(item.get("content", "")).strip()
                ), None)
                if candidate is not None:
                    raw_text = str(candidate["content"]).strip()
        if not raw_text:
            raw_text = str(parsed.get("content", "")).strip()
        return {
            "slot": slot_name,
            "claims": [{"content": raw_text, "evidence_refs": []}] if raw_text else [],
            "raw_text": raw_text,
            "evidence_card_ids": [],
        }
    raw_claims = parsed.get("claims", []) or []
    if not raw_claims and str(parsed.get("raw_text", "")).strip():
        raw_claims = [{"content": str(parsed["raw_text"]).strip()}]
    elif not raw_claims and str(parsed.get("content", "")).strip():
        raw_claims = [parsed]
    claims = []
    for item in raw_claims:
        if not isinstance(item, dict) or not str(item.get("content", "")).strip():
            continue
        claim = {"content": str(item["content"]).strip(), "evidence_refs": []}
        if isinstance(item.get("step_number"), int):
            claim["step_number"] = item["step_number"]
        claims.append(claim)
    return {"slot": slot_name, "claims": claims, "raw_text": "", "evidence_card_ids": []}


def _enforce_evidence_whitelist(
    normalized: Dict[str, Any], allowed_refs: List[str],
) -> Dict[str, Any]:
    """Drop claims that cite IDs not shown to the baseline during inference."""
    allowed = set(allowed_refs)
    kept = []
    for claim in normalized.get("claims", []) or []:
        refs = claim.get("evidence_refs") or []
        if refs and all(isinstance(ref, str) and ref in allowed for ref in refs):
            kept.append(claim)
    normalized = dict(normalized)
    draft = normalized.get("_pre_constraint_draft")
    if isinstance(draft, dict):
        draft = dict(draft)
        # Whitelisting is the hard boundary; retain its exposed IDs in the audit
        # so an evaluator can distinguish an invalid citation from an unseen one.
        draft["exposed_evidence_refs"] = list(allowed_refs)
        normalized["_pre_constraint_draft"] = draft
    normalized["claims"] = kept
    normalized["evidence_card_ids"] = list(dict.fromkeys(
        ref for claim in kept for ref in claim.get("evidence_refs", [])
    ))
    if normalized.get("raw_text") and not kept:
        normalized["raw_text"] = ""
    return normalized


# ============================================================
# 公共 helper：构造 page-id 格式的 context block
# ============================================================

def _format_pages_as_context(page_paths: List[str]) -> str:
    """把页面路径列表格式化为 'P<page_num>: <path>' 形式供 LLM 引用。"""
    import re
    if not page_paths:
        return "(no pages available)"
    lines = []
    seen = set()
    for p in page_paths:
        page_num = _page_number(p)
        if page_num is None:
            continue
        if page_num in seen:
            continue
        seen.add(page_num)
        lines.append(f"P{page_num}: {p}")
    return "\n".join(lines)


# ============================================================
# Base class
# ============================================================

class BaseBaseline(ABC):
    """Abstract base for all baseline methods."""

    def __init__(self, name: str, output_dir: str = "./results_baselines"):
        self.name = name
        self.output_dir = output_dir
        self.requires_registry = True
        self._run_file_names: List[str] = []
        self._evidence_records: Dict[str, Dict[str, Any]] = {}
        self._watermark_cache: Dict[Tuple[str, ...], List[str]] = {}
        os.makedirs(output_dir, exist_ok=True)

    def slot_filler(
        self,
        slot_name: str,
        filled_slots: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Per-slot 填充函数（由共享编排器调用）。"""
        return self._fill_slot(slot_name, filled_slots, context)

    def watermark_pages(self, page_paths: List[str], project: str) -> List[str]:
        key = tuple(page_paths)
        if key not in self._watermark_cache:
            from utils.image_merge import add_page_id_watermarks
            self._watermark_cache[key] = add_page_id_watermarks(
                page_paths,
                os.path.join(self.output_dir, "_watermarked", project),
            )
        exposed = self._watermark_cache[key]
        for path in exposed:
            page_num = _page_number(path)
            if page_num is None:
                continue
            ref = f"P{page_num}"
            self._evidence_records[ref] = {
                "card_id": ref,
                "content": "",
                "source_pages": [page_num],
                "source_doc": project,
                "image_paths": [str(path)],
                "is_cross_page": False,
            }
        return exposed

    @abstractmethod
    def _fill_slot(
        self,
        slot_name: str,
        filled_slots: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Per-slot 检索 + 生成。返回 SlotContent-like dict 或 None（无证据）。"""
        ...

    def run_and_save(
        self, use_case_name: str, actor: str, project: str,
        file_names: Optional[List[str]] = None,
    ) -> str:
        """Run shared orchestrator and save results."""
        logger.info(f"[{self.name}] Running shared orchestrator for: "
                    f"{use_case_name} ({actor}) on {project}")
        import time
        start = time.time()
        self._evidence_records = {}
        self._run_file_names = list(file_names or [project])
        if self.requires_registry:
            _init_registry(project, file_names=file_names)

        result = run_shared_orchestrator(
            use_case_name=use_case_name,
            actor=actor,
            slot_filler=self.slot_filler,
            method=self.name,
            project=project,
            raise_on_error=bool(_cfg("runtime.fail_fast", True)),
        )
        result["elapsed_seconds"] = round(time.time() - start, 1)

        safe_name = f"{actor}_{use_case_name}".replace(" ", "_").replace("/", "_")
        out_dir = os.path.join(self.output_dir, project)
        os.makedirs(out_dir, exist_ok=True)
        uc_path = os.path.join(out_dir, f"{safe_name}_{self.name}.json")
        with open(uc_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        # Baselines cite page IDs rather than CREST evidence cards.  Still emit the
        # paired file required by the common metric collector.
        final_referenced = {
            ref
            for slot in result.get("slots", {}).values()
            for claim in slot.get("claims", [])
            for ref in claim.get("evidence_refs", [])
        }
        # Also expose page/chunk records that were cited in a raw candidate but
        # removed by the hard whitelist.  The audit metric can then distinguish
        # a valid exposed ref from a hallucinated ID without changing final UC.
        audit_referenced = {
            ref
            for draft in result.get("citation_audit", {}).get("pre_constraint_slots", {}).values()
            if isinstance(draft, dict)
            for claim in (draft.get("claims", []) or [])
            if isinstance(claim, dict)
            for ref in (claim.get("evidence_refs", []) or [])
            if isinstance(ref, str) and ref in self._evidence_records
        }
        referenced = sorted(final_referenced | audit_referenced)
        images_root = Path(str(_cfg("paths.images", "data/images"))).expanduser()
        if not images_root.is_absolute():
            images_root = _CONFIG_PATH.parent / images_root
        evidence_cards = []
        for ref in referenced:
            if ref in self._evidence_records:
                evidence_cards.append(self._evidence_records[ref])
                continue
            match = re.fullmatch(r"P(\d+)", ref)
            if not match:
                continue
            page_num = int(match.group(1))
            candidates = [images_root / project / f"{page_num}{suffix}" for suffix in (".png", ".jpg", ".jpeg")]
            image_path = next((path for path in candidates if path.is_file()), candidates[0])
            evidence_cards.append(self._evidence_records.get(ref, {
                "card_id": ref,
                "content": "",
                "source_pages": [page_num],
                "source_doc": project,
                "image_paths": [str(image_path)],
                "is_cross_page": False,
            }))
        evidence_path = uc_path.replace(".json", "-evidence.json")
        with open(evidence_path, "w", encoding="utf-8") as f:
            json.dump({
                "use_case_name": use_case_name,
                "actor": actor,
                "method": self.name,
                "referenced_card_ids": referenced,
                "evidence_cards": evidence_cards,
            }, f, ensure_ascii=False, indent=2)
        logger.info(f"[{self.name}] Saved to {uc_path} ({result['elapsed_seconds']}s)")
        return uc_path


# ============================================================
# 1) LVLM-Direct：把整篇 PDF 全部页面图像喂给 LVLM，按 slot 单独生成
# ============================================================

class LvlMDirectBaseline(BaseBaseline):
    """LVLM-Direct: compress all PDF pages into a few composite images.

    The composites are created once per project and every slot receives the same
    complete-document visual context in a single LVLM request.  No stable page IDs
    are exposed, so this method intentionally emits uncited claims.
    """

    def __init__(self, output_dir: str = "./results_baselines"):
        super().__init__("LVLM-Direct", output_dir)
        self.requires_registry = False
        self._composite_cache: Dict[str, List[str]] = {}

    def _get_page_images(self, project: str) -> List[str]:
        configured = _cfg("paths.images", "data/images")
        images_root = os.path.expanduser(str(configured))
        if not os.path.isabs(images_root):
            images_root = os.path.join(str(_CONFIG_PATH.parent), images_root)
        img_dir = os.path.join(images_root, project)
        if not os.path.isdir(img_dir):
            return []
        images = sorted(
            [os.path.join(img_dir, f) for f in os.listdir(img_dir) if f.endswith(".png")],
            key=lambda x: int("".join(c for c in os.path.splitext(os.path.basename(x))[0] if c.isdigit()) or 0),
        )
        return images

    def _get_composite_images(self, project: str) -> List[str]:
        if project in self._composite_cache:
            return self._composite_cache[project]
        from utils.image_merge import build_composite_images

        pages = self._get_page_images(project)
        if not pages:
            return []
        output_dir = os.path.join(self.output_dir, "_composites", project)
        composite_paths = build_composite_images(
            image_paths=pages,
            output_dir=output_dir,
            composite_count=int(_cfg("baselines.lvlm_direct.composite_count", 5)),
            columns=int(_cfg("baselines.lvlm_direct.columns", 4)),
            page_width=int(_cfg("baselines.lvlm_direct.page_width", 384)),
            jpeg_quality=int(_cfg("baselines.lvlm_direct.jpeg_quality", 75)),
            gutter=int(_cfg("baselines.lvlm_direct.gutter", 4)),
        )
        self._composite_cache[project] = composite_paths
        logger.info(
            f"[{self.name}] compressed {len(pages)} pages into "
            f"{len(composite_paths)} composite image(s)"
        )
        return composite_paths

    def _fill_slot(
        self,
        slot_name: str,
        filled_slots: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        # primary_actor 由 orchestrator 强制注入
        if slot_name == "primary_actor":
            return None

        project = context.get("project", "")
        composite_images = self._get_composite_images(project)
        if not composite_images:
            logger.warning(f"[{self.name}] no page images for {project}")
            return None
        prompt = (
            "You are a requirements engineering analyst. The attached composite images "
            "jointly contain compressed screenshots of every page in the complete PDF, "
            "in original page order. They deliberately expose no stable page identifiers. "
            "Generate only the requested Cockburn slot. Do not invent citations. If "
            "evidence is insufficient, return an empty object.\n\n"
            f"Use case: {context['use_case_name']}\n"
            f"Primary actor: {context['actor']}\n"
            f"Slot: {slot_name}\n"
            f"Definition: {SLOT_DEFINITIONS.get(slot_name, '')}\n"
            "Return JSON: raw_text for a simple slot, or claims with content and optional "
            "step_number for a list/sequential slot."
        )
        prompt = get_prompt_with_prior_context(
            prompt, context["use_case_name"], context["actor"], filled_slots, slot_name,
        )
        try:
            response = _call_lvlm(prompt, composite_images)
        except Exception as e:
            logger.error(f"[{self.name}] LVLM call failed: {e}")
            return None
        norm = _normalize_uncited_slot_response(
            slot_name, _parse_json_with_repair(response)
        )
        return norm if norm.get("claims") or norm.get("raw_text") else None


# ============================================================
# 2) TextRAG-Elaborate：ColBERT 文本检索（OCR/Caption）
# ============================================================

class TextRAGElaborateBaseline(BaseBaseline):
    """TextRAG-Elaborate: ColBERT 文本检索 + 单次生成（per-slot）。

    论文 778 行: "TextRAG-Elaborate 以当前槽位说明和用例名称构造一次文本检索请求，
    通过 ColBERT 风格的检索器在 OCR 提取文本和图像文字描述上检索，为每个检索片段附加
    稳定的块标识符和页面标识符，然后由生成器基于这些文本片段填写当前槽位。"

    文本集合由 services.text_milvus.collection_template 配置，须预先写入 OCR/caption
    chunk 及其 Jina token multi-vector 索引。检索结果按
    <file>:P<page>#C<chunk> 暴露稳定引用标识。
    """

    TOP_K_CHUNKS = 10

    def __init__(self, output_dir: str = "./results_baselines"):
        super().__init__("TextRAG-Elaborate", output_dir)

    def _retrieve_text_chunks(
        self,
        query: str,
        project: str,
        file_names: List[str],
        top_k: int = TOP_K_CHUNKS,
    ) -> List[Tuple[str, str]]:
        """返回 [(chunk_id, chunk_text), ...]

        The text collection is configured independently from the AUCMR image
        collection through services.text_milvus.collection_template.
        """
        try:
            from services.model_registry import registry
            from config.milvus import MilvusColbertRetriever, client as milvus_client
            from config.settings import _cfg
            template = str(_cfg("services.text_milvus.collection_template", "{project}_text"))
            collection = template.format(project=project)
            if not milvus_client.has_collection(collection_name=collection):
                raise RuntimeError(f"text collection does not exist: {collection}")
            retriever = MilvusColbertRetriever(
                collection_name=collection, milvus_client=milvus_client,
            )
            from pipelines.text_index import normalize_multivector
            encoded = registry.embeder.encode_text(
                texts=query, task="retrieval", prompt_name="query",
                return_multivector=True,
            )
            query_vectors = normalize_multivector(encoded)
            coarse = int(_cfg("text_pipeline.coarse_candidates", 100))
            results = retriever.text_colbert_search(
                query_vectors, file_names=file_names, topk=top_k, coarse=coarse,
            )
        except Exception as e:
            logger.warning(f"[TextRAG] retrieval failed: {e}")
            return []
        chunks = []
        for item in results:
            page_num = int(item.get("page_num", 0))
            file_name = str(item.get("file_name", project))
            chunk_id = f"{file_name}:P{page_num}#C{int(item['chunk_id'])}"
            chunks.append((chunk_id, str(item.get("text", ""))))
        return chunks

    def _fill_slot(
        self,
        slot_name: str,
        filled_slots: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if slot_name == "primary_actor":
            return None

        project = context.get("project", "")
        try:
            from services.model_registry import registry
            file_names = registry.file_names
        except Exception:
            file_names = []

        # Per-slot 查询
        slot_query = (
            f"Detailed description of the '{slot_name}' for the use case "
            f"'{context['use_case_name']}' performed by {context['actor']}"
        )
        chunks = self._retrieve_text_chunks(slot_query, project, file_names)
        if not chunks:
            return None

        context_block_lines = []
        for cid, ctext in chunks:
            context_block_lines.append(f"{cid}: {ctext}")
            page_match = re.search(r":P(\d+)#", cid)
            self._evidence_records[cid] = {
                "card_id": cid,
                "content": ctext,
                "source_pages": [int(page_match.group(1))] if page_match else [],
                "source_doc": project,
                "is_cross_page": False,
            }
        context_block = "\n".join(context_block_lines)

        prompt = SLOT_GENERATION_PROMPT.format(
            slot_name=slot_name,
            slot_definition=SLOT_DEFINITIONS.get(slot_name, ""),
            use_case_name=context["use_case_name"],
            actor=context["actor"],
            context_block=context_block,
        )
        prompt += (
            "\nTextRAG citation override: each context line begins with a full chunk ID "
            "such as doc:P3#C7. Cite that exact full ID in evidence_refs; never shorten "
            "it to P3 and never cite an ID absent from the context."
        )
        prompt = get_prompt_with_prior_context(
            prompt,
            context["use_case_name"],
            context["actor"],
            filled_slots,
            slot_name,
        )

        try:
            response = _call_llm_text(prompt)
        except Exception as e:
            logger.error(f"[{self.name}] LLM call failed: {e}")
            return None

        parsed = _parse_json_with_repair(response)
        norm = _normalize_slot_response(slot_name, parsed)
        norm = _enforce_evidence_whitelist(norm, [cid for cid, _ in chunks])
        # Return an empty final slot together with its private audit draft when
        # filtering removed every candidate; the shared orchestrator will mark
        # no_evidence while preserving the pre-constraint CitCov denominator.
        return norm


# ============================================================
# 3) VDocRAG-Elaborate：视觉文档检索（page image）+ 单次生成
# ============================================================

class VDocRAGElaborateBaseline(BaseBaseline):
    """VDocRAG-Elaborate: 调用 vendor/VDocRAG/ 官方代码 (vendor_adapter)。

    论文 778 行: "VDocRAG-Elaborate 以同样的槽位级请求调用 VDocRAG 的视觉文档检索器，
    为当前槽位返回页面图像，并在所有返回图像上添加统一的页码水印，然后由生成器基于
    这些图像填写当前槽位。"

    vendor 仓库: https://github.com/nttmdlab-nlp/VDocRAG
    接入细节见 eval/vendor/vdocrag_adapter.py 与 eval/vendor/README.md。
    """

    TOP_K_PAGES = 5

    def __init__(self, output_dir: str = "./results_baselines"):
        super().__init__("VDocRAG-Elaborate", output_dir)
        self.requires_registry = False

    def _fill_slot(
        self,
        slot_name: str,
        filled_slots: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if slot_name == "primary_actor":
            return None

        project = context.get("project", "")
        file_names = self._run_file_names

        # Official VDocRetriever page retrieval + shared Detail-UC generator.
        from eval.vendor.vdocrag_adapter import fill_slot as vendor_fill
        result = vendor_fill(
            slot_name=slot_name,
            use_case_name=context["use_case_name"],
            actor=context["actor"],
            project=project,
            file_names=file_names,
            filled_slots=filled_slots,
            watermark_fn=lambda pages: self.watermark_pages(pages, project),
        )
        return result


# ============================================================
# 4) ViDoRAG-Elaborate: seeker-inspector-answer 三阶段（per-slot）
# ============================================================

class ViDoRAGElaborateBaseline(VDocRAGElaborateBaseline):
    """ViDoRAG-Elaborate: 调用 vendor/ViDoRAG/ 官方代码 (vendor_adapter)。

    论文 780 行: "ViDoRAG-Elaborate 将 ViDoRAG 原生的 'seeker-inspector-answer' 流水线
    适配到 Detail-UC 槽位粒度..."

    vendor 仓库: https://github.com/Alibaba-NLP/ViDoRAG
    接入细节见 eval/vendor/vidorag_adapter.py 与 eval/vendor/README.md。
    """

    INSPECTOR_ROUNDS = 2

    def __init__(self, output_dir: str = "./results_baselines"):
        super().__init__(output_dir)
        self.name = "ViDoRAG-Elaborate"
        self.requires_registry = True

    def _fill_slot(
        self,
        slot_name: str,
        filled_slots: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if slot_name == "primary_actor":
            return None

        project = context.get("project", "")
        file_names = self._run_file_names

        # 调用 vendor adapter
        from eval.vendor.vidorag_adapter import fill_slot as vendor_fill
        return vendor_fill(
            slot_name=slot_name,
            use_case_name=context["use_case_name"],
            actor=context["actor"],
            project=project,
            file_names=file_names,
            filled_slots=filled_slots,
            watermark_fn=lambda pages: self.watermark_pages(pages, project),
        )


def _pid_from_path(p: str) -> str:
    return f"P{_page_number(p) or 0}"


# ============================================================
# 5) ReAct-Elaborate: 思考-行动-观察循环（per-slot）
# ============================================================

class ReActElaborateBaseline(BaseBaseline):
    """ReAct-Elaborate: 调用 vendor/ReAct/ 官方代码 (vendor_adapter)。

    论文 780 行: "ReAct-Elaborate 是本文对 ReAct 范式的按槽位实现，对于每个槽位，
    它运行'思考-行动-观察'循环，页面检索是唯一工具..."

    vendor 仓库: https://github.com/ysymyth/ReAct
    接入细节见 eval/vendor/react_adapter.py 与 eval/vendor/README.md。
    """

    def __init__(self, output_dir: str = "./results_baselines"):
        super().__init__("ReAct-Elaborate", output_dir)

    def _fill_slot(
        self,
        slot_name: str,
        filled_slots: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if slot_name == "primary_actor":
            return None

        project = context.get("project", "")
        try:
            from services.model_registry import registry
            file_names = registry.file_names
        except Exception:
            file_names = []

        from eval.vendor.react_adapter import fill_slot as vendor_fill
        return vendor_fill(
            slot_name=slot_name,
            use_case_name=context["use_case_name"],
            actor=context["actor"],
            project=project,
            file_names=file_names,
            filled_slots=filled_slots,
            watermark_fn=lambda pages: self.watermark_pages(pages, project),
        )


def _page_overlap_rate(a: List[str], b: List[str]) -> float:
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    return len(sa & sb) / max(1, min(len(sa), len(sb)))


# ============================================================
# Factory
# ============================================================

def get_all_baselines(
    output_dir: str = "./results_baselines",
    names: Optional[List[str]] = None,
) -> List[BaseBaseline]:
    """Return all baseline instances."""
    baselines = [
        LvlMDirectBaseline(output_dir=output_dir),
        TextRAGElaborateBaseline(output_dir=output_dir),
        VDocRAGElaborateBaseline(output_dir=output_dir),
        ViDoRAGElaborateBaseline(output_dir=output_dir),
        ReActElaborateBaseline(output_dir=output_dir),
    ]
    if names is None:
        return baselines
    wanted = set(names)
    unknown = wanted - {b.name for b in baselines}
    if unknown:
        raise ValueError(f"Unknown baseline method(s): {sorted(unknown)}")
    return [b for b in baselines if b.name in wanted]
