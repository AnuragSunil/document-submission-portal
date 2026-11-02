import io
import logging
import re
from typing import Optional
import boto3
from botocore.exceptions import ClientError

from .config import AWS_REGION, BUCKET_NAME

logger = logging.getLogger(__name__)


def get_s3_client():
    """Create and return a boto3 S3 client."""
    return boto3.client("s3", region_name=AWS_REGION)


def upload_to_s3(file_data: bytes, s3_key: str, content_type: str, bucket_name: Optional[str] = None) -> None:
    """Upload byte content to private S3 bucket with SSE-S3 encryption."""
    target_bucket = bucket_name or BUCKET_NAME
    client = get_s3_client()
    client.put_object(
        Bucket=target_bucket,
        Key=s3_key,
        Body=file_data,
        ContentType=content_type,
        ServerSideEncryption="AES256",
    )


def delete_from_s3(s3_key: str, bucket_name: Optional[str] = None) -> None:
    """Delete an object from S3."""
    target_bucket = bucket_name or BUCKET_NAME
    client = get_s3_client()
    try:
        client.delete_object(Bucket=target_bucket, Key=s3_key)
    except Exception as e:
        logger.error("Failed to delete S3 object: %s", type(e).__name__)


def generate_presigned_download_url(s3_key: str, original_filename: str, bucket_name: Optional[str] = None, expires_in: int = 60) -> str:
    """
    Generate a short-lived presigned download URL with safe Content-Disposition.
    Never logs the generated URL.
    """
    target_bucket = bucket_name or BUCKET_NAME
    client = get_s3_client()

    # Sanitize original_filename for header safety (remove newlines, double quotes)
    safe_name = re.sub(r'[\r\n"]', '_', original_filename)
    content_disposition = f'attachment; filename="{safe_name}"'

    url = client.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": target_bucket,
            "Key": s3_key,
            "ResponseContentDisposition": content_disposition,
        },
        ExpiresIn=expires_in,
    )
    return url


def check_s3_health(bucket_name: Optional[str] = None) -> bool:
    """Verify S3 bucket accessibility."""
    target_bucket = bucket_name or BUCKET_NAME
    if not target_bucket:
        return False
    client = get_s3_client()
    try:
        client.head_bucket(Bucket=target_bucket)
        return True
    except ClientError:
        return False
