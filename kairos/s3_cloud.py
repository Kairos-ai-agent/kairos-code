"""S3-compatible object storage for Kairos.

Wraps ``boto3`` to provide a thin, opinionated API for the four
operations Kairos actually needs: PUT (upload a file/blob), GET
(download to path or bytes), LIST (enumerate with prefix), DELETE.
Works against any S3-compatible endpoint:

  - AWS S3           (default endpoint, IAM creds)
  - MinIO            (custom endpoint_url, static creds)
  - Cloudflare R2    (custom endpoint_url, IAM creds)
  - Backblaze B2     (custom endpoint_url, app key)
  - Wasabi / DigitalOcean Spaces / etc.

The implementation deliberately uses boto3's high-level resource
API for the common path and falls back to ``botocore`` low-level
operations when a specific feature (presigned URL, copy) is needed.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Dict, Iterator, List, Optional, Union

logger = logging.getLogger(__name__)


class S3CloudError(RuntimeError):
    """Raised when an S3 operation fails (auth, network, key not found, ...)."""


@dataclass
class S3Config:
    """Connection parameters.

    ``endpoint_url`` defaults to AWS S3. Set it to point at MinIO, R2,
    B2, Wasabi, or any other S3-compatible store. ``region`` is
    required even for custom endpoints (boto3 sanity check).
    """

    bucket: str
    region: str = "us-east-1"
    access_key: str = ""
    secret_key: str = ""
    endpoint_url: str = ""  # empty = AWS
    # Path-style addressing (required by MinIO); virtual-hosted is the AWS default.
    addressing_style: str = "auto"  # one of: auto, virtual, path
    # Server-side encryption hint. Valid values: "AES256", "aws:kms", "" (none).
    server_side_encryption: str = ""
    # Optional KMS key id when server_side_encryption == "aws:kms"
    sse_kms_key_id: str = ""

    def client_kwargs(self) -> Dict[str, Any]:
        """Return kwargs ready to pass to ``boto3.client("s3", **kwargs)``."""
        kw: Dict[str, Any] = {
            "region_name": self.region,
            "config": _S3Config(
                addressing_style=self.addressing_style,
            ),
        }
        if self.access_key and self.secret_key:
            kw["aws_access_key_id"] = self.access_key
            kw["aws_secret_access_key"] = self.secret_key
        if self.endpoint_url:
            kw["endpoint_url"] = self.endpoint_url
        return kw


# Importable alias so the inner config can be patched in tests without
# leaking botocore into the public namespace at import time.
def _S3Config(addressing_style: str = "auto"):
    from botocore.client import Config
    return Config(
        signature_version="s3v4",  # SigV4 — required for SSE-KMS, newer regions
        s3={"addressing_style": addressing_style},
        retries={"max_attempts": 3, "mode": "standard"},
    )


@dataclass
class S3Object:
    """A small descriptor for an S3 object (used in list results)."""
    key: str
    size: int
    etag: str
    last_modified: str  # ISO-8601
    storage_class: str = "STANDARD"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "size": self.size,
            "etag": self.etag,
            "last_modified": self.last_modified,
            "storage_class": self.storage_class,
        }


@dataclass
class S3Cloud:
    """S3-compatible object store.

    Example::

        cloud = S3Cloud(S3Config(
            bucket="my-bucket",
            region="us-east-1",
            access_key=os.environ["AWS_ACCESS_KEY_ID"],
            secret_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        ))
        cloud.put_bytes("hello.txt", b"hi")
        print(cloud.get_bytes("hello.txt"))  # b"hi"
    """

    config: S3Config
    _client: Any = field(default=None, init=False, repr=False)

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import boto3  # type: ignore
        except ImportError as exc:  # pragma: no cover - dep missing
            raise S3CloudError(
                "boto3 is not installed; pip install boto3"
            ) from exc
        try:
            self._client = boto3.client("s3", **self.config.client_kwargs())
        except Exception as exc:
            raise S3CloudError(f"failed to build S3 client: {exc}") from exc
        return self._client

    # -- writes ----------------------------------------------------------

    def put_bytes(self, key: str, data: bytes,
                  content_type: str = "application/octet-stream",
                  metadata: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Upload raw bytes under *key*. Returns the PutObject response."""
        if not key:
            raise S3CloudError("key must be non-empty")
        client = self._ensure_client()
        kwargs: Dict[str, Any] = {
            "Bucket": self.config.bucket,
            "Key": key,
            "Body": data,
            "ContentType": content_type,
        }
        if self.config.server_side_encryption:
            kwargs["ServerSideEncryption"] = self.config.server_side_encryption
            if self.config.sse_kms_key_id:
                kwargs["SSEKMSKeyId"] = self.config.sse_kms_key_id
        if metadata:
            kwargs["Metadata"] = dict(metadata)
        try:
            return client.put_object(**kwargs)
        except Exception as exc:
            raise S3CloudError(f"put_object({key!r}) failed: {exc}") from exc

    def put_file(self, key: str, local_path: Union[str, Path],
                 content_type: str = "application/octet-stream") -> Dict[str, Any]:
        """Upload a local file. Streams the body so large files don't OOM."""
        p = Path(local_path)
        if not p.is_file():
            raise S3CloudError(f"local file not found: {p}")
        client = self._ensure_client()
        extra: Dict[str, Any] = {"ContentType": content_type}
        if self.config.server_side_encryption:
            extra["ServerSideEncryption"] = self.config.server_side_encryption
        try:
            return client.upload_file(str(p), self.config.bucket, key, ExtraArgs=extra)
        except Exception as exc:
            raise S3CloudError(f"upload_file({key!r}) failed: {exc}") from exc

    # -- reads -----------------------------------------------------------

    def get_bytes(self, key: str) -> bytes:
        """Download an object as raw bytes."""
        if not key:
            raise S3CloudError("key must be non-empty")
        client = self._ensure_client()
        try:
            resp = client.get_object(Bucket=self.config.bucket, Key=key)
            return resp["Body"].read()
        except client.exceptions.NoSuchKey as exc:
            raise S3CloudError(f"object not found: {key!r}") from exc
        except Exception as exc:
            # boto3 raises ClientError; the exception type may differ
            # across botocore versions, so we sniff the error code.
            code = getattr(getattr(exc, "response", {}), "get", lambda *_: None)("Error", {}).get("Code") if hasattr(exc, "response") else None
            if code in ("NoSuchKey", "404"):
                raise S3CloudError(f"object not found: {key!r}") from exc
            raise S3CloudError(f"get_object({key!r}) failed: {exc}") from exc

    def download_to(self, key: str, local_path: Union[str, Path]) -> Path:
        """Stream an object directly to disk."""
        p = Path(local_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        client = self._ensure_client()
        try:
            client.download_file(self.config.bucket, key, str(p))
        except Exception as exc:
            code = getattr(getattr(exc, "response", {}), "get", lambda *_: None)("Error", {}).get("Code") if hasattr(exc, "response") else None
            if code in ("NoSuchKey", "404"):
                raise S3CloudError(f"object not found: {key!r}") from exc
            raise S3CloudError(f"download_file({key!r}) failed: {exc}") from exc
        return p

    def exists(self, key: str) -> bool:
        """Return True iff the object exists."""
        if not key:
            raise S3CloudError("key must be non-empty")
        client = self._ensure_client()
        try:
            client.head_object(Bucket=self.config.bucket, Key=key)
            return True
        except Exception as exc:
            code = getattr(getattr(exc, "response", {}), "get", lambda *_: None)("Error", {}).get("Code") if hasattr(exc, "response") else None
            if code in ("NoSuchKey", "404", "NotFound"):
                return False
            # network / auth errors are NOT "not found"
            raise S3CloudError(f"head_object({key!r}) failed: {exc}") from exc

    def stat(self, key: str) -> S3Object:
        """Return object metadata."""
        if not key:
            raise S3CloudError("key must be non-empty")
        client = self._ensure_client()
        try:
            resp = client.head_object(Bucket=self.config.bucket, Key=key)
        except Exception as exc:
            code = getattr(getattr(exc, "response", {}), "get", lambda *_: None)("Error", {}).get("Code") if hasattr(exc, "response") else None
            if code in ("NoSuchKey", "404", "NotFound"):
                raise S3CloudError(f"object not found: {key!r}") from exc
            raise S3CloudError(f"head_object({key!r}) failed: {exc}") from exc
        return S3Object(
            key=key,
            size=int(resp.get("ContentLength", 0)),
            etag=str(resp.get("ETag", "")).strip('"'),
            last_modified=str(resp.get("LastModified", "")),
            storage_class=str(resp.get("StorageClass", "STANDARD")),
        )

    # -- list / delete ---------------------------------------------------

    def list(self, prefix: str = "", max_keys: int = 1000) -> List[S3Object]:
        """Enumerate objects with the given prefix.

        For buckets with >1000 objects per prefix, boto3 paginates
        transparently up to ``max_keys`` total.
        """
        client = self._ensure_client()
        out: List[S3Object] = []
        try:
            paginator = client.get_paginator("list_objects_v2")
            for page in paginator.paginate(
                Bucket=self.config.bucket,
                Prefix=prefix,
                PaginationConfig={"MaxItems": max_keys},
            ):
                for obj in page.get("Contents", []) or []:
                    out.append(S3Object(
                        key=obj["Key"],
                        size=int(obj.get("Size", 0)),
                        etag=str(obj.get("ETag", "")).strip('"'),
                        last_modified=str(obj.get("LastModified", "")),
                        storage_class=str(obj.get("StorageClass", "STANDARD")),
                    ))
        except Exception as exc:
            raise S3CloudError(f"list_objects_v2(prefix={prefix!r}) failed: {exc}") from exc
        return out

    def delete(self, key: str) -> None:
        """Delete a single object. No-op if it doesn't exist."""
        if not key:
            raise S3CloudError("key must be non-empty")
        client = self._ensure_client()
        try:
            client.delete_object(Bucket=self.config.bucket, Key=key)
        except Exception as exc:
            raise S3CloudError(f"delete_object({key!r}) failed: {exc}") from exc

    def delete_many(self, keys: List[str]) -> Dict[str, Any]:
        """Delete up to 1000 keys in a single request."""
        if not keys:
            return {"Deleted": [], "Errors": []}
        if len(keys) > 1000:
            raise S3CloudError("delete_many supports at most 1000 keys per call")
        client = self._ensure_client()
        try:
            return client.delete_objects(
                Bucket=self.config.bucket,
                Delete={"Objects": [{"Key": k} for k in keys], "Quiet": False},
            )
        except Exception as exc:
            raise S3CloudError(f"delete_objects failed: {exc}") from exc

    # -- presigned URLs --------------------------------------------------

    def presigned_get_url(self, key: str, expires_in: int = 3600) -> str:
        """Return a time-limited HTTPS URL the holder can GET without creds."""
        if not key:
            raise S3CloudError("key must be non-empty")
        client = self._ensure_client()
        try:
            return client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.config.bucket, "Key": key},
                ExpiresIn=expires_in,
            )
        except Exception as exc:
            raise S3CloudError(f"presigned_get_url({key!r}) failed: {exc}") from exc
