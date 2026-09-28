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


@app.route("/api/employer/analytics", methods=["GET"])
@require_auth
@require_role("employer")
def get_employer_analytics():
    """Employer analytics — period-aware aggregate stats."""
    try:
        user_id = g.user_id

        period_raw = request.args.get("period", "30")
        period = period_raw if period_raw in ("7", "30", "90") else "30"
        days = int(period)

        summary_row = db.session.execute(
            text("""
                SELECT
                    COUNT(DISTINCT j.id) AS total_jobs,
                    COUNT(DISTINCT CASE WHEN COALESCE(j.status, 'active') = 'active'
                                        THEN j.id END) AS active_jobs
                FROM job_market_data j
                WHERE j.posted_by_user_id = :uid
            """),
            {"uid": user_id}
        ).mappings().first()

        total_jobs  = summary_row["total_jobs"]  or 0
        active_jobs = summary_row["active_jobs"] or 0

        app_row = db.session.execute(
            text("""
                SELECT
                    COUNT(a.id) AS total_applicants,
                    COUNT(CASE WHEN a.status IN ('reviewing','interview') THEN 1 END) AS responded,
                    COUNT(CASE WHEN a.status = 'interview' THEN 1 END) AS interviews,
                    COUNT(CASE WHEN a.status = 'rejected'  THEN 1 END) AS rejected
                FROM job_market_data j
                INNER JOIN applications a ON j.id = a.job_id
                WHERE j.posted_by_user_id = :uid
                  AND a.applied_date >= NOW() - (:days || ' days')::interval
            """),
            {"uid": user_id, "days": days}
        ).mappings().first()

        total_applicants = app_row["total_applicants"] or 0
        responded        = app_row["responded"]        or 0
        interviews       = app_row["interviews"]       or 0
        rejected         = app_row["rejected"]         or 0

        response_rate = (
            round((responded / total_applicants) * 100) if total_applicants else 0
        )

        prev_row = db.session.execute(
            text("""
                SELECT COUNT(a.id) AS prev_count
                FROM job_market_data j
                INNER JOIN applications a ON j.id = a.job_id
                WHERE j.posted_by_user_id = :uid
                  AND a.applied_date >= NOW() - ((:days * 2) || ' days')::interval
                  AND a.applied_date <  NOW() - (:days || ' days')::interval
            """),
            {"uid": user_id, "days": days}
        ).mappings().first()

        prev_count = prev_row["prev_count"] or 0
        if prev_count > 0:
            applicants_delta = round(((total_applicants - prev_count) / prev_count) * 100)
        else:
            applicants_delta = 100 if total_applicants > 0 else 0

        timeline_rows = db.session.execute(
            text("""
                SELECT
                    d::date AS date,
                    COALESCE(cnt.c, 0) AS count
                FROM generate_series(
                    (NOW()::date - (:days - 1)::int),
                    NOW()::date,
                    '1 day'
                ) AS d
                LEFT JOIN (
                    SELECT DATE(a.applied_date) AS day, COUNT(*) AS c
                    FROM job_market_data j
                    INNER JOIN applications a ON j.id = a.job_id
                    WHERE j.posted_by_user_id = :uid
                      AND a.applied_date >= NOW() - (:days || ' days')::interval
                    GROUP BY DATE(a.applied_date)
                ) cnt ON cnt.day = d::date
                ORDER BY d
            """),
            {"uid": user_id, "days": days}
        ).mappings().all()

        applicants_by_date = [
            {"date": row["date"].isoformat() if row["date"] else None,
             "count": int(row["count"])}
            for row in timeline_rows
        ]

        status_rows = db.session.execute(
            text("""
                SELECT a.status, COUNT(*) AS count
                FROM job_market_data j
                INNER JOIN applications a ON j.id = a.job_id
                WHERE j.posted_by_user_id = :uid
                  AND a.applied_date >= NOW() - (:days || ' days')::interval
                GROUP BY a.status
            """),
            {"uid": user_id, "days": days}
        ).mappings().all()

        applicants_by_status = [
            {"status": row["status"], "count": int(row["count"])}
            for row in status_rows
        ]

        top_rows = db.session.execute(
            text("""
                SELECT
                    j.id, j.job_title, j.company_name,
                    COALESCE(j.status, 'active') AS status,
                    COUNT(a.id) AS applicant_count
                FROM job_market_data j
                LEFT JOIN applications a
                    ON a.job_id = j.id
                   AND a.applied_date >= NOW() - (:days || ' days')::interval
                WHERE j.posted_by_user_id = :uid
                GROUP BY j.id, j.job_title, j.company_name, j.status
                ORDER BY applicant_count DESC, j.id DESC
                LIMIT 5
            """),
            {"uid": user_id, "days": days}
        ).mappings().all()

        top_jobs = [
            {"id": row["id"],
             "job_title": row["job_title"],
             "company_name": row["company_name"],
             "status": row["status"],
             "applicants": int(row["applicant_count"])}
            for row in top_rows
        ]

        return {
            "period": days,
            "summary": {
                "total_jobs": total_jobs,
                "active_jobs": active_jobs,
                "total_applicants": total_applicants,
                "response_rate": response_rate,
                "applicants_delta": applicants_delta,
                "interviews": interviews,
                "rejected": rejected,
            },
            "applicants_by_date": applicants_by_date,
            "applicants_by_status": applicants_by_status,
            "top_jobs": top_jobs,
        }, 200

    except Exception as e:
        logger.error("get_employer_analytics_failed", error=str(e), exc_info=True)
        return {"error": {"code": "INTERNAL_ERROR", "message": "เกิดข้อผิดพลาด"}}, 500
