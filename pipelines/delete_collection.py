"""
删除指定名称的 Milvus collection

用法:
    python -m pipelines.delete_collection --project COLLECTION_NAME

示例:
    python -m pipelines.delete_collection --project OR2UC
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.milvus import client as milvus_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("delete_collection")


def main():
    parser = argparse.ArgumentParser(
        description="删除指定的 Milvus collection",
    )
    parser.add_argument(
        "--project", type=str, required=True,
        help="要删除的 Milvus collection 名称",
    )
    args = parser.parse_args()

    collection_name = args.project

    # 检查 collection 是否存在
    if not milvus_client.has_collection(collection_name=collection_name):
        logger.error(f"Collection '{collection_name}' 不存在")
        sys.exit(1)

    # 确认
    confirm = input(f"确认删除 collection '{collection_name}'？(yes/no): ")
    if confirm.strip().lower() != "yes":
        logger.info("已取消")
        sys.exit(0)

    milvus_client.drop_collection(collection_name=collection_name)
    logger.info(f"Collection '{collection_name}' 已删除")


if __name__ == "__main__":
    main()
