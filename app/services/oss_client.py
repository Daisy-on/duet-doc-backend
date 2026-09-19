import alibabacloud_oss_v2 as oss
from alibabacloud_credentials.client import Client as CredentialClient
from alibabacloud_credentials.models import Config as CredentialConfig

from app.core.config import Settings


def create_oss_client(settings: Settings) -> oss.Client:
    if not settings.oss_ecs_role_name:
        raise ValueError("OSS ECS role is not configured")
    credentials = CredentialClient(
        CredentialConfig(
            type="ecs_ram_role",
            role_name=settings.oss_ecs_role_name,
            disable_imds_v1=True,
        )
    )

    def load_credentials() -> oss.credentials.Credentials:
        credential = credentials.get_credential()
        if not credential.access_key_id or not credential.access_key_secret:
            raise ValueError("ECS RAM role returned incomplete credentials")
        return oss.credentials.Credentials(
            access_key_id=credential.access_key_id,
            access_key_secret=credential.access_key_secret,
            security_token=credential.security_token,
        )

    config = oss.config.load_default()
    config.credentials_provider = oss.credentials.CredentialsProviderFunc(load_credentials)
    config.region = settings.oss_region
    config.endpoint = settings.oss_endpoint
    return oss.Client(config)