@app.route("/api/employer/applications/<int:application_id>/detail", methods=["GET"])
@require_auth
@require_role("employer")
def get_application_snapshot(application_id):
    try:
        app_result = db.session.execute(
            text("""
                SELECT 
                    a.id, a.user_id, a.job_id, a.status,
                    a.full_name, a.email, a.phone, a.location,
                    a.resume_filename, a.resume_url, a.cover_letter,
                    a.applied_date, a.updated_at,
                    j.job_title, j.company_name,
                    j.employment_type, j.experience_level,
                    j.location AS job_location,
                    j.salary_min, j.salary_max,
                    u.resume_url AS user_resume_url
                FROM applications a
                LEFT JOIN job_market_data j ON a.job_id = j.id
                LEFT JOIN users u ON a.user_id = u.id
                WHERE a.id = :aid
            """),
            {"aid": application_id}
        )
        application = app_result.mappings().first()
        if not application:
            return {"error": "Application not found"}, 404

        # ⭐ ตรวจว่า employer เป็นเจ้าของ job ที่ application นี้สมัคร
        job_owner = db.session.execute(
            text("SELECT posted_by_user_id FROM job_market_data WHERE id = :jid"),
            {"jid": application["job_id"]}
        ).first()

        if not job_owner or job_owner[0] != g.user_id:
            return {"error": {"code": "FORBIDDEN", "message": "ไม่มีสิทธิ์"}}, 403

        skills_result = db.session.execute(
            text("""
                SELECT skill_name, skill_level 
                FROM application_skills WHERE application_id = :aid
                ORDER BY id
            """),
            {"aid": application_id}
        )
        skills = [serialize_row(row) for row in skills_result.mappings().all()]

        exp_result = db.session.execute(
            text("""
                SELECT job_title, company_name, location,
                       start_date, end_date, is_current, description
                FROM application_experiences WHERE application_id = :aid
                ORDER BY start_date DESC NULLS FIRST
            """),
            {"aid": application_id}
        )
        experiences = [serialize_row(row) for row in exp_result.mappings().all()]

        edu_result = db.session.execute(
            text("""
                SELECT institution, degree, field_of_study,
                       start_date, end_date, is_current, gpa
                FROM application_educations WHERE application_id = :aid
                ORDER BY start_date DESC NULLS FIRST
            """),
            {"aid": application_id}
        )
        educations = [serialize_row(row) for row in edu_result.mappings().all()]

        # ⭐ Calculate match score from snapshot
        try:
            job_data = db.session.execute(
                text("""
                    SELECT skills_required, experience_level, industry, location
                    FROM job_market_data WHERE id = :jid
                """),
                {"jid": application["job_id"]}
            ).mappings().first()

            user_data_row = db.session.execute(
                text("SELECT industry, location FROM users WHERE id = :uid"),
                {"uid": application["user_id"]}
            ).mappings().first()

            # Calculate total years from snapshot experiences
            total_years = 0.0
            for exp in experiences:
                if exp.get("start_date"):
                    try:
                        start = datetime.fromisoformat(str(exp["start_date"]))
                        if exp.get("is_current") or not exp.get("end_date"):
                            end = datetime.now()
                        else:
                            end = datetime.fromisoformat(str(exp["end_date"]))
                        total_years += (end - start).days / 365.25
                    except (ValueError, TypeError):
                        continue

            candidate_data = {
                "skills": [s["skill_name"] for s in skills],
                "total_years": total_years,
                "industry": (user_data_row["industry"] if user_data_row else "") or "",
                "location": (user_data_row["location"] if user_data_row else "") or "",
            }

            match_result = calculate_match_score_v2(dict(job_data), candidate_data)

            logger.info(
                "match_score_calculated",
                application_id=application_id,
                overall=match_result["overall"],
            )
        except Exception as e:
            logger.error("match_score_calculation_failed", error=str(e), exc_info=True)
            match_result = {
                "overall": 0, "skills_match": 0, "experience_match": 0,
                "industry_match": 0, "matched_skills": [], "missing_skills": [],
                "total_years": 0,
                "weights": {"skills": 0.5, "experience": 0.3, "industry": 0.2},
            }

        return {
            "application": serialize_row(application),
            "skills": skills,
            "experiences": experiences,
            "educations": educations,
            "match": match_result,
        }, 200

    except Exception as e:
        logger.error("get_application_detail_failed", error=str(e), exc_info=True)
        return {"error": str(e)}, 500


