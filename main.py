"""
AUCMR — Assisted Understanding and Collaborative Management of Requirements

主入口。双层架构:
    1. Retrieval Layer: 多模态 RAG（跨模态 late-interaction + CMAB-TopN + 页面级检索）
    2. Elaboration Layer: CREST（Cockburn-Reflective multi-agent elaboration + evidence tracing）

用法:
    # 自动检测集合内所有 PDF
    python main.py --project OR2UC --actor "Pilot" --use-case "Authenticate via Link16 Terminal"

    # 限定只搜某个 PDF
    python main.py --project OR2UC --file-names jtvauctions --actor "User" --use-case "拍卖商品浏览"

输出:
    - 不传 --output       → ./results/<project>/<actor>_<uc>.json + ...-evidence.json
    - 传   --output ROOT  → <ROOT>/<project>/<actor>_<uc>.json   + ...-evidence.json
"""

import argparse
import json
import logging
import os
import re
import sys

# 确保包可导入
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("AUCMR")


# 文件名 sanitize：保留 unicode 字母/数字（含中文）+ _ - .，其他全部删除
_SAFE_CHARS = re.compile(r"[^\w\-.]+", flags=re.UNICODE)


def _sanitize_stem(actor: str, use_case: str) -> str:
    """把 actor + use-case 拼成跨平台安全的文件名词干。"""
    raw = f"{actor}_{use_case}".replace(" ", "_")
    cleaned = _SAFE_CHARS.sub("", raw)
    return cleaned or "unnamed"


def _build_evidence_payload(uc) -> dict:
    """组装 evidence 落盘文件内容。

    - 包含全部 all_evidence_cards（按 card_id 去重，后写入覆盖先写入）
    - 附 referenced_card_ids 让消费者快速分辨被引用的卡
    """
    referenced: set = set()
    for slot in uc.slots.values():
        for claim in slot.claims:
            referenced.update(claim.evidence_refs)

    # cross-slot revision 可能产生同 ID 重号；落盘时去重保留最后一次
    seen: dict = {}
    for c in uc.all_evidence_cards:
        seen[c.card_id] = c
    deduped = list(seen.values())

    return {
        "use_case_name": uc.use_case_name,
        "actor": uc.actor,
        "evidence_summary": uc.evidence_summary,
        "referenced_card_ids": sorted(referenced),
        "evidence_cards": [c.to_dict() for c in deduped],
    }


# ============================================================
# 主入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="AUCMR — Use Case Elaboration")
    parser.add_argument(
        "--actor", type=str, required=True,
        help="Primary Actor 名称（如 Pilot, User, Administrator）"
    )
    parser.add_argument(
        "--use-case", type=str, required=True,
        help="用例名称（仅名称，如 'Authenticate via Link16 Terminal'）"
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="输出根目录（默认 ./results）；落盘到 <ROOT>/<project>/<actor>_<uc>.json 与同名 -evidence.json"
    )
    parser.add_argument(
        "--max-slot-rounds", type=int, default=None,
        help="每个 slot 的最大 DCRRM 循环轮数（不传则用 config/settings.py 里的 MAX_SLOT_ROUNDS）"
    )
    parser.add_argument(
        "--project", type=str, required=True,
        help="Milvus 集合（项目）名称"
    )
    parser.add_argument(
        "--file-names", type=str, nargs="+", default=None,
        help="限定检索的 PDF 文件名列表（不传则搜该项目下所有 PDF）"
    )
    args = parser.parse_args()

    logger.info("Running in REAL mode...")

    from services.model_registry import registry
    from crest import CRESTOrchestrator

    # 设置 Milvus 集合（触发 embeder/linucb/pca/retriever 懒加载）
    registry.set_collection(project=args.project, file_names=args.file_names)

    # 工厂方法：注入所有依赖
    retrieval_fn = registry.get_retrieval_adapter()
    provider = registry.get_model_provider(think=False)

    # 只有用户显式传了 --max-slot-rounds 才覆盖；否则让 orchestrator 用 settings 默认。
    orchestrator_kwargs = dict(
        retrieval_fn=retrieval_fn,
        file_names=registry.file_names,
        topk=registry.topk,
        provider=provider,
        total_doc_pages=registry.total_pages,
    )
    if args.max_slot_rounds is not None:
        orchestrator_kwargs["max_slot_rounds"] = args.max_slot_rounds
    orchestrator = CRESTOrchestrator(**orchestrator_kwargs)

    # 执行 elaboration
    logger.info(f"Elaborating: '{args.actor}' — '{args.use_case}'")
    uc = orchestrator.elaborate(actor=args.actor, use_case_name=args.use_case)

    # 输出：<root>/<project>/<actor>_<uc>.json + ...-evidence.json
    out_root = args.output or "results"
    out_dir = os.path.join(out_root, args.project)
    os.makedirs(out_dir, exist_ok=True)
    stem = _sanitize_stem(args.actor, args.use_case)
    main_path = os.path.join(out_dir, f"{stem}.json")
    evidence_path = os.path.join(out_dir, f"{stem}-evidence.json")

    with open(main_path, "w", encoding="utf-8") as f:
        f.write(uc.to_json())
    with open(evidence_path, "w", encoding="utf-8") as f:
        json.dump(_build_evidence_payload(uc), f, ensure_ascii=False, indent=2)
    logger.info(f"Main output written to {main_path}")
    logger.info(f"Evidence written to    {evidence_path}")

    # 摘要
    filled = sum(1 for s in uc.slots.values() if not s.is_empty())
    flags = len(uc.quality_flags)
    cards = uc.evidence_summary.get("total_cards", 0)
    cross = uc.evidence_summary.get("cross_page_cards", 0)
    logger.info(
        f"Done. Slots: {filled}/8 filled, "
        f"Evidence cards: {cards} ({cross} cross-page), "
        f"Quality flags: {flags}"
    )

    return uc


if __name__ == "__main__":
    main()
