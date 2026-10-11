"""ViDoRAG's official Seeker/Inspector loop with an injected AUCMR retriever."""

from __future__ import annotations

import base64
import io
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


logger = logging.getLogger("vidorag_adapter")

STATUS = "official_seeker_inspector_with_injected_retriever"


def _cfg(path: str, default=None):
    from config.settings import _cfg as get_config
    return get_config(path, default)


class EndpointVLM:
    """Expose AUCMR's OpenAI-compatible LVLM through ViDoRAG's ``generate`` API."""

    @staticmethod
    def _data_url(image: Any) -> str:
        if hasattr(image, "save"):
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, format="JPEG", quality=90)
            payload = buffer.getvalue()
            media = "image/jpeg"
        else:
            path = Path(str(image))
            payload = path.read_bytes()
            media = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        return f"data:{media};base64,{base64.b64encode(payload).decode('ascii')}"

    def generate(self, query: str, image: List[Any]) -> str:
        from config.settings import LVLM_ENDPOINT, create_openai_client

        content = [
            {"type": "image_url", "image_url": {"url": self._data_url(value)}}
            for value in (image or [])
        ]
        content.append({"type": "text", "text": query})
        kwargs = {
            "model": LVLM_ENDPOINT.model_name,
            "messages": [{"role": "user", "content": content}],
            "stream": False,
        }
        if LVLM_ENDPOINT.temperature is not None:
            kwargs["temperature"] = LVLM_ENDPOINT.temperature
        if LVLM_ENDPOINT.max_tokens is not None:
            kwargs["max_tokens"] = LVLM_ENDPOINT.max_tokens
        completion = create_openai_client(LVLM_ENDPOINT).chat.completions.create(**kwargs)
        return str(completion.choices[0].message.content)


def official_agent_pages(
    query: str,
    candidate_pages: List[str],
    max_rounds: int,
    vlm: Any | None = None,
    agent_classes: Tuple[Any, Any] | None = None,
) -> Tuple[List[str], str | None]:
    """Run the released Seeker/Inspector state machine and return verified pages."""
    if not candidate_pages:
        return [], None
    if len(candidate_pages) > 20:
        raise ValueError("Official ViDoRAG page maps support at most 20 candidates")
    if agent_classes is None:
        from eval.vendor.ViDoRAG.vidorag_agents import Inspector, Seeker
        agent_classes = (Seeker, Inspector)
    seeker_class, inspector_class = agent_classes
    vlm = vlm or EndpointVLM()
    seeker = seeker_class(vlm)
    inspector = inspector_class(vlm)
    seeker.buffer_images = None
    inspector.buffer_images = []

    selected, _, _ = seeker.run(query=query, images_path=candidate_pages)
    for _ in range(max_rounds):
        status, information, images = inspector.run(query, selected)
        if status in {"answer", "synthesizer"}:
            return list(images or inspector.buffer_images), information
        if status == "seeker":
            if not getattr(seeker, "buffer_images", None):
                return list(inspector.buffer_images), information
            selected, _, _ = seeker.run(feedback=information)
            continue
        return list(inspector.buffer_images), information
    logger.warning("ViDoRAG reached max_rounds=%d; using accumulated verified pages", max_rounds)
    return list(inspector.buffer_images), None


def _retrieve_candidates(query: str, file_names: List[str], top_k: int) -> List[str]:
    from services.model_registry import registry

    retrieval_fn = registry.get_retrieval_adapter()
    results = retrieval_fn([query], file_names, topk=top_k)
    pages = results[0] if results and isinstance(results[0], list) else []
    return list(dict.fromkeys(str(page) for page in pages))[:20]


def _run_slot_synthesizer(prompt: str, page_paths: List[str], vlm: Any) -> str:
    """Adapt the released Synthesizer by replacing only its answer prompt."""
    from eval.vendor.ViDoRAG.agent.map_dict import arrangement_map_dict
    from eval.vendor.ViDoRAG.utils.image_preprosser import concat_images_with_bbox
    from eval.vendor.ViDoRAG.vidorag_agents import Synthesizer

    if not 1 <= len(page_paths) <= 20:
        raise ValueError("ViDoRAG Answer supports between 1 and 20 verified pages")
    synthesizer = Synthesizer(vlm)
    prompt = prompt + "\n\n" + synthesizer.page_map[len(page_paths)]
    if synthesizer.synthesizer_multi_image:
        input_images = page_paths
    else:
        input_images = [concat_images_with_bbox(
            page_paths,
            arrangement=arrangement_map_dict[len(page_paths)],
            scale=1,
            line_width=40,
        )]
    return str(synthesizer.vlm.generate(query=prompt, image=input_images))


def _generate_slot(
    slot_name: str, use_case_name: str, actor: str, filled_slots: Dict[str, Any],
    page_paths: List[str], candidate_answer: str | None = None, vlm: Any | None = None,
) -> Optional[Dict[str, Any]]:
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
    if candidate_answer:
        prompt += f"\n\nViDoRAG Inspector candidate information:\n{candidate_answer}"
    prompt = get_prompt_with_prior_context(prompt, use_case_name, actor, filled_slots, slot_name)
    # Retain the released Answer/Synthesizer page mapping and visual composition;
    # only the original short-QA prompt is replaced by the Detail-UC slot prompt.
    response = _run_slot_synthesizer(prompt, page_paths, vlm or EndpointVLM())
    normalized = _normalize_slot_response(slot_name, _parse_json_with_repair(response))
    allowed = [line.split(":", 1)[0] for line in context_block.splitlines()]
    normalized = _enforce_evidence_whitelist(normalized, allowed)
    return normalized if normalized.get("claims") or normalized.get("raw_text") else None


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
        f"Use case: {use_case_name} by {actor}. Find evidence needed to generate "
        f"the '{slot_name}' slot of a detailed Cockburn use case."
    )
    candidates = _retrieve_candidates(
        query, file_names, int(_cfg("baselines.vidorag.candidate_top_k", 10)),
    )
    pages, candidate_answer = official_agent_pages(
        query, candidates, max_rounds=int(_cfg("baselines.vidorag.max_rounds", 5)),
    )
    if not pages:
        return None
    if watermark_fn:
        pages = watermark_fn(pages)
    return _generate_slot(
        slot_name, use_case_name, actor, filled_slots, pages, candidate_answer,
        EndpointVLM(),
    )