@app.route("/api/employer/applications/<int:application_id>/status", methods=["PUT"])
@require_auth
@require_role("employer")
def update_application_status(application_id):
    try:
        data = request.json
        new_status = (data.get("status") or "").lower()

        allowed = {"applied", "reviewing", "interview", "rejected"}
        if new_status not in allowed:
            return {"error": f"Invalid status. Allowed: {', '.join(allowed)}"}, 400

        # ⭐ ดึง application + job_id
        check = db.session.execute(
            text("SELECT id, status, job_id FROM applications WHERE id = :aid"),
            {"aid": application_id}
        ).first()
        if not check:
            return {"error": "Application not found"}, 404

        # ⭐ ตรวจว่า employer เป็นเจ้าของ job
        job_owner = db.session.execute(
            text("SELECT posted_by_user_id FROM job_market_data WHERE id = :jid"),
            {"jid": check[2]}
        ).first()

        if not job_owner or job_owner[0] != g.user_id:
            return {"error": {"code": "FORBIDDEN", "message": "ไม่มีสิทธิ์"}}, 403

        db.session.execute(
            text("""
                UPDATE applications 
                SET status = :status, updated_at = NOW()
                WHERE id = :aid
            """),
            {"status": new_status, "aid": application_id}
        )

        db.session.commit()

        logger.info(
            "application_status_updated",
            user_id=g.user_id,
            application_id=application_id,
            new_status=new_status,
        )

        return {
            "status": "success",
            "message": f"Status updated to '{new_status}'",
            "application_id": application_id,
            "new_status": new_status,
        }, 200

    except Exception as e:
        db.session.rollback()
        logger.error("update_status_failed", error=str(e), exc_info=True)
        return {"error": str(e)}, 500


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


@app.route("/api/employer/profile", methods=["GET"])
@require_auth
@require_role("employer")
def get_employer_profile():
    try:
        user_id = g.user_id

        result = db.session.execute(
            text("""
                SELECT ep.company_name, ep.industry, ep.company_logo,
                       u.email, u.full_name, u.phone, u.location, u.bio
                FROM employer_profiles ep
                JOIN users u ON u.id = ep.user_id
                WHERE ep.user_id = :uid
            """),
            {"uid": user_id}
        ).mappings().first()

        if not result:
            return {"error": "Employer profile not found"}, 404

        return {"profile": dict(result)}, 200

    except Exception as e:
        logger.error("get_employer_profile_failed", error=str(e), exc_info=True)
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

