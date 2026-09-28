"""
blueprints/match.py — Match score for single job

Routes:
- GET /api/match-score/<int:job_id>
"""
from flask import Blueprint, request
from sqlalchemy import text

from core.extensions import db
from core.logging_config import get_logger
from services.match_score import load_user_data, calculate_match_score_v2

bp = Blueprint("match", __name__, url_prefix="/api")
logger = get_logger(__name__)


@bp.route("/match-score/<int:job_id>", methods=["GET"])
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
        logger.error("get_match_score_failed", error=str(e), exc_info=True)
        return {"error": str(e)}, 500
