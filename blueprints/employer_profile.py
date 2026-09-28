"""
blueprints/employer_profile.py — Employer profile

Routes:
- GET /api/employer/profile
"""
from flask import Blueprint, g
from sqlalchemy import text

from core.extensions import db
from core.security import require_auth, require_role
from core.logging_config import get_logger

bp = Blueprint("employer_profile", __name__, url_prefix="/api/employer")
logger = get_logger(__name__)


@bp.route("/profile", methods=["GET"])
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
