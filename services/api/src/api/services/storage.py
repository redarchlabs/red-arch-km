"""S3-compatible object storage client (MinIO) for the API service.

Stores uploaded document originals. The bucket keeps the raw file so the
worker can download it for OCR/extraction and so originals are never lost
("keep originals" decision). Keys follow ``{org_id}/{document_key}/{filename}``.
"""

from __future__ import annotations

import logging

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from api.config import Settings

logger = logging.getLogger(__name__)


class StorageClient:
    """Thin wrapper over a boto3 S3 client pointed at MinIO.

    Constructed per request from Settings — boto3 clients are cheap to create
    and thread-safe, but we avoid sharing one across the async app to keep the
    lifecycle simple.
    """

    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.storage_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.storage_endpoint,
            aws_access_key_id=settings.storage_access_key,
            aws_secret_access_key=settings.storage_secret_key.get_secret_value(),
            region_name=settings.storage_region,
            # MinIO requires SigV4; path-style addressing avoids DNS bucket hosts.
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    def put_object(self, key: str, data: bytes, content_type: str) -> None:
        """Upload ``data`` to ``key``. Raises on failure (caller should 5xx)."""
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
        """Delete the object at ``key``. Raises on failure (caller may swallow)."""
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except ClientError:
            logger.exception("Failed to delete object %s from bucket %s", key, self._bucket)
            raise
