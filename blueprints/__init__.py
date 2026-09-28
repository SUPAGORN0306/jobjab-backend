"""blueprints/__init__.py — Register all blueprints"""
from .auth import auth_bp
from .jobs import bp as jobs_bp
from .profile import bp as profile_bp
from .skills import bp as skills_bp
from .applications import bp as applications_bp
from .favorites import bp as favorites_bp
from .employer_jobs import bp as employer_jobs_bp
from .employer_applications import bp as employer_applications_bp
from .employer_analytics import bp as employer_analytics_bp

__all__ = [
    "auth_bp",
    "jobs_bp",
    "profile_bp",
    "skills_bp",
    "applications_bp",
    "favorites_bp",
    "employer_jobs_bp",
    "employer_applications_bp",
    "employer_analytics_bp",
]
