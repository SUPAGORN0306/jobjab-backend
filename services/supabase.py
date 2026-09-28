"""
services/supabase.py — Supabase Storage REST API helpers

Functions:
- _sb_headers: สร้าง headers สำหรับ REST API
- sb_upload: อัปโหลดไฟล์
- sb_delete: ลบไฟล์
- sb_public_url: สร้าง public URL
- extract_supabase_path: ดึง path จาก URL
"""
import os

import requests as http_requests

from core.logging_config import get_logger

logger = get_logger(__name__)

# Supabase config
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")


def _sb_headers(content_type=None):
    """สร้าง headers สำหรับ Supabase REST API"""
    h = {
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "apikey": SUPABASE_SERVICE_KEY,
    }
    if content_type:
        h["Content-Type"] = content_type
    return h


def sb_upload(bucket, path, file_bytes, content_type):
    """อัปโหลดไฟล์ขึ้น Supabase Storage ผ่าน REST API"""
    url = f"{SUPABASE_URL}/storage/v1/object/{bucket}/{path}"
    headers = _sb_headers(content_type)
    headers["x-upsert"] = "true"

    res = http_requests.post(url, data=file_bytes, headers=headers, timeout=30)
    return res


def sb_delete(bucket, path):
    """ลบไฟล์ใน Supabase Storage ผ่าน REST API"""
    url = f"{SUPABASE_URL}/storage/v1/object/{bucket}/{path}"
    headers = _sb_headers()
    try:
        res = http_requests.delete(url, headers=headers, timeout=15)
        return res
    except Exception as e:
        logger.warning("sb_delete_failed", error=str(e))
        return None


def sb_public_url(bucket, path):
    """สร้าง Public URL ของไฟล์ใน Supabase Storage"""
    return f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{path}"


def extract_supabase_path(url, bucket):
    """ดึง path ของไฟล์จาก Supabase public URL"""
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
