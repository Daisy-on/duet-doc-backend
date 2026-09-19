import base64
from datetime import datetime

import alibabacloud_oss_v2 as oss

from app.core.config import Settings
from app.services.oss_client import create_oss_client


class MediaStorage:
    def __init__(self, settings: Settings) -> None:
        if not settings.oss_media_bucket:
            raise ValueError("OSS media bucket is not configured")
        self.client = create_oss_client(settings)
        self.bucket = settings.oss_media_bucket

    def sign_upload(
        self,
        key: str,
        content_type: str,
        md5_hex: str,
        expires_at: datetime,
    ) -> tuple[str, dict[str, str]]:
        result = self.client.presign(
            oss.PutObjectRequest(
                bucket=self.bucket,
                key=key,
                content_type=content_type,
                content_md5=base64.b64encode(bytes.fromhex(md5_hex)).decode(),
                forbid_overwrite=True,
                cache_control="private, no-store",
            ),
            expiration=expires_at,
        )
        if not result.url:
            raise ValueError("OSS returned an empty upload URL")
        # PresignResult only returns signed headers; include the cache policy separately.
        headers = dict(result.signed_headers or {})
        headers["Cache-Control"] = "private, no-store"
        return result.url, headers

    def head(self, key: str) -> tuple[int | None, str | None, str | None]:
        result = self.client.head_object(oss.HeadObjectRequest(bucket=self.bucket, key=key))
        return result.content_length, result.content_type, result.etag

    def sign_read(self, key: str, expires_at: datetime) -> str:
        result = self.client.presign(
            oss.GetObjectRequest(bucket=self.bucket, key=key),
            expiration=expires_at,
        )
        if not result.url:
            raise ValueError("OSS returned an empty read URL")
        return result.url

    def delete(self, key: str) -> None:
        self.client.delete_object(oss.DeleteObjectRequest(bucket=self.bucket, key=key))