@app.route("/api/employer/analytics/export", methods=["GET"])
@require_auth
@require_role("employer")
def export_employer_analytics():
    """
    Full export data for CSV — summary + applicants + top jobs
    """
    try:
        user_id = g.user_id

        period_raw = request.args.get("period", "30")
        period = period_raw if period_raw in ("7", "30", "90") else "30"
        days = int(period)

        # ── 1. Summary ──
        summary_row = db.session.execute(
            text("""
                SELECT
                    COUNT(DISTINCT j.id) AS total_jobs,
                    COUNT(DISTINCT CASE WHEN COALESCE(j.status, 'active') = 'active'
                                        THEN j.id END) AS active_jobs
                FROM job_market_data j
                WHERE j.posted_by_user_id = :uid
            """),
            {"uid": user_id}
        ).mappings().first()

        app_row = db.session.execute(
            text("""
                SELECT
                    COUNT(a.id) AS total_applicants,
                    COUNT(CASE WHEN LOWER(a.status) IN ('reviewing','interview') THEN 1 END) AS responded,
                    COUNT(CASE WHEN LOWER(a.status) = 'interview' THEN 1 END) AS interviews,
                    COUNT(CASE WHEN LOWER(a.status) = 'rejected'  THEN 1 END) AS rejected
                FROM job_market_data j
                INNER JOIN applications a ON j.id = a.job_id
                WHERE j.posted_by_user_id = :uid
                  AND a.applied_date >= NOW() - (:days || ' days')::interval
            """),
            {"uid": user_id, "days": days}
        ).mappings().first()

        total_applicants = app_row["total_applicants"] or 0
        responded        = app_row["responded"]        or 0
        interviews       = app_row["interviews"]       or 0
        rejected         = app_row["rejected"]         or 0

        response_rate = round((responded / total_applicants) * 100) if total_applicants else 0
        interview_rate = round((interviews / total_applicants) * 100) if total_applicants else 0

        # ── 2. Applicants list (full detail) ──
        applicants_rows = db.session.execute(
            text("""
                SELECT
                    a.id,
                    a.full_name,
                    a.email,
                    a.phone,
                    a.location,
                    a.status,
                    a.applied_date,
                    a.resume_url,
                    a.resume_filename,
                    j.id AS job_id,
                    j.job_title,
                    j.company_name,
                    j.location AS job_location,
                    j.skills_required,
                    j.experience_level,
                    j.industry
                FROM applications a
                INNER JOIN job_market_data j ON j.id = a.job_id
                WHERE j.posted_by_user_id = :uid
                  AND a.applied_date >= NOW() - (:days || ' days')::interval
                ORDER BY a.applied_date DESC
            """),
            {"uid": user_id, "days": days}
        ).mappings().all()

        # ── Bulk fetch: skills + experience for all applicants ──
        applicant_ids = [r["id"] for r in applicants_rows]

        user_map = {}
        if applicant_ids:
            uid_rows = db.session.execute(
                text("SELECT a.id AS app_id, a.user_id FROM applications a WHERE a.id = ANY(:ids)"),
                {"ids": applicant_ids}
            ).mappings().all()
            user_map = {r["app_id"]: r["user_id"] for r in uid_rows}

        user_ids = list(set(user_map.values()))

        skills_map = {}
        if user_ids:
            skill_rows = db.session.execute(
                text("SELECT user_id, skill_name FROM user_skills WHERE user_id = ANY(:uids)"),
                {"uids": user_ids}
            ).mappings().all()
            for r in skill_rows:
                skills_map.setdefault(r["user_id"], []).append(r["skill_name"])

        years_map = {}
        if user_ids:
            years_rows = db.session.execute(
                text("SELECT user_id, COALESCE(SUM(EXTRACT(EPOCH FROM (COALESCE(end_date, NOW()) - start_date)) / (365.25 * 24 * 3600)), 0) AS total_years FROM user_experience WHERE user_id = ANY(:uids) GROUP BY user_id"),
                {"uids": user_ids}
            ).mappings().all()
            years_map = {r["user_id"]: float(r["total_years"] or 0) for r in years_rows}

        applicants = []
        for row in applicants_rows:
            try:
                uid = user_map.get(row["id"])
                cand_data = {
                    "skills": skills_map.get(uid, []),
                    "total_years": years_map.get(uid, 0),
                    "industry": "",
                    "location": row["location"] or "",
                }

                job_dict = {
                    "skills_required": row["skills_required"],
                    "experience_level": row["experience_level"],
                    "industry": row["industry"],
                    "location": row["job_location"],
                }

                match = calculate_match_score_v2(job_dict, cand_data)
                match_score = match["overall"]
                matched_skills = ", ".join(match.get("matched_skills", []))
            except Exception as e:
                logger.warning("match_calc_failed", app_id=row["id"], error=str(e))
                match_score = 0
                matched_skills = ""

            applicants.append({
                "id": row["id"],
                "full_name": row["full_name"],
                "email": row["email"],
                "phone": row["phone"] or "",
                "location": row["location"] or "",
                "job_title": row["job_title"],
                "company_name": row["company_name"],
                "status": row["status"],
                "applied_date": row["applied_date"].isoformat() if row["applied_date"] else "",
                "match_score": match_score,
                "matched_skills": matched_skills,
                "resume_url": row["resume_url"] or "",
                "resume_filename": row["resume_filename"] or "",
            })

        # ── 3. Top jobs ──
        top_rows = db.session.execute(
            text("""
                SELECT
                    j.id, j.job_title, j.company_name,
                    COALESCE(j.status, 'active') AS status,
                    COUNT(a.id) AS applicant_count
                FROM job_market_data j
                LEFT JOIN applications a
                    ON a.job_id = j.id
                   AND a.applied_date >= NOW() - (:days || ' days')::interval
                WHERE j.posted_by_user_id = :uid
                GROUP BY j.id, j.job_title, j.company_name, j.status
                ORDER BY applicant_count DESC, j.id DESC
            """),
            {"uid": user_id, "days": days}
        ).mappings().all()

        top_jobs = [
            {
                "id": r["id"],
                "job_title": r["job_title"],
                "company_name": r["company_name"],
                "status": r["status"],
                "applicants": int(r["applicant_count"]),
            }
            for r in top_rows
        ]

        return {
            "period": days,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "summary": {
                "total_jobs": summary_row["total_jobs"] or 0,
                "active_jobs": summary_row["active_jobs"] or 0,
                "total_applicants": total_applicants,
                "response_rate": response_rate,
                "interview_rate": interview_rate,
                "interviews": interviews,
                "rejected": rejected,
            },
            "applicants": applicants,
            "top_jobs": top_jobs,
        }, 200

    except Exception as e:
        logger.error("export_analytics_failed", error=str(e), exc_info=True)
        return {"error": {"code": "INTERNAL_ERROR", "message": str(e)}}, 500


