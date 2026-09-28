from flask import Flask, render_template, jsonify, request, Response, g
from flask_cors import CORS
from datetime import datetime
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from dotenv import load_dotenv
import os
import bcrypt
import re
import uuid
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge, HTTPException

import requests as http_requests

load_dotenv()

# ---------- Sprint 1: Security Extensions ----------
from core.config import settings
from core.extensions import db, jwt, limiter, csrf, migrate
from core.logging_config import setup_logging, get_logger
from blueprints import (
    auth_bp, jobs_bp, profile_bp, skills_bp,
    applications_bp, favorites_bp, employer_jobs_bp,
    employer_applications_bp,
    employer_analytics_bp,
    employer_profile_bp,
)

# ---------- Sprint 2: Auth decorators ----------
from core.security import (
    require_auth, require_role, require_owner,
    is_valid_email, is_valid_phone, is_supabase_url, is_safe_filename,
    detect_file_type, validate_file_magic,
    sanitize_text,
)

# ---------- Logging (init first) ----------
setup_logging()
logger = get_logger(__name__)

# ⭐ Supabase Storage config
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    logger.warning("missing_supabase_env")

# =============================================================================
# CONSTANTS
# =============================================================================

ALLOWED_JOB_TITLES = {
    'AI Product Manager',
    'AI Researcher',
    'Computer Vision Engineer',
    'Data Analyst',
    'Data Scientist',
    'ML Engineer',
    'NLP Engineer',
    'Quant Researcher',
}

app = Flask(__name__)

CORS(
    app,
    origins=[
        "http://localhost:5173",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
        "https://joblab-one.vercel.app",
        "https://jobjab-one.vercel.app",
    ],
    supports_credentials=True,  # ⭐ สำหรับ withCredentials: true
    allow_headers=["Content-Type", "Authorization", "X-CSRF-Token"],
    expose_headers=[
        "X-RateLimit-Limit",
        "X-RateLimit-Remaining",
        "X-RateLimit-Reset",
    ],
)

app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# ⭐ Pool options เฉพาะ Postgres — SQLite (test) ไม่รองรับ
_db_url = app.config["SQLALCHEMY_DATABASE_URI"] or ""
if _db_url.startswith(("postgresql://", "postgres://")):
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
        "pool_size": 5,
        "max_overflow": 2,
        "connect_args": {
            "sslmode": "require",
            "connect_timeout": 10,
        },
    }

db.init_app(app)

# =============================================================================
# SPRINT 1: EXTENSIONS + JWT + LOGGING
# =============================================================================

# ---------- JWT config ----------
app.config["JWT_SECRET_KEY"] = settings.JWT_SECRET_KEY
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = settings.JWT_ACCESS_TOKEN_EXPIRES_MINUTES * 60
app.config["JWT_REFRESH_TOKEN_EXPIRES"] = settings.JWT_REFRESH_TOKEN_EXPIRES_DAYS * 24 * 3600
app.config["JWT_TOKEN_LOCATION"] = ["cookies", "headers"]
app.config["JWT_COOKIE_SECURE"] = settings.cookie_secure
app.config["JWT_COOKIE_SAMESITE"] = "None" if settings.cookie_secure else "Lax"
app.config["JWT_COOKIE_CSRF_PROTECT"] = False  # เราจัดการ CSRF เอง

# ⭐ ปิด Flask-WTF CSRF — เราเป็น REST API ใช้ JWT CSRF แทน
app.config["WTF_CSRF_ENABLED"] = False

# ---------- Cookie names (ต้องตรงกับ cookies.py) ----------
app.config["JWT_ACCESS_COOKIE_NAME"] = "access_token"
app.config["JWT_REFRESH_COOKIE_NAME"] = "refresh_token"
app.config["JWT_ACCESS_COOKIE_PATH"] = "/"
app.config["JWT_REFRESH_COOKIE_PATH"] = "/api/auth"

# ---------- Init extensions ----------
migrate.init_app(app, db)
jwt.init_app(app)
limiter.init_app(app)
csrf.init_app(app)

# Exempt auth blueprint จาก Flask-WTF CSRF (ใช้ JWT CSRF แทน)
csrf.exempt(auth_bp)

# ---------- Register blueprints ----------
app.register_blueprint(auth_bp)
app.register_blueprint(jobs_bp)
app.register_blueprint(profile_bp)
app.register_blueprint(skills_bp)
app.register_blueprint(applications_bp)
app.register_blueprint(favorites_bp)
app.register_blueprint(employer_jobs_bp)
app.register_blueprint(employer_applications_bp)
app.register_blueprint(employer_analytics_bp)
app.register_blueprint(employer_profile_bp)

