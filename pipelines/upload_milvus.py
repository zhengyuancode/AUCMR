from config.milvus import MilvusColbertRetriever, client as milvus_client
from config.settings import MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY, MINIO_BUCKET_NAME, MINIO_SECURE
from minio import Minio
from minio.error import S3Error


def _create_minio_client():
    return Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=MINIO_SECURE
    )


def upload_milvus(prefix, collection_name):
    parquet_files = []
    minio_client = _create_minio_client()

    try:
        objects = minio_client.list_objects(MINIO_BUCKET_NAME, prefix=prefix, recursive=True)
        for obj in objects:
            if obj.object_name.endswith(".parquet"):
                parquet_files.append([obj.object_name])
    except S3Error as e:
        print(f"MinIO error: {e}")

    retriever = MilvusColbertRetriever(collection_name=collection_name, milvus_client=milvus_client)
    job_id = retriever.bulk_minio_insert_milvus(collection_name, parquet_files)
    milvus_client.release_collection(collection_name=collection_name)
    return job_id


if __name__ == "__main__":
    # 用法: 将 MinIO 中的 parquet 批量导入 Milvus
    # upload_milvus(prefix="your_collection/", collection_name="your_collection")
    pass
