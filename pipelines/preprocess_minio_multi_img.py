from tqdm import tqdm
from typing import List
from pathlib import Path
import logging
import ast
from config.milvus import MilvusColbertRetriever, client as milvus_client
from config.settings import (
    MINIO_ENDPOINT, MINIO_ACCESS_KEY,
    MINIO_SECRET_KEY, MINIO_BUCKET_NAME, MINIO_SECURE, MILVUS_URI, MILVUS_TOKEN
)
from pymilvus import Collection, connections
from pymilvus.bulk_writer import BulkFileType, RemoteBulkWriter
from pipelines.upload_milvus import upload_milvus

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def processImg(page_paths: List[str], Mymodel, batch_size: int = 1, max_pixels: int | None = None):
    kwargs = {}
    if max_pixels is not None:
        kwargs["max_pixels"] = int(max_pixels)
    multivector_image_embeddings = Mymodel.encode_image(
        images=page_paths,
        task="retrieval",
        batch_size=max(1, int(batch_size)),
        return_multivector=True,
        **kwargs,
    )
    return multivector_image_embeddings


def upload_minio(
    page_paths, retriever, file_name, writer, mymodel,
    embedding_batch_size=1, commit_every_pages=10, max_pixels=None,
):
    if not page_paths:
        logger.error("No supported image files were found in the specified folder.")
        return

    batch_size = max(1, int(embedding_batch_size))
    commit_every = max(1, int(commit_every_pages))
    pending = 0
    committed = 0
    progress = tqdm(total=len(page_paths), desc="Encoding and staging pages", unit="page")
    for start in range(0, len(page_paths), batch_size):
        batch_paths = page_paths[start:start + batch_size]
        logger.info(
            "Encoding image pages %s-%s/%s (batch_size=%s)",
            start + 1, start + len(batch_paths), len(page_paths), batch_size,
        )
        embeddings = processImg(
            batch_paths, mymodel, batch_size=batch_size, max_pixels=max_pixels,
        )
        for page_path, colbert_vecs in zip(batch_paths, embeddings):
            data = {
                "colbert_vecs": colbert_vecs.float().cpu().numpy(),
                "page_num": int(Path(page_path).stem),
                "page_path": str(page_path),
                "file_name": file_name,
            }
            retriever.bulk_insert_prepare_multi_img(data, writer)
            pending += 1
            progress.update(1)
            if pending >= commit_every:
                retriever.bulk_prepare_commit(writer)
                committed += pending
                pending = 0
                logger.info("Staged %s/%s pages in MinIO", committed, len(page_paths))
    if pending:
        retriever.bulk_prepare_commit(writer)
        committed += pending
        logger.info("Staged %s/%s pages in MinIO", committed, len(page_paths))
    progress.close()

    batch_files = writer.batch_files
    if isinstance(batch_files, str):
        batch_files = ast.literal_eval(batch_files)
    path_str = batch_files[0][0]
    return path_str[:path_str.rfind('/') + 1]


def _create_bulk_writer(schema, collection_name):
    conn = RemoteBulkWriter.S3ConnectParam(
        endpoint=MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        bucket_name=MINIO_BUCKET_NAME,
        secure=MINIO_SECURE
    )
    return RemoteBulkWriter(
        schema=schema,
        remote_path="/" + collection_name,
        connect_param=conn,
        file_type=BulkFileType.PARQUET
    )


def preprocess_multiImg(
    output_dir, pdf_name, embeder, collection_name=None,
    embedding_batch_size=1, commit_every_pages=10, max_pixels=None,
):
    if collection_name is None:
        collection_name = Path(pdf_name).stem  # 用 PDF 文件名（去后缀）作为集合名

    if milvus_client.has_collection(collection_name=collection_name):
        logger.info(f"{collection_name} vector database already exists.")
        retriever = MilvusColbertRetriever(collection_name=collection_name, milvus_client=milvus_client)
    else:
        retriever = MilvusColbertRetriever(collection_name=collection_name, milvus_client=milvus_client)
        retriever.create_collection_multi_img()
        retriever.create_index_multi_img()
        logger.info(f"Created {collection_name} vector database.")

    connections.connect(uri=MILVUS_URI, token=MILVUS_TOKEN)
    collection = Collection(name=collection_name, using="default")
    schema = collection.schema

    writer = _create_bulk_writer(schema, collection_name)

    dataset_path = Path(output_dir)
    image_extensions = {'.jpg', '.jpeg', '.png'}
    page_paths = [
        str(p) for p in sorted(
            dataset_path.glob('*'),
            key=lambda path: (0, int(path.stem)) if path.stem.isdigit() else (1, path.stem),
        )
        if p.is_file() and p.suffix.lower() in image_extensions
    ]

    prefix = upload_minio(
        page_paths, retriever, pdf_name, writer, embeder,
        embedding_batch_size=embedding_batch_size,
        commit_every_pages=commit_every_pages,
        max_pixels=max_pixels,
    )
    if not prefix:
        raise RuntimeError("Image embedding produced no MinIO parquet prefix")
    job_id = upload_milvus(prefix, collection_name)
    milvus_client.release_collection(collection_name=collection_name)
    logger.info(f"Collection='{collection_name}', pdf_name='{pdf_name}', job_id={job_id}")
    return job_id
