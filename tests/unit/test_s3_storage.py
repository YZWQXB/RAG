"""S3 兼容存储的边界测试。

生产环境强制使用 s3 后端，而这条路径此前完全没有覆盖。
这里用替身 client 拦在网络层之外，验证参数传递、错误映射与预签名。
"""

from __future__ import annotations

import pytest
from botocore.exceptions import ClientError

from app.providers.storage.local import StorageObjectNotFound
from app.providers.storage.s3 import S3Storage


class _StubClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.objects: dict[str, bytes] = {}
        self.head_error: ClientError | None = None

    def put_object(self, **kwargs) -> None:
        self.calls.append(("put_object", kwargs))
        self.objects[kwargs["Key"]] = kwargs["Body"]

    def get_object(self, **kwargs) -> dict:
        self.calls.append(("get_object", kwargs))
        key = kwargs["Key"]
        if key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": _Body(self.objects[key])}

    def delete_object(self, **kwargs) -> None:
        self.calls.append(("delete_object", kwargs))
        self.objects.pop(kwargs["Key"], None)

    def generate_presigned_url(self, operation: str, **kwargs) -> str:
        self.calls.append(("generate_presigned_url", {"operation": operation, **kwargs}))
        params = kwargs["Params"]
        return f"https://s3.example.com/{params['Bucket']}/{params['Key']}?X-Amz-Signature=stub"

    def head_bucket(self, **kwargs) -> None:
        self.calls.append(("head_bucket", kwargs))
        if self.head_error is not None:
            raise self.head_error

    def create_bucket(self, **kwargs) -> None:
        self.calls.append(("create_bucket", kwargs))


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


@pytest.fixture
def storage(monkeypatch, tmp_path):
    stub = _StubClient()
    monkeypatch.setattr("boto3.client", lambda *args, **kwargs: stub)
    provider = S3Storage()
    provider._client = stub
    return provider, stub


async def test_put_sends_bucket_key_and_content_type(storage):
    provider, stub = storage
    await provider.put("tenant/t/kb/k/doc/v1/a_md", b"hello", "text/markdown")

    name, kwargs = stub.calls[-1]
    assert name == "put_object"
    assert kwargs["Key"] == "tenant/t/kb/k/doc/v1/a_md"
    assert kwargs["Body"] == b"hello"
    assert kwargs["ContentType"] == "text/markdown"


async def test_get_returns_bytes(storage):
    provider, _ = storage
    await provider.put("k", b"payload", "text/plain")
    assert await provider.get("k") == b"payload"


async def test_missing_object_maps_to_storage_not_found(storage):
    provider, _ = storage
    with pytest.raises(StorageObjectNotFound):
        await provider.get("nope")


async def test_delete_removes_object(storage):
    provider, stub = storage
    await provider.put("k", b"x", "text/plain")
    await provider.delete("k")
    assert "k" not in stub.objects


async def test_presign_contains_signature(storage):
    provider, stub = storage
    url = await provider.presign_get("k", expires=120)
    assert url.startswith("https://")
    assert "X-Amz-Signature" in url
    name, kwargs = stub.calls[-1]
    assert kwargs["ExpiresIn"] == 120


async def test_ensure_bucket_creates_when_missing(storage):
    provider, stub = storage
    stub.head_error = ClientError({"Error": {"Code": "404"}}, "HeadBucket")

    await provider.ensure_bucket()
    assert stub.calls[-1][0] == "create_bucket"


async def test_ensure_bucket_skips_when_present(storage):
    provider, stub = storage
    await provider.ensure_bucket()
    assert [name for name, _ in stub.calls] == ["head_bucket"]
