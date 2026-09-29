from flask import Flask, request
from flask_cors import CORS
from sqlalchemy.exc import IntegrityError
from dotenv import load_dotenv
import os
from werkzeug.exceptions import RequestEntityTooLarge, HTTPException


load_dotenv()

# ---------- Sprint 1: Security Extensions ----------
from core.config import settings
from core.extensions import db, jwt, limiter, csrf, migrate, cache
from core.logging_config import setup_logging, get_logger
from blueprints import (
    auth_bp, jobs_bp, profile_bp, skills_bp,
    applications_bp, favorites_bp, employer_jobs_bp,
    employer_applications_bp,
    employer_analytics_bp,
    employer_profile_bp,
    uploads_bp,
    match_bp,
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
        "pool_pre_ping": False,
        "pool_recycle": 60,
        "pool_size": 10,
        "max_overflow": 20,
        "pool_timeout": 30,
        "pool_use_lifo": True,
        "connect_args": {
            "sslmode": "require",
            "connect_timeout": 10,
        },
    }

db.init_app(app)
cache.init_app(app, config={
    'CACHE_TYPE': 'SimpleCache',
    'CACHE_DEFAULT_TIMEOUT': 60,
})


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
app.register_blueprint(uploads_bp)
app.register_blueprint(match_bp)

logger.info("app_initialized", env=settings.ENV)

# =============================================================================
# UPLOAD CONFIG
# =============================================================================

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB

app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE


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

