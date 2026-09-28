"""
blueprints/skills.py — Skills routes

Routes:
- GET /api/skills
"""
from sqlalchemy import text
from flask import Blueprint

from core.extensions import db

bp = Blueprint("skills", __name__, url_prefix="/api")


@bp.route("/skills", methods=["GET"])
def get_skills():
    try:
        result = db.session.execute(
            text("""
                SELECT DISTINCT skill_name 
                FROM user_skills 
                WHERE skill_name IS NOT NULL
                ORDER BY skill_name
            """)
        )
        db_skills = [row[0] for row in result]

        default_skills = [
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

        all_skills = sorted(set(db_skills + default_skills))

        return {"skills": all_skills}, 200
    except Exception as e:
        return {"error": str(e)}, 500