logger.info("app_initialized", env=settings.ENV)

# =============================================================================
# UPLOAD CONFIG
# =============================================================================

from utils.files import (
    allowed_file,
    allowed_resume_file,
    ALLOWED_EXTENSIONS,
    ALLOWED_RESUME_EXTENSIONS,
)

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB

app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE


# =============================================================================
# SUPABASE STORAGE HELPERS (REST API)
# =============================================================================

from services.supabase import (
    sb_upload,
    sb_delete,
    sb_public_url,
    extract_supabase_path,
)


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

from services.serializers import (
    format_salary,
    get_company_initial,
    serialize_row,
)

from services.users import (
    normalize_phone,
    generate_username_from_email,
)


# =============================================================================
# MATCH SCORE
# =============================================================================

from services.match_score import (
    load_user_data,
    calculate_match_score_v2,
    # calculate_match_score_fast,  # ไม่ได้ใช้ใน routes — uncomment ถ้าต้องการ
)


# =============================================================================
# =============================================================================
# HOME / DEBUG
# =============================================================================

@app.route("/")
def home():
    return {"message": "Flask is running"}


@app.route("/api/check-columns")
def check_columns():
    try:
        sql = text("""
            SELECT table_name, column_name, data_type 
            FROM information_schema.columns 
            WHERE table_schema = 'public' 
            ORDER BY table_name, ordinal_position;
        """)
        result = db.session.execute(sql)

        tables_data = {}
        for row in result:
            t_name = row.table_name
            c_info = {"column_name": row.column_name, "data_type": row.data_type}
            if t_name not in tables_data:
                tables_data[t_name] = []
            tables_data[t_name].append(c_info)

        return {"database_structure": tables_data}, 200
    except Exception as e:
        return {"error": str(e)}, 500


@app.route("/debug/routes")
def debug_routes():
    routes = []
    for rule in app.url_map.iter_rules():
        routes.append({
            "endpoint": rule.endpoint,
            "methods": sorted(list(rule.methods - {"HEAD", "OPTIONS"})),
            "path": str(rule)
        })
    return {"total": len(routes), "routes": sorted(routes, key=lambda x: x["path"])}


# =============================================================================
# AUTH
# =============================================================================

@app.route("/api/auth/add-role", methods=["POST"])
def auth_add_role():
    try:
        data = request.json
        user_id = data.get("user_id")
        role = (data.get("role") or "").lower()
        company_name = (data.get("company_name") or "").strip()
        industry = (data.get("industry") or "").strip()

        if not user_id or not role:
            return {"error": "user_id and role required"}, 400

        if role not in ("candidate", "employer"):
            return {"error": "Invalid role"}, 400

        if role == "employer" and not company_name:
            return {"error": "Company name is required"}, 400

        user_check = db.session.execute(
            text("SELECT id, email FROM users WHERE id = :uid"),
            {"uid": user_id}
        ).first()
        if not user_check:
            return {"error": "User not found"}, 404

        existing = db.session.execute(
            text("SELECT id FROM user_roles WHERE user_id = :uid AND role = :r"),
            {"uid": user_id, "r": role}
        ).first()

        if existing:
            return {"error": f"Role '{role}' already exists for this user"}, 409

        db.session.execute(
            text("""
                INSERT INTO user_roles (user_id, role, created_at)
                VALUES (:uid, :r, NOW())
            """),
            {"uid": user_id, "r": role}
        )

        if role == "employer":
            existing_profile = db.session.execute(
                text("SELECT id FROM employer_profiles WHERE user_id = :uid"),
                {"uid": user_id}
            ).first()

            if not existing_profile:
                db.session.execute(
                    text("""
                        INSERT INTO employer_profiles 
                            (user_id, company_name, industry, created_at, updated_at)
                        VALUES (:uid, :cn, :ind, NOW(), NOW())
                    """),
                    {"uid": user_id, "cn": company_name, "ind": industry or None}
                )

        db.session.commit()

        return {
            "status": "success",
            "message": f"Role '{role}' added successfully"
        }, 201

    except IntegrityError as e:
        db.session.rollback()
        return {"error": "Integrity error", "detail": str(e)}, 409
    except Exception as e:
        db.session.rollback()
        logger.error("add_role_failed", error=str(e), exc_info=True)
        return {"error": str(e)}, 500


# =============================================================================
# OTHERS
# =============================================================================

