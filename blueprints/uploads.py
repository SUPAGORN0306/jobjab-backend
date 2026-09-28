"""
blueprints/uploads.py — File upload routes (Supabase Storage)

Routes:
- POST   /api/upload/resume
- DELETE /api/resume/<int:user_id>
- POST   /api/upload/avatar
- POST   /api/upload/company-logo
"""
import uuid

from flask import Blueprint, request, g
from sqlalchemy import text
from werkzeug.utils import secure_filename

from core.extensions import db
from core.security import (
    require_auth, require_role,
    is_safe_filename, validate_file_magic,
)
from core.logging_config import get_logger
from utils.files import (
    allowed_file,
    allowed_resume_file,
    ALLOWED_EXTENSIONS,
)
from services.supabase import (
    sb_upload,
    sb_delete,
    sb_public_url,
    extract_supabase_path,
)

bp = Blueprint("uploads", __name__, url_prefix="/api")
logger = get_logger(__name__)


@bp.route("/upload/resume", methods=["POST"])
@require_auth
def upload_resume():
    """อัปโหลด Resume (PDF เท่านั้น) → Supabase Storage"""
    try:
        user_id = g.user_id

        if "resume" not in request.files:
            return {"error": "No file provided"}, 400

        file = request.files["resume"]

        if file.filename == "":
            return {"error": "Empty filename"}, 400

        if not is_safe_filename(file.filename):
            return {"error": "Invalid filename"}, 400

        if not allowed_resume_file(file.filename):
            return {"error": "Only PDF files are allowed for resume"}, 400

        file_bytes = file.read()

        if not validate_file_magic(file_bytes, ["pdf"]):
            return {"error": "File is not a valid PDF"}, 400

        original_filename = file.filename
        safe_filename = secure_filename(original_filename) or f"resume_{user_id}.pdf"
        if not safe_filename.lower().endswith(".pdf"):
            safe_filename += ".pdf"

        storage_filename = f"resume_user_{user_id}_{uuid.uuid4().hex[:8]}.pdf"

        old = db.session.execute(
            text("SELECT resume_url FROM users WHERE id = :uid"),
            {"uid": user_id}
        ).first()

        if old and old[0] and "supabase.co" in old[0]:
            old_path = extract_supabase_path(old[0], "resumes")
            if old_path:
                sb_delete("resumes", old_path)

        upload_res = sb_upload("resumes", storage_filename, file_bytes, "application/pdf")

        if upload_res.status_code not in (200, 201):
            logger.error("supabase_upload_failed", status=upload_res.status_code, response=upload_res.text[:200])
            return {"error": f"Upload failed: {upload_res.text}"}, 500

        resume_url = sb_public_url("resumes", storage_filename)

        db.session.execute(
            text("""
                UPDATE users 
                SET resume_url = :url, 
                    resume_filename = :filename,
                    updated_at = NOW()
                WHERE id = :uid
            """),
            {"url": resume_url, "filename": safe_filename, "uid": user_id}
        )
        db.session.commit()

        logger.info(
            "resume_uploaded",
            user_id=user_id,
            filename=safe_filename,
            size_bytes=len(file_bytes),
        )

        return {
            "status": "success",
            "message": "Resume uploaded successfully",
            "resume_url": resume_url,
            "filename": safe_filename,
        }, 200

    except Exception as e:
        db.session.rollback()
        logger.error("upload_resume_failed", error=str(e), exc_info=True)
        return {"error": str(e)}, 500


@bp.route("/resume/<int:user_id>", methods=["DELETE"])
@require_auth
def delete_resume(user_id):
    """ลบ Resume (จาก Supabase + DB)"""
    if user_id != g.user_id:
        return {"error": {"code": "FORBIDDEN", "message": "ไม่มีสิทธิ์"}}, 403

    try:
        old = db.session.execute(
            text("SELECT resume_url FROM users WHERE id = :uid"),
            {"uid": user_id}
        ).first()

        if not old or not old[0]:
            return {"error": "No resume to delete"}, 404

        if "supabase.co" in old[0]:
            old_path = extract_supabase_path(old[0], "resumes")
            if old_path:
                sb_delete("resumes", old_path)

        db.session.execute(
            text("""
                UPDATE users 
                SET resume_url = NULL, 
                    resume_filename = NULL,
                    updated_at = NOW()
                WHERE id = :uid
            """),
            {"uid": user_id}
        )
        db.session.commit()

        logger.info("resume_deleted", user_id=user_id)

        return {"status": "success", "message": "Resume deleted successfully"}, 200

    except Exception as e:
        db.session.rollback()
        logger.error("delete_resume_failed", error=str(e), exc_info=True)
        return {"error": str(e)}, 500


