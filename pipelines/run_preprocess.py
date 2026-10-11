"""
一键数据预处理脚本 — 将 PDF 导入到指定项目（Milvus 集合）

用法:
    # 导入到自动命名的集合（PDF 文件名）
    python -m pipelines.run_preprocess data/jtvauctions.pdf

    # 导入到指定项目（多次执行可将不同 PDF 灌入同一集合）
    python -m pipelines.run_preprocess data/jtvauctions.pdf --project OR2UC
    python -m pipelines.run_preprocess data/nasa_report.pdf --project OR2UC

    # 指定图像输出目录
    python -m pipelines.run_preprocess data/jtvauctions.pdf --image-dir data/images/jtvauctions

流程:
    1. PDF → 页面图像 (data/images/<pdf_name>/1.png, 2.png, ...)
    2. 页面图像 → Jina 嵌入 → MinIO(parquet) → Milvus bulk import

注意:
    ROCm 环境变量 (TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL) 已由
    services.model_registry 在模块加载时统一设置，本脚本无需重复设置。
"""
import argparse
import logging
import os
import sys
from pathlib import Path

# 确保 AUCMR 包可导入 (run_preprocess.py 在 AUCMR/pipelines/ 下，父目录即 AUCMR/)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("preprocess")


def main():
    parser = argparse.ArgumentParser(
        description="AUCMR 数据预处理 — 从 PDF 到 Milvus 一键完成",
    )
    parser.add_argument(
        "pdf_path", type=str,
        help="PDF 文件路径",
    )
    parser.add_argument(
        "--image-dir", type=str, default=None,
        help="页面图像输出目录（默认 data/images/<pdf_name>/）",
    )
    parser.add_argument(
        "--project", type=str, default=None,
        help="Milvus 集合（项目）名称。不传则用 PDF 文件名。多次执行传入同一项目名可将不同 PDF 灌入同一集合。",
    )
    args = parser.parse_args()

    pdf_path = Path(args.pdf_path)
    if not pdf_path.exists():
        logger.error(f"PDF not found: {pdf_path}")
        sys.exit(1)

    pdf_name = pdf_path.stem  # 去后缀的文件名
    collection = args.project or pdf_name

    # 默认图像目录
    image_dir = args.image_dir or f"data/images/{pdf_name}"
    os.makedirs(image_dir, exist_ok=True)

    # ============================================================
    # Step 1: PDF → 页面图像
    # ============================================================
    logger.info(f"Step 1/2: Converting PDF to images -> {image_dir}")
    from utils.pdf_image import pdfToImage
    pdfToImage(str(pdf_path), image_dir)

    # ============================================================
    # Step 2: 嵌入 + 导入 Milvus
    # ============================================================
    logger.info(f"Step 2/2: Embedding images and importing to Milvus")
    from services.model_registry import registry
    from pipelines.preprocess_minio_multi_img import preprocess_multiImg

    embeder = registry.embeder
    job_id = preprocess_multiImg(
        output_dir=image_dir,
        pdf_name=pdf_name,
        embeder=embeder,
        collection_name=collection,
    )
    logger.info(f"Job ID: {job_id}")

    logger.info(f"\n{'='*60}")
    logger.info(f"Preprocessing submitted!")
    logger.info(f"  Project:    {collection}")
    logger.info(f"  PDF:        {pdf_path}")
    logger.info(f"  PDF name:   {pdf_name}")
    logger.info(f"  Images:     {image_dir}")
    logger.info(f"  Job ID:     {job_id}")
    logger.info(f"")
    logger.info(f"Check import progress:")
    logger.info(f"  python -m pipelines.check_progress {job_id} --project {collection} --watch")
    logger.info(f"")
    logger.info(f"After import, run CREST:")
    logger.info(f"  python AUCMR/main.py --project {collection} --file-names {pdf_name} --use-case \"...\"")
    logger.info(f"{'='*60}")


if __name__ == "__main__":
    main()
