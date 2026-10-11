"""
查询 Milvus bulk import 任务进度

用法:
    # 查一次就退出
    python -m pipelines.check_progress JOB_ID --project COLLECTION_NAME

    # 持续轮询直到任务结束 (Completed / Failed)
    python -m pipelines.check_progress JOB_ID --project COLLECTION_NAME --watch

    # 自定义轮询间隔 (默认 10 秒)
    python -m pipelines.check_progress JOB_ID --project COLLECTION_NAME --watch --interval 5

示例:
    python -m pipelines.check_progress 458176921830xxxx --project OR2UC --watch
"""
import argparse
import logging
import sys
import time
from pathlib import Path

# 确保 AUCMR 包可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.milvus import MilvusColbertRetriever, client as milvus_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("check_progress")

# Milvus bulk import 的终态
TERMINAL_STATES = {"Completed", "Failed"}


def check_job(job_id, collection_name):
    """查询一次 bulk import 任务状态，返回 state 字符串。"""
    retriever = MilvusColbertRetriever(collection_name=collection_name, milvus_client=milvus_client)
    resp = retriever.search_import_progress(job_id)
    milvus_client.release_collection(collection_name=collection_name)
    return resp["data"]["state"]


def main():
    parser = argparse.ArgumentParser(
        description="查询 Milvus bulk import 任务进度",
    )
    parser.add_argument(
        "job_id", type=str,
        help="bulk import 任务 ID (run_preprocess 输出)",
    )
    parser.add_argument(
        "--project", type=str, required=True,
        help="Milvus 集合（项目）名称",
    )
    parser.add_argument(
        "--watch", action="store_true",
        help="持续轮询直到任务结束",
    )
    parser.add_argument(
        "--interval", type=int, default=10,
        help="轮询间隔（秒），默认 10",
    )
    args = parser.parse_args()

    if not args.watch:
        state = check_job(args.job_id, args.project)
        logger.info(f"Job {args.job_id} state: {state}")
        sys.exit(0 if state == "Completed" else 1)

    logger.info(f"Watching job {args.job_id} on collection '{args.project}' (interval={args.interval}s)")
    while True:
        state = check_job(args.job_id, args.project)
        logger.info(f"State: {state}")
        if state in TERMINAL_STATES:
            if state == "Completed":
                logger.info("Import completed.")
                sys.exit(0)
            logger.error("Import failed.")
            sys.exit(1)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
