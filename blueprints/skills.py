"""
blueprints/skills.py — Skills routes

Routes:
- GET /api/skills
"""
from core.extensions import db, cache
from core.logging_config import get_logger
from sqlalchemy import text
from flask import Blueprint

bp = Blueprint("skills", __name__, url_prefix="/api")
logger = get_logger(__name__)


@bp.route("/skills", methods=["GET"])
def get_skills():
    try:
        cached = cache.get("skills_list")
        if cached is not None:
            return {"skills": cached}, 200

        result = db.session.execute(
            text("""
                SELECT DISTINCT skill FROM (
                    SELECT skill_name AS skill
                    FROM user_skills
                    WHERE skill_name IS NOT NULL AND skill_name != ''

                    UNION

                    SELECT TRIM(unnest(string_to_array(skills_required, ','))) AS skill
                    FROM job_market_data
                    WHERE skills_required IS NOT NULL
                ) x
                WHERE skill IS NOT NULL AND skill != ''
                ORDER BY skill
            """)
        )
        all_skills = [row[0] for row in result]

        cache.set("skills_list", all_skills, timeout=300)

        return {"skills": all_skills}, 200
    except Exception as e:
        logger.error("get_skills_failed", error=str(e), exc_info=True)
        return {"error": {"code": "INTERNAL_ERROR",
                "message": "Failed to load skills"}}, 500