@app.route("/api/all-tables-data")
def all_tables_data():
    try:
        tables_result = db.session.execute(text("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public'
            ORDER BY table_name
        """))
        tables = [row[0] for row in tables_result]

        result = {}
        for table_name in tables:
            try:
                data_result = db.session.execute(
                    text(f"SELECT * FROM {table_name} ORDER BY 1")
                )
                rows = data_result.mappings().all()

                result[table_name] = {
                    "columns": list(rows[0].keys()) if rows else [],
                    "data": [serialize_row(row) for row in rows]
                }
            except Exception as e:
                result[table_name] = {
                    "columns": [],
                    "data": [],
                    "error": str(e)
                }

        return result
    except Exception as e:
        return {"error": str(e)}, 500


@app.route("/jobs-page")
def jobs_page():
    return render_template("jobs.html")


@app.route("/tables")
def tables_page():
    return render_template("table_selector.html")


# =============================================================================
# UPLOAD: Resume → Supabase Storage
# =============================================================================

@app.route("/api/upload/resume", methods=["POST"])
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

        # ⭐ 1. Filename safety (กัน path traversal)
        if not is_safe_filename(file.filename):
            return {"error": "Invalid filename"}, 400

        # ⭐ 2. Extension check
        if not allowed_resume_file(file.filename):
            return {"error": "Only PDF files are allowed for resume"}, 400

        # ⭐ 3. Read file bytes
        file_bytes = file.read()

        # ⭐ 4. Magic bytes check (PDF จริง)
        if not validate_file_magic(file_bytes, ["pdf"]):
            return {"error": "File is not a valid PDF"}, 400

        # ⭐ เก็บชื่อไฟล์จริง
        original_filename = file.filename
        safe_filename = secure_filename(original_filename) or f"resume_{user_id}.pdf"
        if not safe_filename.lower().endswith(".pdf"):
            safe_filename += ".pdf"

        # สร้างชื่อใน Supabase
        storage_filename = f"resume_user_{user_id}_{uuid.uuid4().hex[:8]}.pdf"

        # ลบไฟล์เก่าใน Supabase
        old = db.session.execute(
            text("SELECT resume_url FROM users WHERE id = :uid"),
            {"uid": user_id}
        ).first()

        if old and old[0] and "supabase.co" in old[0]:
            old_path = extract_supabase_path(old[0], "resumes")
            if old_path:
                sb_delete("resumes", old_path)

        # ⭐ อัปโหลดผ่าน REST API
        upload_res = sb_upload("resumes", storage_filename, file_bytes, "application/pdf")

        if upload_res.status_code not in (200, 201):
            logger.error("supabase_upload_failed", status=upload_res.status_code, response=upload_res.text[:200])
            return {"error": f"Upload failed: {upload_res.text}"}, 500

        # Public URL
        resume_url = sb_public_url("resumes", storage_filename)

        # บันทึก DB
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


@app.route("/api/resume/<int:user_id>", methods=["DELETE"])
@require_auth
def delete_resume(user_id):
    """ลบ Resume (จาก Supabase + DB)"""
    # ⭐ ตรวจว่าเป็นเจ้าของ
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


# =============================================================================
# UPLOAD: Avatar → Supabase Storage
# =============================================================================

@app.route("/api/upload/avatar", methods=["POST"])
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

        # ⭐ 1. Filename safety
        if not is_safe_filename(file.filename):
            return {"error": "Invalid filename"}, 400

        # ⭐ 2. Extension check
        if not allowed_file(file.filename):
            return {
                "error": f"Invalid file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"
            }, 400

        # ⭐ 3. Read + magic bytes
        file_bytes = file.read()
        if not validate_file_magic(file_bytes, ["png", "jpg", "gif", "webp"]):
            return {"error": "File is not a valid image"}, 400

        # ⭐ สร้างชื่อไฟล์
        ext = file.filename.rsplit(".", 1)[1].lower()
        storage_filename = f"avatar_user_{user_id}_{uuid.uuid4().hex[:8]}.{ext}"
        content_type = file.content_type or "image/jpeg"

        # ⭐ ลบไฟล์เก่า
        old = db.session.execute(
            text("SELECT profile_image FROM users WHERE id = :uid"),
            {"uid": user_id}
        ).first()

        if old and old[0] and "supabase.co" in old[0]:
            old_path = extract_supabase_path(old[0], "avatars")
            if old_path:
                sb_delete("avatars", old_path)

        # ⭐ อัปโหลดผ่าน REST API
        upload_res = sb_upload("avatars", storage_filename, file_bytes, content_type)

        if upload_res.status_code not in (200, 201):
            logger.error("supabase_upload_failed", status=upload_res.status_code, response=upload_res.text[:200])
            return {"error": f"Upload failed: {upload_res.text}"}, 500

        # ⭐ Public URL
        image_url = sb_public_url("avatars", storage_filename)

        # ⭐ บันทึก DB
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


# =============================================================================
# UPLOAD: Company Logo → Supabase Storage
# =============================================================================

@app.route("/api/upload/company-logo", methods=["POST"])
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

        # ⭐ 1. Filename safety
        if not is_safe_filename(file.filename):
            return {"error": "Invalid filename"}, 400

        # ⭐ 2. Extension check
        if not allowed_file(file.filename):
            return {
                "error": f"Invalid file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"
            }, 400

        # ⭐ 3. Read + magic bytes
        file_bytes = file.read()
        if not validate_file_magic(file_bytes, ["png", "jpg", "gif", "webp"]):
            return {"error": "File is not a valid image"}, 400

        ext = file.filename.rsplit(".", 1)[1].lower()
        storage_filename = f"logo_user_{user_id}_{uuid.uuid4().hex[:8]}.{ext}"
        content_type = file.content_type or "image/jpeg"

        # ⭐ ลบไฟล์เก่า
        old = db.session.execute(
            text("SELECT company_logo FROM employer_profiles WHERE user_id = :uid"),
            {"uid": user_id}
        ).first()

        if old and old[0] and "supabase.co" in old[0]:
            old_path = extract_supabase_path(old[0], "company-logos")
            if old_path:
                sb_delete("company-logos", old_path)

        # ⭐ อัปโหลดผ่าน REST API
        upload_res = sb_upload("company-logos", storage_filename, file_bytes, content_type)

        if upload_res.status_code not in (200, 201):
            logger.error("supabase_upload_failed", status=upload_res.status_code, response=upload_res.text[:200])
            return {"error": f"Upload failed: {upload_res.text}"}, 500

        image_url = sb_public_url("company-logos", storage_filename)

        # ⭐ บันทึก DB
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


# =============================================================================
# MATCH SCORE (single job)
# =============================================================================

@app.route("/api/match-score/<int:job_id>", methods=["GET"])
def get_match_score(job_id):
    try:
        user_id = request.args.get("user_id", type=int)
        if not user_id:
            return {"error": "user_id is required"}, 400

        job = db.session.execute(
            text("""
                SELECT id, skills_required, experience_level, industry
                FROM job_market_data WHERE id = :jid
            """),
            {"jid": job_id}
        ).mappings().first()

        if not job:
            return {"error": "Job not found"}, 404

        user_data = load_user_data(user_id, db.session)
        match = calculate_match_score_v2(dict(job), user_data)
        return match, 200
    except Exception as e:
        return {"error": str(e)}, 500


# ═══════════════════════════════════════════════════════════
# EMPLOYER ANALYTICS EXPORT (full data)
# ═══════════════════════════════════════════════════════════

# =============================================================================
# ERROR HANDLERS
# =============================================================================

# =============================================================================
# ERROR HANDLERS (Sprint 3.3)
# =============================================================================

@app.errorhandler(RequestEntityTooLarge)
def handle_file_too_large(e):
    return {"error": {"code": "FILE_TOO_LARGE", "message": "ไฟล์ใหญ่เกินไป (สูงสุด 5MB)"}}, 413


@app.errorhandler(HTTPException)
def handle_http_exception(e):
    """Handle HTTP errors (401, 403, 404, 405, ...)"""
    logger.warning("http_exception", status=e.code, name=e.name, path=request.path)
    return {
        "error": {
            "code": e.name.upper().replace(" ", "_"),
            "message": e.description or e.name,
        }
    }, e.code


@app.errorhandler(IntegrityError)
def handle_integrity_error(e):
    """Handle database integrity errors"""
    db.session.rollback()
    logger.error("db_integrity_error", error=str(e), exc_info=True)
    return {
        "error": {
            "code": "CONFLICT",
            "message": "ข้อมูลซ้ำหรือขัดแย้ง",
        }
    }, 409


@app.errorhandler(Exception)
def handle_generic_error(e):
    """Handle unexpected errors — ไม่โชว์ str(e) ใน production"""
    db.session.rollback()
    logger.error("unhandled_error", error=str(e), path=request.path, exc_info=True)

    if settings.is_development:
        return {
            "error": {
                "code": "INTERNAL_ERROR",
                "message": str(e),
                "type": type(e).__name__,
            }
        }, 500

    return {
        "error": {
            "code": "INTERNAL_ERROR",
            "message": "เกิดข้อผิดพลาด กรุณาลองใหม่",
        }
    }, 500


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    required = [
        "DATABASE_URL",
        "SUPABASE_URL",
        "SUPABASE_SERVICE_KEY",
    ]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        print(f"⚠️  Missing env vars: {missing}")

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

