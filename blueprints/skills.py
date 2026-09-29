"""
blueprints/skills.py — Skills routes

Routes:
- GET /api/skills
"""
from core.extensions import db, cache
from sqlalchemy import text
from flask import Blueprint


bp = Blueprint("skills", __name__, url_prefix="/api")


_DEFAULT_SKILLS = [
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


@bp.route("/skills", methods=["GET"])
def get_skills():
    try:
        # ลอง cache ก่อน
        cached = cache.get("skills_list")
        if cached is not None:
            return {"skills": cached}, 200

        result = db.session.execute(
            text("""
                SELECT DISTINCT skill_name 
                FROM user_skills 
                WHERE skill_name IS NOT NULL
                ORDER BY skill_name
            """)
        )
        db_skills = [row[0] for row in result]

        all_skills = sorted(set(db_skills + _DEFAULT_SKILLS))

        # เก็บ cache 5 นาที
        cache.set("skills_list", all_skills, timeout=300)

        return {"skills": all_skills}, 200
    except Exception as e:
        return {"error": str(e)}, 500