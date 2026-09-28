"""blueprints/__init__.py — Register all blueprints"""
from .auth import auth_bp
from .jobs import bp as jobs_bp
from .profile import bp as profile_bp
from .skills import bp as skills_bp

__all__ = ["auth_bp", "jobs_bp", "profile_bp", "skills_bp"]