@bp.route("/upload/avatar", methods=["POST"])
@require_auth
def upload_avatar():
    """อัปโหลด Avatar → Supabase Storage"""
    try:
        user_id = g.user_id

        if "avatar" not in request.files:
            return {"error": "No file provided"}, 400

        file = request.files["avatar"]

        if file.filename == "":
            return {"error": "Empty filename"}, 400

        if not is_safe_filename(file.filename):
            return {"error": "Invalid filename"}, 400

        if not allowed_file(file.filename):
            return {
                "error": f"Invalid file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"
            }, 400

        file_bytes = file.read()
        if not validate_file_magic(file_bytes, ["png", "jpg", "gif", "webp"]):
            return {"error": "File is not a valid image"}, 400

        ext = file.filename.rsplit(".", 1)[1].lower()
        storage_filename = f"avatar_user_{user_id}_{uuid.uuid4().hex[:8]}.{ext}"
        content_type = file.content_type or "image/jpeg"

        old = db.session.execute(
            text("SELECT profile_image FROM users WHERE id = :uid"),
            {"uid": user_id}
        ).first()

        if old and old[0] and "supabase.co" in old[0]:
            old_path = extract_supabase_path(old[0], "avatars")
            if old_path:
                sb_delete("avatars", old_path)

        upload_res = sb_upload("avatars", storage_filename, file_bytes, content_type)

        if upload_res.status_code not in (200, 201):
            logger.error("supabase_upload_failed", status=upload_res.status_code, response=upload_res.text[:200])
            return {"error": f"Upload failed: {upload_res.text}"}, 500

        image_url = sb_public_url("avatars", storage_filename)

        db.session.execute(
            text("""
                UPDATE users 
                SET profile_image = :img, updated_at = NOW()
                WHERE id = :uid
            """),
            {"img": image_url, "uid": user_id}
        )
        db.session.commit()

        logger.info(
            "avatar_uploaded",
            user_id=user_id,
            filename=storage_filename,
            size_bytes=len(file_bytes),
        )

        return {
            "status": "success",
            "message": "Avatar uploaded successfully",
            "image_url": image_url,
            "filename": storage_filename,
        }, 200

    except Exception as e:
        db.session.rollback()
        logger.error("upload_avatar_failed", error=str(e), exc_info=True)
        return {"error": str(e)}, 500


@bp.route("/upload/company-logo", methods=["POST"])
@require_auth
@require_role("employer")
def upload_company_logo():
    """อัปโหลด Company Logo → Supabase Storage"""
    try:
        user_id = g.user_id

        if "logo" not in request.files:
            return {"error": "No file provided"}, 400

        file = request.files["logo"]

        if file.filename == "":
            return {"error": "Empty filename"}, 400

        if not is_safe_filename(file.filename):
            return {"error": "Invalid filename"}, 400

        if not allowed_file(file.filename):
            return {
                "error": f"Invalid file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"
            }, 400

        file_bytes = file.read()
        if not validate_file_magic(file_bytes, ["png", "jpg", "gif", "webp"]):
            return {"error": "File is not a valid image"}, 400

        ext = file.filename.rsplit(".", 1)[1].lower()
        storage_filename = f"logo_user_{user_id}_{uuid.uuid4().hex[:8]}.{ext}"
        content_type = file.content_type or "image/jpeg"

        old = db.session.execute(
            text("SELECT company_logo FROM employer_profiles WHERE user_id = :uid"),
            {"uid": user_id}
        ).first()

        if old and old[0] and "supabase.co" in old[0]:
            old_path = extract_supabase_path(old[0], "company-logos")
            if old_path:
                sb_delete("company-logos", old_path)

        upload_res = sb_upload("company-logos", storage_filename, file_bytes, content_type)

        if upload_res.status_code not in (200, 201):
            logger.error("supabase_upload_failed", status=upload_res.status_code, response=upload_res.text[:200])
            return {"error": f"Upload failed: {upload_res.text}"}, 500

        image_url = sb_public_url("company-logos", storage_filename)

        db.session.execute(
            text("""
                UPDATE employer_profiles 
                SET company_logo = :logo, updated_at = NOW()
                WHERE user_id = :uid
            """),
            {"logo": image_url, "uid": user_id}
        )
        db.session.commit()

        logger.info(
            "company_logo_uploaded",
            user_id=user_id,
            filename=storage_filename,
        )

        return {
            "status": "success",
            "message": "Company logo uploaded successfully",
            "image_url": image_url,
            "filename": storage_filename,
        }, 200

    except Exception as e:
        db.session.rollback()
        logger.error("upload_company_logo_failed", error=str(e), exc_info=True)
        return {"error": str(e)}, 500
