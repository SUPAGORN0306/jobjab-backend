"""
blueprints/skills.py — Skills routes

Routes:
- GET /api/skills
"""
from core.extensions import db, cache
from sqlalchemy import text
from flask import Blueprint


bp = Blueprint("skills", __name__, url_prefix="/api")


@bp.route("/skills", methods=["GET"])
def get_skills():
    try:
        # 1. Cache check
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

        # 3. Cache 5 นาที
        cache.set("skills_list", all_skills, timeout=300)

        return {"skills": all_skills}, 200
    except Exception as e:
        return {"error": str(e)}, 500