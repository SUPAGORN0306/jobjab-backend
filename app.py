from flask import Flask, render_template, jsonify, request, Response, g
from flask_cors import CORS
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
from config import settings
from extensions import db, jwt, limiter, csrf, migrate
from logging_config import setup_logging, get_logger
from auth import auth_bp

# ---------- Sprint 2: Auth decorators ----------
from security import (
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

logger.info("app_initialized", env=settings.ENV)

# =============================================================================
# UPLOAD CONFIG
# =============================================================================

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
ALLOWED_RESUME_EXTENSIONS = {'pdf'}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB

app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE


def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def allowed_resume_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_RESUME_EXTENSIONS


# =============================================================================
# SUPABASE STORAGE HELPERS (REST API)
# =============================================================================

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


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def format_salary(min_val, max_val):
    try:
        if min_val is None and max_val is None:
            return "N/A"
        min_val = float(min_val) if min_val else 0
        max_val = float(max_val) if max_val else 0
        return f"${int(min_val):,} - ${int(max_val):,}"
    except:
        return "N/A"


def get_company_initial(company_name):
    if not company_name:
        return "J"
    return company_name.strip()[0].upper()


def serialize_row(row):
    result = dict(row)
    for key, value in result.items():
        if hasattr(value, 'isoformat'):
            result[key] = value.isoformat()
    return result


def generate_username_from_email(email, db_session):
    base = email.split("@")[0].lower()
    base = re.sub(r"[^a-z0-9_]", "_", base)
    username = base
    counter = 1
    while True:
        exists = db_session.execute(
            text("SELECT id FROM users WHERE username = :u"),
            {"u": username}
        ).first()
        if not exists:
            return username
        counter += 1
        username = f"{base}_{counter}"


# =============================================================================
# MATCH SCORE
# =============================================================================

INDUSTRY_RELATED = {
    "tech": ["e-commerce", "finance", "education"],
    "finance": ["tech", "e-commerce"],
    "e-commerce": ["tech", "retail", "finance"],
    "retail": ["e-commerce"],
    "healthcare": ["education"],
    "education": ["healthcare", "tech"],
    "automotive": ["tech"],
}


def load_user_data(user_id, db_session):
    try:
        user = db_session.execute(
            text("SELECT industry FROM users WHERE id = :uid"),
            {"uid": user_id}
        ).mappings().first()

        skills_result = db_session.execute(
            text("SELECT skill_name FROM user_skills WHERE user_id = :uid"),
            {"uid": user_id}
        ).fetchall()

        exp_result = db_session.execute(
            text("""
                SELECT 
                    COALESCE(SUM(
                        EXTRACT(EPOCH FROM (
                            COALESCE(end_date, NOW()) - start_date
                        )) / (365.25 * 24 * 3600)
                    ), 0) AS total_years
                FROM user_experience 
                WHERE user_id = :uid
            """),
            {"uid": user_id}
        ).mappings().first()

        return {
            "industry": (user["industry"] or "").lower().strip() if user else "",
            "skills": [s[0].lower().strip() for s in skills_result],
            "total_years": float(exp_result["total_years"] or 0) if exp_result else 0,
        }
    except Exception as e:
        logger.error("load_user_data_failed", error=str(e), exc_info=True)
        return {"industry": "", "skills": [], "total_years": 0}


def calculate_match_score_fast(job_row, user_data):
    try:
        job_skills_raw = (job_row.get("skills_required") or "").lower()
        job_skills = [s.strip() for s in job_skills_raw.split(",") if s.strip()]
        user_skills = user_data["skills"]

        matched_skills = []
        missing_skills = []

        for js in job_skills:
            matched = False
            for us in user_skills:
                if js in us or us in js:
                    matched = True
                    matched_skills.append(js)
                    break
            if not matched:
                missing_skills.append(js)

        skills_match = round((len(matched_skills) / len(job_skills)) * 100) if job_skills else 0

        total_years = user_data["total_years"]
        level_requirements = {"entry": 0, "junior": 0, "mid": 2, "senior": 5, "lead": 7}
        job_level = (job_row.get("experience_level") or "mid").lower()
        required_years = level_requirements.get(job_level, 2)

        if required_years == 0:
            exp_match = 100
        else:
            exp_match = min(round((total_years / required_years) * 100), 100)

        user_industry = user_data["industry"]
        job_industry = (job_row.get("industry") or "").lower().strip()

        if not user_industry or not job_industry:
            industry_match = 50
        elif user_industry == job_industry:
            industry_match = 100
        elif job_industry in INDUSTRY_RELATED.get(user_industry, []):
            industry_match = 75
        elif user_industry in INDUSTRY_RELATED.get(job_industry, []):
            industry_match = 75
        else:
            industry_match = 40

        overall = round(skills_match * 0.5 + exp_match * 0.3 + industry_match * 0.2)

        return {
            "overall": overall,
            "skills_match": skills_match,
            "experience_match": exp_match,
            "industry_match": industry_match,
            "matched_skills": matched_skills,
            "missing_skills": missing_skills,
            "total_years": round(total_years, 1),
        }
    except Exception as e:
        logger.error("calculate_match_score_failed", error=str(e), exc_info=True)
        return {
            "overall": 0, "skills_match": 0, "experience_match": 0,
            "industry_match": 0, "matched_skills": [], "missing_skills": [],
            "total_years": 0,
        }


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
# JOBS
# =============================================================================

@app.route("/api/jobs")
def get_jobs():
    try:
        user_id = request.args.get("user_id", type=int)

        result = db.session.execute(text("""
            SELECT 
                j.id, j.company_name, j.industry, j.job_title,
                j.skills_required, j.experience_level, j.employment_type,
                j.location, j.posted_date, j.company_size, j.tools_preferred,
                j.salary_min, j.salary_max,
                COUNT(a.id) AS applicant_count
            FROM job_market_data j
            LEFT JOIN applications a ON j.id = a.job_id
            GROUP BY j.id
            ORDER BY j.id
        """))
        rows = result.mappings().all()

        user_data = load_user_data(user_id, db.session) if user_id else None

        jobs_list = []
        for row in rows:
            job_dict = dict(row)
            count = job_dict.get("applicant_count", 0)

            if user_id and user_data:
                match = calculate_match_score_fast(job_dict, user_data)
                match_score = match["overall"]
                match_breakdown = {
                    "skills": match["skills_match"],
                    "experience": match["experience_match"],
                    "industry": match["industry_match"],
                }
                matched_skills = match.get("matched_skills", [])
                missing_skills = match.get("missing_skills", [])
            else:
                match_score = 75
                match_breakdown = {"skills": 75, "experience": 75, "industry": 75}
                matched_skills = []
                missing_skills = []

            mapped_job = {
                "id": job_dict.get("id"),
                "title": job_dict.get("job_title") or "Unknown Title",
                "company": job_dict.get("company_name") or "Unknown Company",
                "type": job_dict.get("employment_type") or "Full-time",
                "level": job_dict.get("experience_level") or "Mid",
                "location": job_dict.get("location") or "N/A",
                "industry": job_dict.get("industry") or "Tech",
                "salary": format_salary(
                    job_dict.get("salary_min"),
                    job_dict.get("salary_max")
                ),
                "applicants": f"{count} applicant{'' if count == 1 else 's'}",
                "applicant_count": count,
                "match_score": match_score,
                "match_breakdown": match_breakdown,
                "matched_skills": matched_skills,
                "missing_skills": missing_skills,
                "logo_letter": get_company_initial(job_dict.get("company_name")),
                "logoClass": "bg-blue-500",
                "work_mode": "Remote",
                "skills_required": job_dict.get("skills_required"),
                "company_size": job_dict.get("company_size"),
                "tools_preferred": job_dict.get("tools_preferred"),
                "posted_date": job_dict.get("posted_date"),
                "company_name": job_dict.get("company_name"),
                "job_title": job_dict.get("job_title"),
                "salary_min": job_dict.get("salary_min"),
                "salary_max": job_dict.get("salary_max"),
            }

            jobs_list.append(mapped_job)

        return {"jobs": jobs_list}

    except Exception as e:
        logger.error("get_jobs_failed", error=str(e), exc_info=True)
        return {"error": {"code": "INTERNAL_ERROR", "message": "ไม่สามารถโหลดงานได้"}}, 500


@app.route("/api/jobs/<int:job_id>")
def get_job_detail(job_id):
    try:
        user_id = request.args.get("user_id", type=int)

        result = db.session.execute(
            text("""
                SELECT 
                    id, company_name, industry, job_title,
                    skills_required, experience_level, employment_type,
                    location, posted_date, company_size, tools_preferred,
                    salary_min, salary_max, about_role, responsibilities,
                    requirements, team_size, visa_sponsorship, posted_ago,
                    match_score
                FROM job_market_data 
                WHERE id = :job_id
            """),
            {"job_id": job_id}
        )
        job = result.mappings().first()

        if not job:
            return {"error": "Job not found"}, 404

        job_dict = serialize_row(job)
        job_dict["title"] = job_dict.get("job_title")
        job_dict["company"] = job_dict.get("company_name")
        job_dict["type"] = job_dict.get("employment_type")
        job_dict["level"] = job_dict.get("experience_level")
        job_dict["salary"] = format_salary(
            job_dict.get("salary_min"),
            job_dict.get("salary_max")
        )

        if user_id:
            user_data = load_user_data(user_id, db.session)
            match = calculate_match_score_fast(job_dict, user_data)
            job_dict["match_score"] = match["overall"]
            job_dict["match_breakdown"] = {
                "skills": match["skills_match"],
                "experience": match["experience_match"],
                "industry": match["industry_match"],
            }
            job_dict["matched_skills"] = match["matched_skills"]
            job_dict["missing_skills"] = match["missing_skills"]
        else:
            job_dict["match_score"] = 75
            job_dict["match_breakdown"] = {"skills": 75, "experience": 75, "industry": 75}
            job_dict["matched_skills"] = []
            job_dict["missing_skills"] = []

        return {"job": job_dict}

    except Exception as e:
        logger.error("operation_failed", error=str(e), exc_info=True)
        return {"error": {"code": "INTERNAL_ERROR", "message": "เกิดข้อผิดพลาด"}}, 500


# =============================================================================
# PROFILE
# =============================================================================

@app.route("/api/profile/<int:user_id>", methods=["GET"])
@require_auth
def get_profile(user_id):
    # ⭐ ตรวจเจ้าของ
    if user_id != g.user_id:
        return {"error": {"code": "FORBIDDEN", "message": "ไม่มีสิทธิ์"}}, 403

    try:
        result = db.session.execute(
            text("""
                SELECT id, username, email, full_name, phone, location, 
                       bio, profile_image, created_at
                FROM users 
                WHERE id = :user_id
            """),
            {"user_id": user_id}
        )
        user = result.mappings().first()

        if not user:
            return {"error": "User not found"}, 404

        return {"profile": serialize_row(user)}
    except Exception as e:
        return {"error": str(e)}, 500


@app.route("/api/profile/<int:user_id>/full", methods=["GET"])
@require_auth
def get_full_profile(user_id):
    # ⭐ ตรวจเจ้าของ
    if user_id != g.user_id:
        return {"error": {"code": "FORBIDDEN", "message": "ไม่มีสิทธิ์"}}, 403

    try:
        user_result = db.session.execute(
            text("""
                SELECT id, username, email, full_name, phone, location, 
                    bio, profile_image, industry, resume_url, resume_filename,
                    created_at, updated_at
                FROM users WHERE id = :user_id
            """),
            {"user_id": user_id}
        )
        user = user_result.mappings().first()
        if not user:
            return {"error": "User not found"}, 404

        skills_result = db.session.execute(
            text("""
                SELECT id, skill_name, skill_level 
                FROM user_skills WHERE user_id = :user_id
                ORDER BY id
            """),
            {"user_id": user_id}
        )
        skills = [serialize_row(row) for row in skills_result.mappings().all()]

        exp_result = db.session.execute(
            text("""
                SELECT id, job_title, company_name, location, 
                       start_date, end_date, is_current, description
                FROM user_experience WHERE user_id = :user_id
                ORDER BY start_date DESC NULLS FIRST
            """),
            {"user_id": user_id}
        )
        experiences = [serialize_row(row) for row in exp_result.mappings().all()]

        edu_result = db.session.execute(
            text("""
                SELECT id, institution, degree, field_of_study,
                       start_date, end_date, is_current, gpa
                FROM user_education WHERE user_id = :user_id
                ORDER BY start_date DESC NULLS FIRST
            """),
            {"user_id": user_id}
        )
        educations = [serialize_row(row) for row in edu_result.mappings().all()]

        return {
            "profile": serialize_row(user),
            "skills": skills,
            "experiences": experiences,
            "educations": educations
        }, 200

    except Exception as e:
        logger.error("operation_failed", error=str(e), exc_info=True)
        return {"error": {"code": "INTERNAL_ERROR", "message": "เกิดข้อผิดพลาด"}}, 500


@app.route("/api/profile/<int:user_id>", methods=["PUT"])
@require_auth
def update_profile(user_id):
    # ⭐ ตรวจเจ้าของ
    if user_id != g.user_id:
        return {"error": {"code": "FORBIDDEN", "message": "ไม่มีสิทธิ์"}}, 403

    try:
        data = request.json

        if not data:
            return {"error": "No data provided"}, 400

        # ⭐ Sprint 3: Validation + Sanitize
        # Phone validation
        phone = data.get("phone")
        if phone and not is_valid_phone(phone):
            return {"error": {"code": "INVALID_PHONE", "message": "เบอร์โทรไม่ถูกต้อง"}}, 400

        # Sanitize text (XSS protection)
        full_name = sanitize_text(data.get("full_name") or "", max_length=100) or None
        bio = sanitize_text(data.get("bio") or "", max_length=2000) or None
        location = sanitize_text(data.get("location") or "", max_length=200) or None
        industry = sanitize_text(data.get("industry") or "", max_length=100) or None

        db.session.execute(
            text("""
                UPDATE users 
                SET full_name = COALESCE(:full_name, full_name),
                    phone = COALESCE(:phone, phone),
                    location = COALESCE(:location, location),
                    bio = COALESCE(:bio, bio),
                    industry = COALESCE(:industry, industry),
                    profile_image = COALESCE(:profile_image, profile_image),
                    updated_at = NOW()
                WHERE id = :user_id
            """),
            {
                "user_id": user_id,
                "full_name": full_name,
                "phone": phone,
                "location": location,
                "bio": bio,
                "industry": industry,
                "profile_image": data.get("profile_image"),
            }
        )

        if "skills" in data:
            db.session.execute(
                text("DELETE FROM user_skills WHERE user_id = :user_id"),
                {"user_id": user_id}
            )
            if data["skills"]:
                db.session.execute(
                    text("""
                        INSERT INTO user_skills (user_id, skill_name, skill_level)
                        VALUES (:user_id, :skill_name, :skill_level)
                    """),
                    [
                        {
                            "user_id": user_id,
                            "skill_name": s.get("name") or s.get("skill_name"),
                            "skill_level": s.get("level") or s.get("skill_level", "Intermediate")
                        }
                        for s in data["skills"]
                    ]
                )

        if "experiences" in data:
            db.session.execute(
                text("DELETE FROM user_experience WHERE user_id = :user_id"),
                {"user_id": user_id}
            )
            if data["experiences"]:
                db.session.execute(
                    text("""
                        INSERT INTO user_experience 
                            (user_id, job_title, company_name, location,
                             start_date, end_date, is_current, description)
                        VALUES 
                            (:user_id, :job_title, :company_name, :location,
                             :start_date, :end_date, :is_current, :description)
                    """),
                    [
                        {
                            "user_id": user_id,
                            "job_title": e.get("job_title"),
                            "company_name": e.get("company_name"),
                            "location": e.get("location"),
                            "start_date": e.get("start_date") or None,
                            "end_date": e.get("end_date") or None,
                            "is_current": e.get("is_current", False),
                            "description": e.get("description", "")
                        }
                        for e in data["experiences"]
                    ]
                )

        if "educations" in data:
            db.session.execute(
                text("DELETE FROM user_education WHERE user_id = :user_id"),
                {"user_id": user_id}
            )
            if data["educations"]:
                db.session.execute(
                    text("""
                        INSERT INTO user_education 
                            (user_id, institution, degree, field_of_study,
                             start_date, end_date, is_current, gpa)
                        VALUES 
                            (:user_id, :institution, :degree, :field_of_study,
                             :start_date, :end_date, :is_current, :gpa)
                    """),
                    [
                        {
                            "user_id": user_id,
                            "institution": ed.get("institution"),
                            "degree": ed.get("degree"),
                            "field_of_study": ed.get("field_of_study"),
                            "start_date": ed.get("start_date") or None,
                            "end_date": ed.get("end_date") or None,
                            "is_current": ed.get("is_current", False),
                            "gpa": ed.get("gpa") or None
                        }
                        for ed in data["educations"]
                    ]
                )

        db.session.commit()

        logger.info("profile_updated", user_id=user_id)

        return {"status": "success", "message": "Profile updated successfully"}, 200

    except IntegrityError as e:
        db.session.rollback()
        return {"error": "Constraint violation", "detail": str(e)}, 409
    except Exception as e:
        db.session.rollback()
        logger.error("operation_failed", error=str(e), exc_info=True)
        return {"error": {"code": "INTERNAL_ERROR", "message": "เกิดข้อผิดพลาด"}}, 500


# =============================================================================
# APPLICATIONS
# =============================================================================

@app.route("/api/applications", methods=["GET"])
@require_auth
def get_applications():
    # ⭐ return เฉพาะของตัวเอง
    try:
        sql = text("""
            SELECT 
                a.id, a.job_id, a.status, a.full_name, a.email, a.phone,
                a.applied_date, a.updated_at,
                j.job_title as title, j.company_name as company, 
                j.location, j.salary_min, j.salary_max
            FROM applications a
            LEFT JOIN job_market_data j ON a.job_id = j.id
            WHERE a.user_id = :user_id
            ORDER BY a.applied_date DESC
        """)
        result = db.session.execute(sql, {"user_id": g.user_id})
        rows = result.mappings().all()

        return jsonify({
            "applications": [serialize_row(row) for row in rows]
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/applications/user/<int:user_id>", methods=["GET"])
@require_auth
def get_user_applications(user_id):
    # ⭐ ตรวจเจ้าของ
    if user_id != g.user_id:
        return {"error": {"code": "FORBIDDEN", "message": "ไม่มีสิทธิ์"}}, 403

    try:
        result = db.session.execute(
            text("""
                SELECT 
                    a.id, a.job_id, a.status, a.full_name, a.email, a.phone,
                    a.cover_letter, a.applied_date, a.updated_at,
                    j.job_title, j.company_name as company,
                    j.location, j.salary_min, j.salary_max
                FROM applications a
                LEFT JOIN job_market_data j ON a.job_id = j.id
                WHERE a.user_id = :user_id
                ORDER BY a.applied_date DESC
            """),
            {"user_id": user_id}
        )
        rows = result.mappings().all()
        return {"applications": [serialize_row(row) for row in rows]}
    except Exception as e:
        return {"error": str(e)}, 500


@app.route("/api/applications/<int:application_id>/detail", methods=["GET"])
@require_auth
def get_application_detail(application_id):
    try:
        app_result = db.session.execute(
            text("""
                SELECT 
                    a.id, a.user_id, a.job_id, a.status,
                    a.full_name, a.email, a.phone, a.location,
                    a.resume_filename, a.resume_url, a.cover_letter,
                    a.applied_date, a.updated_at,
                    j.job_title, j.company_name, j.location AS job_location,
                    j.salary_min, j.salary_max, j.employment_type,
                    u.resume_url AS user_resume_url
                FROM applications a
                LEFT JOIN job_market_data j ON a.job_id = j.id
                LEFT JOIN users u ON a.user_id = u.id
                WHERE a.id = :application_id
            """),
            {"application_id": application_id}
        )

        application = app_result.mappings().first()
        if not application:
            return {"error": "Application not found"}, 404

        # ⭐ ตรวจเจ้าของ
        if application["user_id"] != g.user_id:
            return {"error": {"code": "FORBIDDEN", "message": "ไม่มีสิทธิ์"}}, 403

        skills_result = db.session.execute(
            text("""
                SELECT skill_name, skill_level 
                FROM application_skills WHERE application_id = :application_id
            """),
            {"application_id": application_id}
        )
        skills = [serialize_row(row) for row in skills_result.mappings().all()]

        exp_result = db.session.execute(
            text("""
                SELECT job_title, company_name, location,
                       start_date, end_date, is_current, description
                FROM application_experiences WHERE application_id = :application_id
                ORDER BY start_date DESC NULLS FIRST
            """),
            {"application_id": application_id}
        )
        experiences = [serialize_row(row) for row in exp_result.mappings().all()]

        edu_result = db.session.execute(
            text("""
                SELECT institution, degree, field_of_study,
                       start_date, end_date, is_current, gpa
                FROM application_educations WHERE application_id = :application_id
                ORDER BY start_date DESC NULLS FIRST
            """),
            {"application_id": application_id}
        )
        educations = [serialize_row(row) for row in edu_result.mappings().all()]

        return {
            "application": serialize_row(application),
            "skills": skills,
            "experiences": experiences,
            "educations": educations
        }, 200

    except Exception as e:
        logger.error("operation_failed", error=str(e), exc_info=True)
        return {"error": {"code": "INTERNAL_ERROR", "message": "เกิดข้อผิดพลาด"}}, 500


@app.route("/api/applications", methods=["POST"])
@require_auth
def create_application():
    try:
        data = request.json

        if not data:
            return {"error": "No data provided"}, 400

        # ⭐ ใช้ g.user_id
        user_id = g.user_id
        job_id = data.get("jobId") or data.get("job_id")

        # ⭐ Sprint 3: Validation + Sanitize
        full_name = sanitize_text(
            data.get("fullName") or data.get("full_name") or "",
            max_length=100,
        ) or None

        email_raw = (data.get("email") or "").strip().lower()
        if email_raw and not is_valid_email(email_raw):
            return {"error": {"code": "INVALID_EMAIL", "message": "อีเมลไม่ถูกต้อง"}}, 400
        email = email_raw or None

        phone = data.get("phone")
        if phone and not is_valid_phone(phone):
            return {"error": {"code": "INVALID_PHONE", "message": "เบอร์โทรไม่ถูกต้อง"}}, 400

        location = sanitize_text(
            data.get("location") or "",
            max_length=200,
        ) or ""

        resume_filename = sanitize_text(
            data.get("resumeFilename") or data.get("resume_filename") or "",
            max_length=255,
        ) or ""

        resume_url = data.get("resumeUrl") or data.get("resume_url") or ""
        if resume_url and not is_supabase_url(resume_url):
            return {"error": {"code": "INVALID_RESUME_URL", "message": "URL resume ไม่ถูกต้อง"}}, 400

        cover_letter = sanitize_text(
            data.get("coverLetter") or data.get("cover_letter") or "",
            max_length=5000,
        ) or ""

        skills = data.get("skills", [])
        experiences = data.get("experiences", [])
        educations = data.get("educations", [])

        if not job_id:
            return {"error": "job_id is required"}, 400
        if not full_name or not email:
            return {"error": "full_name and email are required"}, 400

        if not resume_url:
            return {"error": "Please upload a resume first"}, 400

        check = db.session.execute(
            text("""
                SELECT id FROM applications
                WHERE user_id = :user_id AND job_id = :job_id
            """),
            {"user_id": user_id, "job_id": job_id}
        )
        if check.first():
            return {"error": "You have already applied for this position"}, 400

        job_check = db.session.execute(
            text("""
                SELECT id, posted_by_user_id
                FROM job_market_data WHERE id = :job_id
            """),
            {"job_id": job_id}
        )

        job_row = job_check.first()
        if not job_row:
            return {"error": "Job not found"}, 404

        if job_row[1] and job_row[1] == user_id:
            return {"error": "You cannot apply to a job that you posted"}, 400

        result = db.session.execute(
            text("""
                INSERT INTO applications
                    (user_id, job_id, full_name, email, phone, location,
                     resume_filename, resume_url, cover_letter, status, applied_date)
                VALUES
                    (:user_id, :job_id, :full_name, :email, :phone, :location,
                     :resume_filename, :resume_url, :cover_letter, 'applied', NOW())
                RETURNING id
            """),
            {
                "user_id": user_id,
                "job_id": job_id,
                "full_name": full_name,
                "email": email,
                "phone": phone,
                "location": location,
                "resume_filename": resume_filename,
                "resume_url": resume_url,
                "cover_letter": cover_letter,
            }
        )
        application_id = result.scalar()

        if skills:
            db.session.execute(
                text("""
                    INSERT INTO application_skills
                        (application_id, skill_name, skill_level)
                    VALUES
                        (:application_id, :skill_name, :skill_level)
                """),
                [
                    {
                        "application_id": application_id,
                        "skill_name": s.get("name") or s.get("skill_name"),
                        "skill_level": s.get("level") or s.get("skill_level", "Intermediate")
                    }
                    for s in skills
                ]
            )

        if experiences:
            db.session.execute(
                text("""
                    INSERT INTO application_experiences
                        (application_id, job_title, company_name, location,
                         start_date, end_date, is_current, description)
                    VALUES
                        (:application_id, :job_title, :company_name, :location,
                         :start_date, :end_date, :is_current, :description)
                """),
                [
                    {
                        "application_id": application_id,
                        "job_title": e.get("job_title"),
                        "company_name": e.get("company_name"),
                        "location": e.get("location"),
                        "start_date": e.get("start_date") or None,
                        "end_date": e.get("end_date") or None,
                        "is_current": e.get("is_current", False),
                        "description": e.get("description", "")
                    }
                    for e in experiences
                ]
            )

        if educations:
            db.session.execute(
                text("""
                    INSERT INTO application_educations
                        (application_id, institution, degree, field_of_study,
                         start_date, end_date, is_current, gpa)
                    VALUES
                        (:application_id, :institution, :degree, :field_of_study,
                         :start_date, :end_date, :is_current, :gpa)
                """),
                [
                    {
                        "application_id": application_id,
                        "institution": ed.get("institution"),
                        "degree": ed.get("degree"),
                        "field_of_study": ed.get("field_of_study"),
                        "start_date": ed.get("start_date") or None,
                        "end_date": ed.get("end_date") or None,
                        "is_current": ed.get("is_current", False),
                        "gpa": ed.get("gpa") or None
                    }
                    for ed in educations
                ]
            )

        db.session.commit()

        logger.info(
            "application_submitted",
            user_id=user_id,
            job_id=job_id,
            application_id=application_id,
        )

        return {
            "status": "success",
            "message": "Application submitted successfully!",
            "application_id": application_id
        }, 201

    except IntegrityError as e:
        db.session.rollback()
        return {"error": "Integrity error", "detail": str(e)}, 409
    except Exception as e:
        db.session.rollback()
        logger.error("operation_failed", error=str(e), exc_info=True)
        return {"error": {"code": "INTERNAL_ERROR", "message": "เกิดข้อผิดพลาด"}}, 500


# =============================================================================
# FAVORITES
# =============================================================================

@app.route("/api/favorites")
@require_auth
def get_favorites():
    try:
        user_id = g.user_id

        result = db.session.execute(
            text("""
                SELECT 
                    f.id, f.user_id, f.job_id, f.created_at,
                    j.job_title, j.company_name, j.location,
                    j.salary_min, j.salary_max, j.industry,
                    j.experience_level, j.employment_type,
                    j.skills_required
                FROM favorites f
                LEFT JOIN job_market_data j ON f.job_id = j.id
                WHERE f.user_id = :user_id
                ORDER BY f.created_at DESC
            """),
            {"user_id": user_id}
        )
        rows = result.mappings().all()
        return {"favorites": [serialize_row(row) for row in rows]}
    except Exception as e:
        logger.error("get_favorites_failed", error=str(e), exc_info=True)
        return {"error": str(e)}, 500


@app.route("/api/favorites/toggle", methods=["POST"])
@require_auth
def toggle_favorite():
    user_id = g.user_id
    data = request.json or {}
    job_id = data.get("job_id")

    if not job_id:
        return {"error": "job_id is required"}, 400

    try:
        job_check = db.session.execute(
            text("SELECT id FROM job_market_data WHERE id = :jid"),
            {"jid": job_id}
        ).first()
        if not job_check:
            return {"error": "Job not found"}, 404

        check = db.session.execute(
            text("SELECT id FROM favorites WHERE user_id = :user_id AND job_id = :job_id"),
            {"user_id": user_id, "job_id": job_id}
        )
        existing = check.first()

        if existing:
            db.session.execute(
                text("DELETE FROM favorites WHERE user_id = :user_id AND job_id = :job_id"),
                {"user_id": user_id, "job_id": job_id}
            )
            db.session.commit()
            logger.info("favorite_removed", user_id=user_id, job_id=job_id)
            return {"favorited": False, "message": "Removed from favorites"}
        else:
            db.session.execute(
                text("INSERT INTO favorites (user_id, job_id) VALUES (:user_id, :job_id)"),
                {"user_id": user_id, "job_id": job_id}
            )
            db.session.commit()
            logger.info("favorite_added", user_id=user_id, job_id=job_id)
            return {"favorited": True, "message": "Added to favorites"}

    except Exception as e:
        db.session.rollback()
        logger.error("toggle_favorite_failed", error=str(e), exc_info=True)
        return {"error": str(e)}, 500


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
# EMPLOYER
# =============================================================================

@app.route("/api/employer/jobs", methods=["GET"])
@require_auth
@require_role("employer")
def get_employer_jobs():
    try:
        user_id = g.user_id

        result = db.session.execute(
            text("""
                SELECT 
                    j.id,
                    j.job_title,
                    j.company_name,
                    j.location,
                    j.employment_type,
                    j.experience_level,
                    j.salary_min,
                    j.salary_max,
                    j.posted_date,
                    COUNT(a.id) AS applicant_count
                FROM job_market_data j
                LEFT JOIN applications a ON j.id = a.job_id
                WHERE j.posted_by_user_id = :user_id
                GROUP BY j.id
                ORDER BY j.posted_date DESC
            """),
            {"user_id": user_id}
        )
        rows = result.mappings().all()

        jobs = []
        for row in rows:
            jobs.append({
                "id": row["id"],
                "job_title": row["job_title"],
                "company_name": row["company_name"],
                "location": row["location"],
                "employment_type": row["employment_type"],
                "experience_level": row["experience_level"],
                "salary_min": row["salary_min"],
                "salary_max": row["salary_max"],
                "posted_date": row["posted_date"].isoformat() if row["posted_date"] else None,
                "applicant_count": row["applicant_count"],
                "status": "Active",
            })

        return {"jobs": jobs}, 200

    except Exception as e:
        logger.error("get_employer_jobs_failed", error=str(e), exc_info=True)
        return {"error": str(e)}, 500


@app.route("/api/employer/jobs", methods=["POST"])
@require_auth
@require_role("employer")
def create_employer_job():
    try:
        data = request.json

        user_id = g.user_id

        # ⭐ Sprint 3: Validation + Sanitize
        job_title = sanitize_text(data.get("job_title") or "", max_length=100)
        company_name = sanitize_text(data.get("company_name") or "", max_length=200)
        location = sanitize_text(data.get("location") or "", max_length=200)
        skills_required = sanitize_text(data.get("skills_required") or "", max_length=1000)
        tools_preferred = sanitize_text(data.get("tools_preferred") or "", max_length=1000)
        industry = sanitize_text(data.get("industry") or "", max_length=100)
        company_size = sanitize_text(data.get("company_size") or "", max_length=50)
        about_role = sanitize_text(data.get("about_role") or "", max_length=5000)
        responsibilities = sanitize_text(data.get("responsibilities") or "", max_length=5000)
        requirements = sanitize_text(data.get("requirements") or "", max_length=5000)

        # ⭐ Validate employment_type
        employment_type = (data.get("employment_type") or "Full-time").strip()
        ALLOWED_EMPLOYMENT_TYPES = {"Full-time", "Part-time", "Contract", "Internship"}
        if employment_type not in ALLOWED_EMPLOYMENT_TYPES:
            return {
                "error": {"code": "INVALID_EMPLOYMENT_TYPE",
                          "message": f"ต้องเป็น: {', '.join(ALLOWED_EMPLOYMENT_TYPES)}"}
            }, 400

        # ⭐ Validate experience_level
        experience_level = (data.get("experience_level") or "Mid").strip()
        ALLOWED_EXPERIENCE_LEVELS = {"Entry", "Mid", "Senior", "Lead"}
        if experience_level not in ALLOWED_EXPERIENCE_LEVELS:
            return {
                "error": {"code": "INVALID_EXPERIENCE_LEVEL",
                          "message": f"ต้องเป็น: {', '.join(ALLOWED_EXPERIENCE_LEVELS)}"}
            }, 400

        # ⭐ Validate salary (ตัวเลขบวก, min <= max)
        salary_min = data.get("salary_min")
        salary_max = data.get("salary_max")
        try:
            salary_min = float(salary_min) if salary_min else None
            salary_max = float(salary_max) if salary_max else None
        except (ValueError, TypeError):
            return {"error": {"code": "INVALID_SALARY", "message": "เงินเดือนต้องเป็นตัวเลข"}}, 400

        if salary_min is not None and salary_min < 0:
            return {"error": {"code": "INVALID_SALARY", "message": "เงินเดือนต้องไม่ติดลบ"}}, 400
        if salary_max is not None and salary_max < 0:
            return {"error": {"code": "INVALID_SALARY", "message": "เงินเดือนต้องไม่ติดลบ"}}, 400
        if salary_min and salary_max and salary_min > salary_max:
            return {"error": {"code": "INVALID_SALARY", "message": "เงินเดือนต่ำสุดต้อง <= สูงสุด"}}, 400

        # ⭐ Validate required
        if not job_title:
            return {"error": "Job title is required"}, 400
        if job_title not in ALLOWED_JOB_TITLES:
            return {
                "error": (
                    f"Invalid job title. Allowed: "
                    f"{', '.join(sorted(ALLOWED_JOB_TITLES))}"
                )
            }, 400
        if not company_name:
            return {"error": "Company name is required"}, 400

        result = db.session.execute(
            text("""
                INSERT INTO job_market_data (
                    job_title, company_name, location,
                    employment_type, experience_level,
                    salary_min, salary_max,
                    skills_required, tools_preferred,
                    industry, company_size,
                    about_role, responsibilities, requirements,
                    posted_by_user_id, posted_date
                ) VALUES (
                    :job_title, :company_name, :location,
                    :employment_type, :experience_level,
                    :salary_min, :salary_max,
                    :skills_required, :tools_preferred,
                    :industry, :company_size,
                    :about_role, :responsibilities, :requirements,
                    :user_id, NOW()
                )
                RETURNING id
            """),
            {
                "job_title": job_title,
                "company_name": company_name,
                "location": location or None,
                "employment_type": employment_type,
                "experience_level": experience_level,
                "salary_min": float(salary_min) if salary_min else None,
                "salary_max": int(salary_max) if salary_max else None,
                "skills_required": skills_required or None,
                "tools_preferred": tools_preferred or None,
                "industry": industry or None,
                "company_size": company_size or None,
                "about_role": about_role or None,
                "responsibilities": responsibilities or None,
                "requirements": requirements or None,
                "user_id": user_id,
            }
        )
        job_id = result.scalar()

        db.session.commit()

        logger.info(
            "job_created",
            user_id=user_id,
            job_id=job_id,
            job_title=job_title,
            company_name=company_name,
        )

        return {
            "status": "success",
            "message": "Job posted successfully",
            "job_id": job_id,
        }, 201

    except IntegrityError as e:
        db.session.rollback()
        return {"error": "Integrity error", "detail": str(e)}, 409
    except Exception as e:
        db.session.rollback()
        logger.error("create_job_failed", error=str(e), exc_info=True)
        return {"error": str(e)}, 500


@app.route("/api/employer/jobs/<int:job_id>/applications", methods=["GET"])
@require_auth
@require_role("employer")
def get_job_applications(job_id):
    try:
        job_check = db.session.execute(
            text("SELECT id, job_title, posted_by_user_id FROM job_market_data WHERE id = :jid"),
            {"jid": job_id}
        ).first()
        if not job_check:
            return {"error": "Job not found"}, 404

        # ⭐ ตรวจว่าเป็นเจ้าของ job
        if job_check[2] != g.user_id:
            return {"error": {"code": "FORBIDDEN", "message": "ไม่มีสิทธิ์"}}, 403

        result = db.session.execute(
            text("""
                SELECT 
                    a.id, a.user_id, a.full_name, a.email, a.phone,
                    a.location, a.status, a.applied_date, a.resume_filename,
                    a.resume_url,
                    u.resume_url AS user_resume_url
                FROM applications a
                LEFT JOIN users u ON a.user_id = u.id
                WHERE a.job_id = :jid
                ORDER BY a.applied_date DESC
            """),
            {"jid": job_id}
        )

        rows = result.mappings().all()

        applications = []
        for row in rows:
            applications.append({
                "id": row["id"],
                "user_id": row["user_id"],
                "full_name": row["full_name"],
                "email": row["email"],
                "phone": row["phone"],
                "location": row["location"],
                "status": row["status"] or "applied",
                "applied_date": row["applied_date"].isoformat() if row["applied_date"] else None,
                "resume_filename": row["resume_filename"],
                "resume_url": row["resume_url"],
                "user_resume_url": row["user_resume_url"],
            })

        return {
            "job_id": job_id,
            "job_title": job_check[1],
            "applications": applications,
            "total": len(applications),
        }, 200

    except Exception as e:
        logger.error("get_job_applications_failed", error=str(e), exc_info=True)
        return {"error": str(e)}, 500


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

        return {
            "application": serialize_row(application),
            "skills": skills,
            "experiences": experiences,
            "educations": educations,
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
# SKILLS
# =============================================================================

@app.route("/api/skills", methods=["GET"])
def get_skills():
    try:
        result = db.session.execute(
            text("""
                SELECT DISTINCT skill_name 
                FROM user_skills 
                WHERE skill_name IS NOT NULL
                ORDER BY skill_name
            """)
        )
        db_skills = [row[0] for row in result]

        default_skills = [
            "Python", "JavaScript", "TypeScript", "Java", "C++", "Go", "Rust",
            "React", "Vue", "Angular", "Node.js", "Express", "Django", "Flask",
            "SQL", "PostgreSQL", "MySQL", "MongoDB", "Redis",
            "PyTorch", "TensorFlow", "Scikit-learn", "Pandas", "NumPy",
            "Docker", "Kubernetes", "AWS", "GCP", "Azure",
            "Git", "CI/CD", "Linux", "REST API", "GraphQL",
            "Figma", "UI Design", "UX Research", "Prototyping",
            "Data Analysis", "Machine Learning", "Deep Learning", "NLP",
            "Computer Vision", "Data Visualization", "Tableau", "Power BI",
            "Statistics", "A/B Testing", "Excel",
        ]

        all_skills = sorted(set(db_skills + default_skills))

        return {"skills": all_skills}, 200
    except Exception as e:
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
        match = calculate_match_score_fast(dict(job), user_data)
        return match, 200
    except Exception as e:
        return {"error": str(e)}, 500


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