from __future__ import annotations

import io
from typing import Any

from minio import Minio
from minio.error import S3Error

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import StorageError

logger = get_logger(__name__)


class MinioClient:
    def __init__(self) -> None:
        settings = get_settings()
        self._client = Minio(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
            region=settings.minio_region,
        )
        # Separate client used only to mint presigned URLs against the
        # externally-reachable endpoint. An explicit region is required so the
        # SDK does not attempt a region-discovery call to that endpoint (which
        # is not reachable from the backend).
        self._presign_client = Minio(
            endpoint=settings.minio_presign_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
            region=settings.minio_region,
        )
        self.bucket_specs = settings.minio_bucket_specs
        self.bucket_artifacts = settings.minio_bucket_artifacts
        self._ensure_buckets()

    def _ensure_buckets(self) -> None:
        for bucket in (self.bucket_specs, self.bucket_artifacts):
            try:
                if not self._client.bucket_exists(bucket):
                    self._client.make_bucket(bucket)
                    logger.info("bucket_created", bucket=bucket)
            except S3Error as exc:
                logger.warning("bucket_ensure_failed", bucket=bucket, error=str(exc))

    async def put_object(
        self,
        bucket: str,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> None:
        import asyncio
        try:
            await asyncio.to_thread(
                self._client.put_object,
                bucket_name=bucket,
                object_name=key,
                data=io.BytesIO(data),
                length=len(data),
                content_type=content_type,
            )
        except S3Error as exc:
            raise StorageError(f"Failed to upload {key} to {bucket}: {exc}") from exc

    async def get_object(self, bucket: str, key: str) -> str:
        import asyncio
        try:
            def _get() -> str:
                response = self._client.get_object(bucket_name=bucket, object_name=key)
                content = response.read().decode("utf-8")
                response.close()
                response.release_conn()
                return content
            return await asyncio.to_thread(_get)
        except S3Error as exc:
            raise StorageError(f"Failed to get {key} from {bucket}: {exc}") from exc

    async def get_presigned_url(self, bucket: str, key: str, expires_seconds: int = 3600) -> str:
        from datetime import timedelta
        import asyncio
        try:
            return await asyncio.to_thread(
                self._presign_client.presigned_get_object,
                bucket_name=bucket,
                object_name=key,
                expires=timedelta(seconds=expires_seconds),
            )
        except S3Error as exc:
            raise StorageError(f"Failed to generate presigned URL for {key}: {exc}") from exc

    async def object_exists(self, bucket: str, key: str) -> bool:
        import asyncio
        try:
            await asyncio.to_thread(self._client.stat_object, bucket, key)
            return True
        except S3Error:
            return False


_minio_client: MinioClient | None = None


def get_minio_client() -> MinioClient:
    global _minio_client
    if _minio_client is None:
        _minio_client = MinioClient()
    return _minio_client
