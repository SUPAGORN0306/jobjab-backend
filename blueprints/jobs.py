"""
blueprints/jobs.py — Jobs routes

Routes:
- GET /api/jobs
- GET /api/jobs/<int:job_id>
"""
from sqlalchemy import text
from flask import Blueprint, request

from core.extensions import db
from core.logging_config import get_logger
from services.match_score import load_user_data, calculate_match_score_v2
from services.serializers import format_salary, get_company_initial, serialize_row

bp = Blueprint("jobs", __name__, url_prefix="/api")
logger = get_logger(__name__)


@bp.route("/jobs")
def get_jobs():
    try:
        user_id = request.args.get("user_id", type=int)

        result = db.session.execute(text("""
            SELECT 
                j.id, j.company_name, j.industry, j.job_title,
                j.skills_required, j.experience_level, j.employment_type,
                j.location, j.posted_date, j.company_size, j.tools_preferred,
                j.salary_min, j.salary_max,
                COUNT(a.id) AS applicant_count
            FROM job_market_data j
            LEFT JOIN applications a ON j.id = a.job_id
            GROUP BY j.id
            ORDER BY j.id
        """))
        rows = result.mappings().all()

        user_data = load_user_data(user_id, db.session) if user_id else None

        jobs_list = []
        for row in rows:
            job_dict = dict(row)
            count = job_dict.get("applicant_count", 0)

            if user_id and user_data:
                match = calculate_match_score_v2(job_dict, user_data)
                match_score = match["overall"]
                match_breakdown = {
                    "skills": match["skills_match"],
                    "experience": match["experience_match"],
                    "industry": match["industry_match"],
                }
                matched_skills = match.get("matched_skills", [])
                missing_skills = match.get("missing_skills", [])
            else:
                match_score = 75
                match_breakdown = {"skills": 75, "experience": 75, "industry": 75}
                matched_skills = []
                missing_skills = []

            mapped_job = {
                "id": job_dict.get("id"),
                "title": job_dict.get("job_title") or "Unknown Title",
                "company": job_dict.get("company_name") or "Unknown Company",
                "type": job_dict.get("employment_type") or "Full-time",
                "level": job_dict.get("experience_level") or "Mid",
                "location": job_dict.get("location") or "N/A",
                "industry": job_dict.get("industry") or "Tech",
                "salary": format_salary(
                    job_dict.get("salary_min"),
                    job_dict.get("salary_max")
                ),
                "applicants": f"{count} applicant{'' if count == 1 else 's'}",
                "applicant_count": count,
                "match_score": match_score,
                "match_breakdown": match_breakdown,
                "matched_skills": matched_skills,
                "missing_skills": missing_skills,
                "logo_letter": get_company_initial(job_dict.get("company_name")),
                "logoClass": "bg-blue-500",
                "work_mode": "Remote",
                "skills_required": job_dict.get("skills_required"),
                "company_size": job_dict.get("company_size"),
                "tools_preferred": job_dict.get("tools_preferred"),
                "posted_date": job_dict.get("posted_date"),
                "company_name": job_dict.get("company_name"),
                "job_title": job_dict.get("job_title"),
                "salary_min": job_dict.get("salary_min"),
                "salary_max": job_dict.get("salary_max"),
            }

            jobs_list.append(mapped_job)

        return {"jobs": jobs_list}

    except Exception as e:
        logger.error("get_jobs_failed", error=str(e), exc_info=True)
        return {"error": {"code": "INTERNAL_ERROR", "message": "ไม่สามารถโหลดงานได้"}}, 500


@bp.route("/jobs/<int:job_id>")
def get_job_detail(job_id):
    try:
        user_id = request.args.get("user_id", type=int)

        result = db.session.execute(
            text("""
                SELECT 
                    id, company_name, industry, job_title,
                    skills_required, experience_level, employment_type,
                    location, posted_date, company_size, tools_preferred,
                    salary_min, salary_max, about_role, responsibilities,
                    requirements, team_size, visa_sponsorship, posted_ago,
                    match_score
                FROM job_market_data 
                WHERE id = :job_id
            """),
            {"job_id": job_id}
        )
        job = result.mappings().first()

        if not job:
            return {"error": "Job not found"}, 404

        job_dict = serialize_row(job)
        job_dict["title"] = job_dict.get("job_title")
        job_dict["company"] = job_dict.get("company_name")
        job_dict["type"] = job_dict.get("employment_type")
        job_dict["level"] = job_dict.get("experience_level")
        job_dict["salary"] = format_salary(
            job_dict.get("salary_min"),
            job_dict.get("salary_max")
        )

        if user_id:
            user_data = load_user_data(user_id, db.session)
            match = calculate_match_score_v2(job_dict, user_data)
            job_dict["match_score"] = match["overall"]
            job_dict["match_breakdown"] = {
                "skills": match["skills_match"],
                "experience": match["experience_match"],
                "industry": match["industry_match"],
            }
            job_dict["matched_skills"] = match["matched_skills"]
            job_dict["missing_skills"] = match["missing_skills"]
        else:
            job_dict["match_score"] = 75
            job_dict["match_breakdown"] = {"skills": 75, "experience": 75, "industry": 75}
            job_dict["matched_skills"] = []
            job_dict["missing_skills"] = []

        return {"job": job_dict}

    except Exception as e:
        logger.error("operation_failed", error=str(e), exc_info=True)
        return {"error": {"code": "INTERNAL_ERROR", "message": "เกิดข้อผิดพลาด"}}, 500
