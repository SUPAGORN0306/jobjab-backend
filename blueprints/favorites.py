"""
blueprints/favorites.py — Favorite jobs routes

Routes:
- GET /api/favorites
- POST /api/favorites/toggle
"""
from sqlalchemy import text
from flask import Blueprint, request, g

from core.extensions import db
from core.security import require_auth
from core.logging_config import get_logger
from services.match_score import load_user_data, calculate_match_score_v2
from services.serializers import serialize_row

bp = Blueprint("favorites", __name__, url_prefix="/api")
logger = get_logger(__name__)


@bp.route("/favorites")
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
                    j.skills_required, j.tools_preferred
                FROM favorites f
                LEFT JOIN job_market_data j ON f.job_id = j.id
                WHERE f.user_id = :user_id
                ORDER BY f.created_at DESC
            """),
            {"user_id": user_id}
        )
        rows = result.mappings().all()

        # ── Load user data once for match scoring ──
        user_data = load_user_data(user_id, db.session)

        favorites = []
        for row in rows:
            fav_dict = serialize_row(row)

            # Calculate match score
            try:
                job_dict = {
                    "skills_required": row.get("skills_required"),
                    "experience_level": row.get("experience_level"),
                    "industry": row.get("industry"),
                    "location": row.get("location"),
                }
                match = calculate_match_score_v2(job_dict, user_data)
                fav_dict["match_score"] = match["overall"]
                fav_dict["match_breakdown"] = {
                    "skills": match["skills_match"],
                    "experience": match["experience_match"],
                    "industry": match["industry_match"],
                }
                fav_dict["matched_skills"] = match.get("matched_skills", [])
                fav_dict["missing_skills"] = match.get("missing_skills", [])
            except Exception as e:
                logger.warning("favorite_match_calc_failed", job_id=row.get("job_id"), error=str(e))
                fav_dict["match_score"] = 0
                fav_dict["match_breakdown"] = {"skills": 0, "experience": 0, "industry": 0}
                fav_dict["matched_skills"] = []
                fav_dict["missing_skills"] = []

            favorites.append(fav_dict)

        return {"favorites": favorites}
    except Exception as e:
        logger.error("get_favorites_failed", error=str(e), exc_info=True)
        return {"error": str(e)}, 500


@bp.route("/favorites/toggle", methods=["POST"])
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
