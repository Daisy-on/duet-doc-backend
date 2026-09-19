from datetime import UTC, datetime, timedelta

import alibabacloud_oss_v2 as oss

from app.core.config import Settings
from app.services.media_storage import MediaStorage


def test_upload_signature_binds_checksum_type_and_overwrite_protection(monkeypatch):
    config = oss.config.load_default()
    config.region = "cn-chengdu"
    config.endpoint = "https://oss-cn-chengdu.aliyuncs.com"
    config.credentials_provider = oss.credentials.StaticCredentialsProvider(
        "test-id", "test-secret"
    )
    monkeypatch.setattr(
        "app.services.media_storage.create_oss_client", lambda _: oss.Client(config)
    )
    storage = MediaStorage(Settings(oss_media_bucket="test-media"))
    url, headers = storage.sign_upload(
        "media/v1/test.png",
        "image/png",
        "a" * 32,
        datetime.now(UTC) + timedelta(minutes=5),
    )
    normalized = {key.lower(): value for key, value in headers.items()}
    assert normalized["content-md5"] == "qqqqqqqqqqqqqqqqqqqqqg=="
    assert normalized["content-type"] == "image/png"
    assert normalized["x-oss-forbid-overwrite"] == "true"
    assert normalized["cache-control"] == "private, no-store"
    assert "test-media.oss-cn-chengdu.aliyuncs.com/media/v1/test.png" in url
