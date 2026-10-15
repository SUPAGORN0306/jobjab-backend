"""
app.py — Flask application entry point

Assembly point for:
- CORS
- Database (SQLAlchemy + pool)
- Cache
- JWT
- Extensions (db, migrate, jwt, limiter, csrf, cache)
- Blueprints (12 modules)
- Error handlers
- Dev server run

Business logic lives in `blueprints/`. Config lives in `core.config`.
"""
from flask import Flask, request
from flask_cors import CORS
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import RequestEntityTooLarge, HTTPException

# ---------- Config + Extensions ----------
from core.config import settings
from core.extensions import db, jwt, limiter, csrf, migrate, cache
from core.logging_config import setup_logging, get_logger

# ---------- Blueprints ----------
from blueprints import (
    auth_bp,
    jobs_bp,
    profile_bp,
    skills_bp,
    applications_bp,
    favorites_bp,
    employer_jobs_bp,
    employer_applications_bp,
    employer_analytics_bp,
    employer_profile_bp,
    uploads_bp,
    match_bp,
)

# ---------- Logging (init first) ----------
setup_logging()
logger = get_logger(__name__)


# =============================================================================
# APP INIT
# =============================================================================

app = Flask(__name__)

# ---------- CORS ----------
CORS(
    app,
    origins=settings.cors_origins_list,
    supports_credentials=True,   # for withCredentials: true
    allow_headers=["Content-Type", "Authorization", "X-CSRF-Token"],
    expose_headers=[
        "X-RateLimit-Limit",
        "X-RateLimit-Remaining",
        "X-RateLimit-Reset",
    ],
)

# ---------- Database ----------
app.config["SQLALCHEMY_DATABASE_URI"] = settings.DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Pool options only for Postgres — SQLite (test) does not support them
if settings.DATABASE_URL.startswith(("postgresql://", "postgres://")):
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": True,       # verify connections before use
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

# ---------- Cache ----------
cache.init_app(app, config={
    "CACHE_TYPE": "SimpleCache",
    "CACHE_DEFAULT_TIMEOUT": 60,
})


# =============================================================================
# JWT CONFIG
# =============================================================================

app.config["JWT_SECRET_KEY"] = settings.JWT_SECRET_KEY
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = settings.JWT_ACCESS_TOKEN_EXPIRES_MINUTES * 60
app.config["JWT_REFRESH_TOKEN_EXPIRES"] = settings.JWT_REFRESH_TOKEN_EXPIRES_DAYS * 24 * 3600
app.config["JWT_TOKEN_LOCATION"] = ["cookies"]         # httpOnly cookies only
app.config["JWT_COOKIE_SECURE"] = settings.cookie_secure
app.config["JWT_COOKIE_SAMESITE"] = "None" if settings.cookie_secure else "Lax"
app.config["JWT_COOKIE_CSRF_PROTECT"] = False          # we handle CSRF ourselves

# Cookie names — must match cookies.py
app.config["JWT_ACCESS_COOKIE_NAME"] = "access_token"
app.config["JWT_REFRESH_COOKIE_NAME"] = "refresh_token"
app.config["JWT_ACCESS_COOKIE_PATH"] = "/"
app.config["JWT_REFRESH_COOKIE_PATH"] = "/api/auth"

# Disable Flask-WTF CSRF — we use JWT CSRF instead
app.config["WTF_CSRF_ENABLED"] = False


# =============================================================================
# INIT EXTENSIONS
# =============================================================================

db.init_app(app)
migrate.init_app(app, db)
jwt.init_app(app)
limiter.init_app(app)
csrf.init_app(app)

# Exempt auth blueprint from Flask-WTF CSRF (we use JWT CSRF)
csrf.exempt(auth_bp)


# =============================================================================
# REGISTER BLUEPRINTS
# =============================================================================

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
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE


# =============================================================================
# ERROR HANDLERS
# =============================================================================

@app.errorhandler(RequestEntityTooLarge)
def handle_file_too_large(e):
    return {"error": {
        "code": "FILE_TOO_LARGE",
        "message": "File too large (max 5MB)",
    }}, 413


@app.errorhandler(HTTPException)
def handle_http_exception(e):
    """Handle HTTP errors (401, 403, 404, 405, ...)."""
    logger.warning("http_exception", status=e.code, name=e.name, path=request.path)
    return {
        "error": {
            "code": e.name.upper().replace(" ", "_"),
            "message": e.description or e.name,
        }
    }, e.code


@app.errorhandler(IntegrityError)
def handle_integrity_error(e):
    """Handle database integrity errors."""
    db.session.rollback()
    logger.error("db_integrity_error", error=str(e), exc_info=True)
    return {
        "error": {
            "code": "CONFLICT",
            "message": "Duplicate or conflicting data",
        }
    }, 409


@app.errorhandler(Exception)
def handle_generic_error(e):
    """Handle unexpected errors — hide str(e) in production."""
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
            "message": "Something went wrong. Please try again.",
        }
    }, 500


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    port = settings.PORT
    app.run(host="0.0.0.0", port=port, debug=False)