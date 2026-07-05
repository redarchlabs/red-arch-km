"""S3-compatible object storage client (MinIO) for the worker service.

Mirrors ``api.services.storage`` but reads worker settings. The worker only
downloads originals (for extraction), but the full put/get/delete surface is
kept identical so the two clients stay interchangeable.
"""

from __future__ import annotations

import logging

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from worker.config import WorkerSettings

logger = logging.getLogger(__name__)


class StorageClient:
    """Thin wrapper over a boto3 S3 client pointed at MinIO."""

    def __init__(self, settings: WorkerSettings) -> None:
        self._bucket = settings.storage_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.storage_endpoint,
            aws_access_key_id=settings.storage_access_key,
            aws_secret_access_key=settings.storage_secret_key,
            region_name=settings.storage_region,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    def put_object(self, key: str, data: bytes, content_type: str) -> None:
        """Upload ``data`` to ``key``. Raises on failure."""
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )
        except ClientError:
            logger.exception("Failed to put object %s to bucket %s", key, self._bucket)
            raise

    def get_object(self, key: str) -> bytes:
        """Download the object at ``key`` and return its bytes."""
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            body: bytes = response["Body"].read()
            return body
        except ClientError:
            logger.exception("Failed to get object %s from bucket %s", key, self._bucket)
            raise

    def delete_object(self, key: str) -> None:
        """Delete the object at ``key``. Raises on failure."""
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except ClientError:
            logger.exception("Failed to delete object %s from bucket %s", key, self._bucket)
            raise
