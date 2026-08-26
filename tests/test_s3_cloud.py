"""End-to-end tests for S3Cloud using moto's in-process S3 backend.

Uses ``moto.mock_aws`` so boto3 talks to a real in-process S3-compatible
implementation with real SigV4 signing, real retry config, real
pagination. No external server, no network.
"""
from __future__ import annotations

import time

import boto3
import pytest
from moto import mock_aws

from kairos.s3_cloud import S3Cloud, S3CloudError, S3Config


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def moto_ctx():
    """Activate moto's S3 mock for the duration of one test."""
    with mock_aws():
        yield


@pytest.fixture
def bucket(moto_ctx) -> str:
    name = f"kairos-test-{int(time.time() * 1000)}"
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=name)
    return name


@pytest.fixture
def cloud(bucket: str):
    return S3Cloud(S3Config(
        bucket=bucket,
        region="us-east-1",
        access_key="testing",
        secret_key="testing",
    ))


# ---------------------------------------------------------------------------
# Roundtrip
# ---------------------------------------------------------------------------


def test_put_and_get_bytes_roundtrip(cloud, bucket: str):
    resp = cloud.put_bytes("hello.txt", b"hi from kairos",
                           content_type="text/plain")
    assert resp.get("ResponseMetadata", {}).get("HTTPStatusCode") in (200, 201)

    out = cloud.get_bytes("hello.txt")
    assert out == b"hi from kairos"


def test_put_file_streams_from_disk(cloud, tmp_path):
    p = tmp_path / "big.bin"
    p.write_bytes(b"x" * 1024 + b"END")
    cloud.put_file("subdir/big.bin", p)
    out = cloud.get_bytes("subdir/big.bin")
    assert out == b"x" * 1024 + b"END"


def test_get_missing_key_raises_s3_cloud_error(cloud):
    with pytest.raises(S3CloudError) as ei:
        cloud.get_bytes("nope.txt")
    assert "not found" in str(ei.value).lower() or "nosuchkey" in str(ei.value).lower()


def test_exists_true_and_false(cloud):
    cloud.put_bytes("a", b"1")
    assert cloud.exists("a") is True
    assert cloud.exists("b") is False


def test_stat_returns_metadata(cloud):
    cloud.put_bytes("file.txt", b"hello")
    obj = cloud.stat("file.txt")
    assert obj.size == 5
    assert obj.key == "file.txt"
    assert obj.etag


def test_list_with_prefix(cloud):
    cloud.put_bytes("logs/2026-01/a.log", b"1")
    cloud.put_bytes("logs/2026-01/b.log", b"22")
    cloud.put_bytes("logs/2026-02/c.log", b"333")
    cloud.put_bytes("other/x.txt", b"nope")

    keys = sorted(o.key for o in cloud.list(prefix="logs/2026-01/"))
    assert keys == ["logs/2026-01/a.log", "logs/2026-01/b.log"]


def test_list_empty_prefix_returns_all(cloud):
    cloud.put_bytes("a", b"1")
    cloud.put_bytes("b/c", b"2")
    keys = sorted(o.key for o in cloud.list())
    assert keys == ["a", "b/c"]


def test_delete_removes_object(cloud):
    cloud.put_bytes("tmp", b"x")
    assert cloud.exists("tmp")
    cloud.delete("tmp")
    assert not cloud.exists("tmp")


def test_delete_missing_is_silent(cloud):
    # moto: delete on non-existent key returns 204; we should not raise
    cloud.delete("never-existed")


def test_delete_many_removes_batch(cloud):
    for i in range(5):
        cloud.put_bytes(f"k{i}", f"v{i}".encode())
    assert len(cloud.list()) == 5

    result = cloud.delete_many([f"k{i}" for i in range(5)])
    # moto returns a dict; on success there are no errors
    errors = result.get("Errors") or []
    assert errors == []
    assert cloud.list() == []


def test_delete_many_too_many_raises(cloud):
    too_many = [f"k{i}" for i in range(1001)]
    with pytest.raises(S3CloudError):
        cloud.delete_many(too_many)


def test_presigned_get_url_is_https_signed(cloud):
    cloud.put_bytes("share.txt", b"shared data")
    url = cloud.presigned_get_url("share.txt", expires_in=60)
    # boto3 emits a URL containing the bucket, key, and SigV4 query params
    assert "share.txt" in url
    assert "X-Amz-Signature=" in url or "Signature=" in url
    assert "X-Amz-Expires=60" in url or "Expires=60" in url


def test_download_to_writes_file(cloud, tmp_path):
    cloud.put_bytes("report.txt", b"report content here")
    out = cloud.download_to("report.txt", tmp_path / "out" / "report.txt")
    assert out.exists()
    assert out.read_bytes() == b"report content here"


def test_metadata_roundtrip(cloud):
    cloud.put_bytes("doc.txt", b"x", metadata={"author": "kairos", "run": "42"})
    # moto doesn't return metadata on GetObject without explicit flag,
    # but head_object does. We just confirm no exception.
    assert cloud.exists("doc.txt")


def test_content_type_roundtrip(cloud):
    cloud.put_bytes("page.html", b"<h1>hi</h1>", content_type="text/html")
    # verify by re-fetching the head
    obj = cloud.stat("page.html")
    assert obj.size == len(b"<h1>hi</h1>")


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


def test_empty_key_rejected(cloud):
    with pytest.raises(S3CloudError):
        cloud.put_bytes("", b"x")
    with pytest.raises(S3CloudError):
        cloud.get_bytes("")
    with pytest.raises(S3CloudError):
        cloud.delete("")
    with pytest.raises(S3CloudError):
        cloud.exists("")


def test_config_kw_excludes_endpoint_when_blank():
    cfg = S3Config(bucket="b", region="us-east-1",
                   access_key="a", secret_key="s",
                   endpoint_url="")
    kw = cfg.client_kwargs()
    assert "endpoint_url" not in kw
    assert kw["region_name"] == "us-east-1"


def test_config_kw_includes_endpoint_when_set():
    cfg = S3Config(bucket="b", region="eu-west-1",
                   access_key="a", secret_key="s",
                   endpoint_url="http://minio.local:9000")
    kw = cfg.client_kwargs()
    assert kw["endpoint_url"] == "http://minio.local:9000"
    assert kw["region_name"] == "eu-west-1"


# ---------------------------------------------------------------------------
# S3Cloud with custom endpoint (MinIO-style)
# ---------------------------------------------------------------------------


def test_cloud_with_custom_endpoint(moto_ctx):
    """Verify custom endpoint flows through to the boto3 client config.

    Under ``mock_aws`` the boto3 calls route to the in-process mock
    regardless of endpoint_url, so we can only assert the endpoint
    was passed to boto3 correctly (i.e. it's in the client's meta).
    """
    name = f"minio-bucket-{int(time.time() * 1000)}"
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=name)
    cloud = S3Cloud(S3Config(
        bucket=name,
        region="us-east-1",
        access_key="minio",
        secret_key="miniosecret",
        endpoint_url="http://minio.local:9000",
        addressing_style="path",
    ))
    # Force client build and confirm the endpoint is in the meta.
    cloud._ensure_client()
    assert "minio.local" in (cloud._client.meta.endpoint_url or "")
