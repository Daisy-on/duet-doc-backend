"""Run on ECS: python -m app.media_smoke. Only touches a new random test object."""

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import alibabacloud_oss_v2 as oss
import httpx

from app.core.config import Settings
from app.services.media_storage import MediaStorage
from app.services.oss_client import oss_service_error


def main() -> None:
    storage = MediaStorage(Settings())
    key = f"media/v1/_checks/{uuid4()}/pixel.png"
    image = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aRZkAAAAASUVORK5CYII="
    )
    checksum = hashlib.md5(image, usedforsecurity=False).hexdigest()
    expires = datetime.now(UTC) + timedelta(minutes=5)
    uploaded = False
    try:
        url, headers = storage.sign_upload(key, "image/png", checksum, expires)
        with httpx.Client(timeout=30) as client:
            result = client.put(url, headers=headers, content=image)
            if result.status_code != 200:
                raise RuntimeError(f"Upload returned HTTP {result.status_code}")
            uploaded = True
            print("上传成功")
            size, content_type, etag = storage.head(key)
            if (size, content_type, (etag or "").strip('"').lower()) != (
                len(image),
                "image/png",
                checksum,
            ):
                raise RuntimeError("Object metadata mismatch")
            print("文件大小、类型和校验值验证成功")
            result = client.get(storage.sign_read(key, expires))
            if result.status_code != 200 or result.content != image:
                raise RuntimeError(f"Read verification failed: HTTP {result.status_code}")
            print("签名读取成功")
            retry = client.put(url, headers=headers, content=image)
            if retry.status_code != 409:
                raise RuntimeError(f"Overwrite protection failed: HTTP {retry.status_code}")
            print("禁止覆盖验证成功")
    finally:
        if uploaded:
            storage.client.delete_object(oss.DeleteObjectRequest(bucket=storage.bucket, key=key))
            try:
                storage.head(key)
            except Exception as exc:
                service_error = oss_service_error(exc)
                if service_error is None or service_error.status_code != 404:
                    raise
            else:
                raise RuntimeError("Test object still exists after deletion")
            print("测试图片已删除")
        else:
            print(f"若上传请求中断，可在 OSS 检查并清理测试路径：{key}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # SDK/HTTP exception strings may include credentials or signed URLs.
        service_error = oss_service_error(exc)
        detail = (
            f"HTTP {service_error.status_code}，OSS 错误码 {service_error.code}"
            if service_error is not None
            else type(exc).__name__
        )
        print(f"验证失败：{detail}，请检查角色权限、Bucket 和网络配置。")
        raise SystemExit(1) from None
