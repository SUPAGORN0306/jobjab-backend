"""
blueprints/employer_applications.py — Employer view applicant details

Routes:
- GET /api/employer/applications/<int:application_id>/detail
- PUT /api/employer/applications/<int:application_id>/status
"""
from datetime import datetime

from flask import Blueprint, request, g
from sqlalchemy import text

from core.extensions import db
from core.security import require_auth, require_role
from core.logging_config import get_logger
from services.match_score import calculate_match_score_v2
from services.serializers import serialize_row

bp = Blueprint("employer_applications", __name__, url_prefix="/api/employer")
logger = get_logger(__name__)


@bp.route("/applications/<int:application_id>/detail", methods=["GET"])
@require_auth
@require_role("employer")
def get_application_snapshot(application_id):
    try:
        app_result = db.session.execute(
            text("""
                SELECT 
                    a.id, a.user_id, a.job_id, a.status,
                    a.full_name, a.email, a.phone, a.location,
                    a.resume_filename, a.resume_url, a.cover_letter,
                    a.applied_date, a.updated_at,
                    j.job_title, j.company_name,
                    j.employment_type, j.experience_level,
                    j.location AS job_location,
                    j.salary_min, j.salary_max,
                    u.resume_url AS user_resume_url
                FROM applications a
                LEFT JOIN job_market_data j ON a.job_id = j.id
                LEFT JOIN users u ON a.user_id = u.id
                WHERE a.id = :aid
            """),
            {"aid": application_id}
        )
        application = app_result.mappings().first()
        if not application:
            return {"error": "Application not found"}, 404

        # ตรวจว่า employer เป็นเจ้าของ job ที่ application นี้สมัคร
        job_owner = db.session.execute(
            text("SELECT posted_by_user_id FROM job_market_data WHERE id = :jid"),
            {"jid": application["job_id"]}
        ).first()

        if not job_owner or job_owner[0] != g.user_id:
            return {"error": {"code": "FORBIDDEN", "message": "ไม่มีสิทธิ์"}}, 403

        skills_result = db.session.execute(
            text("""
                SELECT skill_name, skill_level 
                FROM application_skills WHERE application_id = :aid
                ORDER BY id
            """),
            {"aid": application_id}
        )
        skills = [serialize_row(row) for row in skills_result.mappings().all()]

        exp_result = db.session.execute(
            text("""
                SELECT job_title, company_name, location,
                       start_date, end_date, is_current, description
                FROM application_experiences WHERE application_id = :aid
                ORDER BY start_date DESC NULLS FIRST
            """),
            {"aid": application_id}
        )
        experiences = [serialize_row(row) for row in exp_result.mappings().all()]

        edu_result = db.session.execute(
            text("""
                SELECT institution, degree, field_of_study,
                       start_date, end_date, is_current, gpa
                FROM application_educations WHERE application_id = :aid
                ORDER BY start_date DESC NULLS FIRST
            """),
            {"aid": application_id}
        )
        educations = [serialize_row(row) for row in edu_result.mappings().all()]

        # Calculate match score from snapshot
        try:
            job_data = db.session.execute(
                text("""
                    SELECT skills_required, experience_level, industry, location
                    FROM job_market_data WHERE id = :jid
                """),
                {"jid": application["job_id"]}
            ).mappings().first()

            user_data_row = db.session.execute(
                text("SELECT industry, location FROM users WHERE id = :uid"),
                {"uid": application["user_id"]}
            ).mappings().first()

            total_years = 0.0
            for exp in experiences:
                if exp.get("start_date"):
                    try:
                        start = datetime.fromisoformat(str(exp["start_date"]))
                        if exp.get("is_current") or not exp.get("end_date"):
                            end = datetime.now()
                        else:
                            end = datetime.fromisoformat(str(exp["end_date"]))
                        total_years += (end - start).days / 365.25
                    except (ValueError, TypeError):
                        continue

            candidate_data = {
                "skills": [s["skill_name"] for s in skills],
                "total_years": total_years,
                "industry": (user_data_row["industry"] if user_data_row else "") or "",
                "location": (user_data_row["location"] if user_data_row else "") or "",
            }

            match_result = calculate_match_score_v2(dict(job_data), candidate_data)

            logger.info(
                "match_score_calculated",
                application_id=application_id,
                overall=match_result["overall"],
            )
        except Exception as e:
            logger.error("match_score_calculation_failed", error=str(e), exc_info=True)
            match_result = {
                "overall": 0, "skills_match": 0, "experience_match": 0,
                "industry_match": 0, "matched_skills": [], "missing_skills": [],
                "total_years": 0,
                "weights": {"skills": 0.5, "experience": 0.3, "industry": 0.2},
            }

        return {
            "application": serialize_row(application),
            "skills": skills,
            "experiences": experiences,
            "educations": educations,
            "match": match_result,
        }, 200

    except Exception as e:
        logger.error("get_application_detail_failed", error=str(e), exc_info=True)
        return {"error": str(e)}, 500


@bp.route("/applications/<int:application_id>/status", methods=["PUT"])
@require_auth
@require_role("employer")
def update_application_status(application_id):
    try:
        data = request.json
        new_status = (data.get("status") or "").lower()

        allowed = {"applied", "reviewing", "interview", "rejected"}
        if new_status not in allowed:
            return {"error": f"Invalid status. Allowed: {', '.join(allowed)}"}, 400

        check = db.session.execute(
            text("SELECT id, status, job_id FROM applications WHERE id = :aid"),
            {"aid": application_id}
        ).first()
        if not check:
            return {"error": "Application not found"}, 404

        job_owner = db.session.execute(
            text("SELECT posted_by_user_id FROM job_market_data WHERE id = :jid"),
            {"jid": check[2]}
        ).first()

        if not job_owner or job_owner[0] != g.user_id:
            return {"error": {"code": "FORBIDDEN", "message": "ไม่มีสิทธิ์"}}, 403

        db.session.execute(
            text("""
                UPDATE applications 
                SET status = :status, updated_at = NOW()
                WHERE id = :aid
            """),
            {"status": new_status, "aid": application_id}
        )
        db.session.commit()

        logger.info(
            "application_status_updated",
            user_id=g.user_id,
            application_id=application_id,
            new_status=new_status,
        )

        return {
            "status": "success",
            "message": f"Status updated to '{new_status}'",
            "application_id": application_id,
            "new_status": new_status,
        }, 200

    except Exception as e:
        db.session.rollback()
        logger.error("update_status_failed", error=str(e), exc_info=True)
        return {"error": str(e)}, 500
