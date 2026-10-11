"""
测试 Milvus 集合的检索功能（使用 CREST 同款检索函数）

用法:
    python pipelines/test_retrieval.py --project surveillance_6016b

    # 自定义查询
    python pipelines/test_retrieval.py --project surveillance_6016b --query "your question here"

    # 指定 topk
    python pipelines/test_retrieval.py --project surveillance_6016b --topk 3
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("test_retrieval")


def main():
    parser = argparse.ArgumentParser(description="测试 Milvus 集合检索")
    parser.add_argument(
        "--project", type=str, required=True,
        help="Milvus 集合名称",
    )
    parser.add_argument(
        "--query", type=str, nargs="+",
        default=[
            "What is the purpose of the surveillance system described in this document?",
            "How does the data processing pipeline work?",
        ],
        help="测试查询",
    )
    parser.add_argument(
        "--topk", type=int, default=None,
        help="返回页数（默认使用 config/settings.py 中的 TOPK）",
    )
    args = parser.parse_args()

    # ---- 1. 初始化 registry（与 CREST 完全相同的流程） ----
    from services.model_registry import registry

    registry.set_collection(project=args.project, file_names=None)

    project = args.project
    file_names = registry.file_names
    total_pages = registry.total_pages
    topk = args.topk or registry.topk

    logger.info(f"Collection : {project}")
    logger.info(f"Files      : {file_names}")
    logger.info(f"Total pages: {total_pages}")
    logger.info(f"TopK       : {topk}")

    # ---- 2. 获取检索适配器（CREST 用的就是这个） ----
    retrieval_fn = registry.get_retrieval_adapter()

    # ---- 3. 执行检索 ----
    queries = args.query if isinstance(args.query, list) else [args.query]
    results = retrieval_fn(queries, file_names, topk)

    # ---- 4. 输出结果 ----
    for i, (query, pages) in enumerate(zip(queries, results)):
        print(f"\n{'='*60}")
        print(f"Query {i+1}: {query}")
        print(f"Retrieved {len(pages)} pages:")
        for j, page_path in enumerate(pages):
            print(f"  [{j+1}] {page_path}")
        if not pages:
            print("  (无结果)")

    logger.info("Done.")


if __name__ == "__main__":
    main()
