"""Business files always use private object storage."""

from zhiheng.bootstrap.settings import Settings
from zhiheng.infrastructure.file_storage import S3FileStorage
from zhiheng.shared.errors import AppError
from zhiheng.shared.storage import ImportStoragePort


def create_file_storage(settings: Settings) -> ImportStoragePort:
    if (
        not settings.files_s3_endpoint
        or not settings.files_s3_access_key.get_secret_value()
        or not settings.files_s3_secret_key.get_secret_value()
    ):
        raise AppError('file_storage_unconfigured', '请先配置业务 MinIO 地址和应用凭据', 503)
    return S3FileStorage(
        settings.files_s3_endpoint,
        settings.files_s3_access_key.get_secret_value(),
        settings.files_s3_secret_key.get_secret_value(),
        settings.files_s3_bucket,
        region=settings.files_s3_region,
        timeout=settings.files_s3_timeout_seconds,
    )