# =============================================================================
# EMPLOYER ANALYTICS — WIDGETS (Top Matches / Funnel / Activity)
# =============================================================================

@app.route("/api/employer/analytics/widgets", methods=["GET"])
@require_auth
@require_role("employer")
def get_analytics_widgets():
    """Dashboard widgets: top_matches, funnel, recent_activity"""
    try:
        user_id = g.user_id

        period_raw = request.args.get("period", "30")
        period = period_raw if period_raw in ("7", "30", "90") else "30"
        days = int(period)

        # ── 1. Hiring Funnel ──
        funnel_row = db.session.execute(
            text("""
                SELECT
                    COUNT(a.id) AS total,
                    COUNT(CASE WHEN LOWER(a.status) IN ('reviewing','interview') THEN 1 END) AS reviewing,
                    COUNT(CASE WHEN LOWER(a.status) = 'interview' THEN 1 END) AS interview,
                    COUNT(CASE WHEN LOWER(a.status) = 'rejected' THEN 1 END) AS rejected
                FROM job_market_data j
                INNER JOIN applications a ON j.id = a.job_id
                WHERE j.posted_by_user_id = :uid
                  AND a.applied_date >= NOW() - (:days || ' days')::interval
            """),
            {"uid": user_id, "days": days}
        ).mappings().first()

        funnel = [
            {"stage": "Applied",   "count": funnel_row["total"] or 0,     "color": "#38bdf8"},
            {"stage": "Reviewing", "count": funnel_row["reviewing"] or 0, "color": "#f472b6"},
            {"stage": "Interview", "count": funnel_row["interview"] or 0, "color": "#34d399"},
            {"stage": "Rejected",  "count": funnel_row["rejected"] or 0,  "color": "#94a3b8"},
        ]

        # ── 2. Top Matches ──
        top_apps = db.session.execute(
            text("""
                SELECT
                    a.id, a.user_id, a.full_name, a.status, a.applied_date,
                    j.job_title, j.skills_required, j.experience_level,
                    j.industry, j.location AS job_location
                FROM applications a
                INNER JOIN job_market_data j ON j.id = a.job_id
                WHERE j.posted_by_user_id = :uid
                  AND a.applied_date >= NOW() - (:days || ' days')::interval
                ORDER BY a.applied_date DESC
                LIMIT 50
            """),
            {"uid": user_id, "days": days}
        ).mappings().all()

        applicant_ids = [r["id"] for r in top_apps]
        user_map = {}
        if applicant_ids:
            uid_rows = db.session.execute(
                text("SELECT id AS app_id, user_id FROM applications WHERE id = ANY(:ids)"),
                {"ids": applicant_ids}
            ).mappings().all()
            user_map = {r["app_id"]: r["user_id"] for r in uid_rows}

        user_ids = list(set(user_map.values()))
        skills_map = {}
        if user_ids:
            skill_rows = db.session.execute(
                text("SELECT user_id, skill_name FROM user_skills WHERE user_id = ANY(:uids)"),
                {"uids": user_ids}
            ).mappings().all()
            for r in skill_rows:
                skills_map.setdefault(r["user_id"], []).append(r["skill_name"])

        years_map = {}
        if user_ids:
            years_rows = db.session.execute(
                text("""
                    SELECT user_id, COALESCE(SUM(
                        EXTRACT(EPOCH FROM (COALESCE(end_date, NOW()) - start_date)) / (365.25 * 24 * 3600)
                    ), 0) AS total_years
                    FROM user_experience
                    WHERE user_id = ANY(:uids)
                    GROUP BY user_id
                """),
                {"uids": user_ids}
            ).mappings().all()
            years_map = {r["user_id"]: float(r["total_years"] or 0) for r in years_rows}

        scored = []
        for row in top_apps:
            try:
                uid = user_map.get(row["id"])
                cand_data = {
                    "skills": skills_map.get(uid, []),
                    "total_years": years_map.get(uid, 0),
                    "industry": "",
                    "location": "",
                }
                job_dict = {
                    "skills_required": row["skills_required"],
                    "experience_level": row["experience_level"],
                    "industry": row["industry"],
                    "location": row["job_location"],
                }
                match = calculate_match_score_v2(job_dict, cand_data)
                scored.append({
                    "id": row["id"],
                    "full_name": row["full_name"],
                    "job_title": row["job_title"],
                    "status": row["status"],
                    "match_score": match["overall"],
                })
            except Exception as e:
                logger.warning("widget_match_failed", app_id=row["id"], error=str(e))

        top_matches = sorted(scored, key=lambda x: x["match_score"], reverse=True)[:5]

        # ── 3. Recent Activity ──
        activity_rows = db.session.execute(
            text("""
                SELECT
                    a.id, a.full_name, a.status, a.applied_date, a.updated_at,
                    j.job_title
                FROM applications a
                INNER JOIN job_market_data j ON j.id = a.job_id
                WHERE j.posted_by_user_id = :uid
                  AND a.applied_date >= NOW() - (:days || ' days')::interval
                ORDER BY COALESCE(a.updated_at, a.applied_date) DESC
                LIMIT 6
            """),
            {"uid": user_id, "days": days}
        ).mappings().all()

        recent_activity = [
            {
                "id": r["id"],
                "full_name": r["full_name"],
                "status": r["status"],
                "job_title": r["job_title"],
                "applied_date": r["applied_date"].isoformat() if r["applied_date"] else None,
                "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
            }
            for r in activity_rows
        ]

        return {
            "period": days,
            "funnel": funnel,
            "top_matches": top_matches,
            "recent_activity": recent_activity,
        }, 200

    except Exception as e:
        logger.error("analytics_widgets_failed", error=str(e), exc_info=True)
        return {"error": {"code": "INTERNAL_ERROR", "message": str(e)}}, 500


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

