"""
services/supabase.py — Supabase Storage REST API helpers

Functions:
- _sb_headers: build headers for REST API
- sb_upload: upload file
- sb_delete: delete file
- sb_public_url: build public URL
- extract_supabase_path: extract file path from URL
"""
import requests as http_requests

from core.config import settings
from core.logging_config import get_logger

logger = get_logger(__name__)

# Supabase config (from pydantic settings)
SUPABASE_URL = settings.SUPABASE_URL
SUPABASE_SERVICE_KEY = settings.SUPABASE_SERVICE_KEY


def _sb_headers(content_type=None):
    """Build headers for Supabase REST API."""
    h = {
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "apikey": SUPABASE_SERVICE_KEY,
    }
    if content_type:
        h["Content-Type"] = content_type
    return h


def sb_upload(bucket, path, file_bytes, content_type):
    """
    Upload file to Supabase Storage via REST API.
    Returns Response object (caller checks status_code).
    """
    try:
        url = f"{SUPABASE_URL}/storage/v1/object/{bucket}/{path}"
        headers = _sb_headers(content_type)
        headers["x-upsert"] = "true"

        res = http_requests.post(url, data=file_bytes, headers=headers, timeout=30)

        if res.status_code not in (200, 201):
            logger.warning(
                "sb_upload_failed",
                status=res.status_code,
                bucket=bucket,
                path=path[:50],
                response=res.text[:200],
            )
        return res
    except http_requests.RequestException as e:
        logger.error("sb_upload_exception", bucket=bucket,
                     path=path[:50], error=str(e))
        raise


def sb_delete(bucket, path):
    """Delete file from Supabase Storage."""
    url = f"{SUPABASE_URL}/storage/v1/object/{bucket}/{path}"
    headers = _sb_headers()
    try:
        res = http_requests.delete(url, headers=headers, timeout=15)
        if res.status_code not in (200, 204):
            logger.warning(
                "sb_delete_failed",
                status=res.status_code,
                bucket=bucket,
                path=path[:50],
            )
        return res
    except Exception as e:
        logger.warning("sb_delete_exception", error=str(e))
        return None


def sb_public_url(bucket, path):
    """Build public URL for a file in Supabase Storage."""
    return f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{path}"


def extract_supabase_path(url, bucket):
    """Extract file path from Supabase public URL."""
    try:
        if "supabase.co" not in url:
            return None
        marker = f"/{bucket}/"
        if marker in url:
            return url.split(marker)[-1]
        return None
    except Exception as e:
        logger.warning("extract_supabase_path_failed", error=str(e))
        return None