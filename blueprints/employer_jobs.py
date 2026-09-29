"""
blueprints/employer_jobs.py — Employer job management

Routes:
- GET    /api/employer/jobs
- POST   /api/employer/jobs
- PUT    /api/employer/jobs/<id>
- DELETE /api/employer/jobs/<id>
- PATCH  /api/employer/jobs/<id>/status
- GET    /api/employer/jobs/<id>/applications
"""
from flask import Blueprint, request, g
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from core.extensions import db, invalidate_jobs_cache
from core.security import require_auth, require_role, sanitize_text
from core.logging_config import get_logger

bp = Blueprint("employer_jobs", __name__, url_prefix="/api/employer")
logger = get_logger(__name__)

# Job title whitelist
ALLOWED_JOB_TITLES = {
    'AI Product Manager',
    'AI Researcher',
    'Computer Vision Engineer',
    'Data Analyst',
    'Data Scientist',
    'ML Engineer',
    'NLP Engineer',
    'Quant Researcher',
}


@bp.route("/jobs", methods=["GET"])
@require_auth
@require_role("employer")
def get_employer_jobs():
    try:
        user_id = g.user_id

        result = db.session.execute(
            text("""
                SELECT 
                    j.id,
                    j.job_title,
                    j.company_name,
                    j.location,
                    j.employment_type,
                    j.experience_level,
                    j.salary_min,
                    j.salary_max,
                    j.posted_date,
                    COALESCE(j.status, 'active') AS status,
                    COUNT(a.id) AS applicant_count
                FROM job_market_data j
                LEFT JOIN applications a ON j.id = a.job_id
                WHERE j.posted_by_user_id = :user_id
                GROUP BY j.id
                ORDER BY j.posted_date DESC
            """),
            {"user_id": user_id}
        )
        rows = result.mappings().all()

        jobs = []
        for row in rows:
            status_raw = row.get("status") or "active"
            status_label = status_raw.capitalize() if status_raw else "Active"

            jobs.append({
                "id": row["id"],
                "job_title": row["job_title"],
                "company_name": row["company_name"],
                "location": row["location"],
                "employment_type": row["employment_type"],
                "experience_level": row["experience_level"],
                "salary_min": row["salary_min"],
                "salary_max": row["salary_max"],
                "posted_date": row["posted_date"].isoformat() if row["posted_date"] else None,
                "applicant_count": row["applicant_count"],
                "status": status_label,
                "status_key": status_raw,
            })

        return {"jobs": jobs}, 200

    except Exception as e:
        logger.error("get_employer_jobs_failed", error=str(e), exc_info=True)
        return {"error": str(e)}, 500


@bp.route("/jobs", methods=["POST"])
@require_auth
@require_role("employer")
def create_employer_job():
    try:
        data = request.json
        user_id = g.user_id

        job_title = sanitize_text(data.get("job_title") or "", max_length=100)
        company_name = sanitize_text(data.get("company_name") or "", max_length=200)
        location = sanitize_text(data.get("location") or "", max_length=200)
        skills_required = sanitize_text(data.get("skills_required") or "", max_length=1000)
        tools_preferred = sanitize_text(data.get("tools_preferred") or "", max_length=1000)
        industry = sanitize_text(data.get("industry") or "", max_length=100)
        company_size = sanitize_text(data.get("company_size") or "", max_length=50)
        about_role = sanitize_text(data.get("about_role") or "", max_length=5000)
        responsibilities = sanitize_text(data.get("responsibilities") or "", max_length=5000)
        requirements = sanitize_text(data.get("requirements") or "", max_length=5000)

        employment_type = (data.get("employment_type") or "Full-time").strip()
        ALLOWED_EMPLOYMENT_TYPES = {"Full-time", "Part-time", "Contract", "Internship"}
        if employment_type not in ALLOWED_EMPLOYMENT_TYPES:
            return {
                "error": {"code": "INVALID_EMPLOYMENT_TYPE",
                          "message": f"ต้องเป็น: {', '.join(ALLOWED_EMPLOYMENT_TYPES)}"}
            }, 400

        experience_level = (data.get("experience_level") or "Mid").strip()
        ALLOWED_EXPERIENCE_LEVELS = {"Entry", "Mid", "Senior", "Lead"}
        if experience_level not in ALLOWED_EXPERIENCE_LEVELS:
            return {
                "error": {"code": "INVALID_EXPERIENCE_LEVEL",
                          "message": f"ต้องเป็น: {', '.join(ALLOWED_EXPERIENCE_LEVELS)}"}
            }, 400

        salary_min = data.get("salary_min")
        salary_max = data.get("salary_max")
        try:
            salary_min = float(salary_min) if salary_min else None
            salary_max = float(salary_max) if salary_max else None
        except (ValueError, TypeError):
            return {"error": {"code": "INVALID_SALARY", "message": "เงินเดือนต้องเป็นตัวเลข"}}, 400

        if salary_min is not None and salary_min < 0:
            return {"error": {"code": "INVALID_SALARY", "message": "เงินเดือนต้องไม่ติดลบ"}}, 400
        if salary_max is not None and salary_max < 0:
            return {"error": {"code": "INVALID_SALARY", "message": "เงินเดือนต้องไม่ติดลบ"}}, 400
        if salary_min and salary_max and salary_min > salary_max:
            return {"error": {"code": "INVALID_SALARY", "message": "เงินเดือนต่ำสุดต้อง <= สูงสุด"}}, 400

        if not job_title:
            return {"error": "Job title is required"}, 400
        if job_title not in ALLOWED_JOB_TITLES:
            return {
                "error": (
                    f"Invalid job title. Allowed: "
                    f"{', '.join(sorted(ALLOWED_JOB_TITLES))}"
                )
            }, 400
        if not company_name:
            return {"error": "Company name is required"}, 400

        result = db.session.execute(
            text("""
                INSERT INTO job_market_data (
                    job_title, company_name, location,
                    employment_type, experience_level,
                    salary_min, salary_max,
                    skills_required, tools_preferred,
                    industry, company_size,
                    about_role, responsibilities, requirements,
                    posted_by_user_id, posted_date, status
                ) VALUES (
                    :job_title, :company_name, :location,
                    :employment_type, :experience_level,
                    :salary_min, :salary_max,
                    :skills_required, :tools_preferred,
                    :industry, :company_size,
                    :about_role, :responsibilities, :requirements,
                    :user_id, NOW(), 'active'
                )
                RETURNING id
            """),
            {
                "job_title": job_title,
                "company_name": company_name,
                "location": location or None,
                "employment_type": employment_type,
                "experience_level": experience_level,
                "salary_min": float(salary_min) if salary_min else None,
                "salary_max": int(salary_max) if salary_max else None,
                "skills_required": skills_required or None,
                "tools_preferred": tools_preferred or None,
                "industry": industry or None,
                "company_size": company_size or None,
                "about_role": about_role or None,
                "responsibilities": responsibilities or None,
                "requirements": requirements or None,
                "user_id": user_id,
            }
        )
        job_id = result.scalar()
        db.session.commit()
        invalidate_jobs_cache()

        logger.info("job_created", user_id=user_id, job_id=job_id,
                    job_title=job_title, company_name=company_name)

        return {"status": "success", "message": "Job posted successfully", "job_id": job_id}, 201

    except IntegrityError as e:
        db.session.rollback()
        return {"error": "Integrity error", "detail": str(e)}, 409
    except Exception as e:
        db.session.rollback()
        logger.error("create_job_failed", error=str(e), exc_info=True)
        return {"error": str(e)}, 500


@bp.route("/jobs/<int:job_id>", methods=["PUT"])
@require_auth
@require_role("employer")
def update_employer_job(job_id):
    """Edit job post — employer only"""
    try:
        user_id = g.user_id

        owner_check = db.session.execute(
            text("SELECT posted_by_user_id FROM job_market_data WHERE id = :jid"),
            {"jid": job_id}
        ).first()

        if not owner_check:
            return {"error": {"code": "NOT_FOUND", "message": "Job not found"}}, 404
        if owner_check[0] != user_id:
            return {"error": {"code": "FORBIDDEN", "message": "No permission"}}, 403

        data = request.json
        if not data:
            return {"error": {"code": "NO_DATA", "message": "No data provided"}}, 400

        job_title = sanitize_text(data.get("job_title") or "", max_length=100)
        company_name = sanitize_text(data.get("company_name") or "", max_length=200)
        location = sanitize_text(data.get("location") or "", max_length=200)
        skills_required = sanitize_text(data.get("skills_required") or "", max_length=1000)
        tools_preferred = sanitize_text(data.get("tools_preferred") or "", max_length=1000)
        industry = sanitize_text(data.get("industry") or "", max_length=100)
        company_size = sanitize_text(data.get("company_size") or "", max_length=50)
        about_role = sanitize_text(data.get("about_role") or "", max_length=5000)
        responsibilities = sanitize_text(data.get("responsibilities") or "", max_length=5000)
        requirements = sanitize_text(data.get("requirements") or "", max_length=5000)

        employment_type = (data.get("employment_type") or "Full-time").strip()
        ALLOWED_EMPLOYMENT_TYPES = {"Full-time", "Part-time", "Contract", "Internship"}
        if employment_type not in ALLOWED_EMPLOYMENT_TYPES:
            return {"error": {"code": "INVALID_EMPLOYMENT_TYPE", "message": "Invalid type"}}, 400

        experience_level = (data.get("experience_level") or "Mid").strip()
        ALLOWED_EXPERIENCE_LEVELS = {"Entry", "Mid", "Senior", "Lead"}
        if experience_level not in ALLOWED_EXPERIENCE_LEVELS:
            return {"error": {"code": "INVALID_EXPERIENCE_LEVEL", "message": "Invalid level"}}, 400

        salary_min = data.get("salary_min")
        salary_max = data.get("salary_max")
        try:
            salary_min = float(salary_min) if salary_min else None
            salary_max = float(salary_max) if salary_max else None
        except (ValueError, TypeError):
            return {"error": {"code": "INVALID_SALARY", "message": "Invalid salary"}}, 400

        if not job_title or job_title not in ALLOWED_JOB_TITLES:
            return {"error": {"code": "INVALID_TITLE", "message": "Invalid job title"}}, 400
        if not company_name:
            return {"error": {"code": "MISSING_COMPANY", "message": "Company required"}}, 400

        db.session.execute(
            text("""
                UPDATE job_market_data SET
                    job_title = :job_title,
                    company_name = :company_name,
                    location = :location,
                    employment_type = :employment_type,
                    experience_level = :experience_level,
                    salary_min = :salary_min,
                    salary_max = :salary_max,
                    skills_required = :skills_required,
                    tools_preferred = :tools_preferred,
                    industry = :industry,
                    company_size = :company_size,
                    about_role = :about_role,
                    responsibilities = :responsibilities,
                    requirements = :requirements
                WHERE id = :jid AND posted_by_user_id = :uid
            """),
            {
                "jid": job_id, "uid": user_id,
                "job_title": job_title, "company_name": company_name,
                "location": location or None,
                "employment_type": employment_type,
                "experience_level": experience_level,
                "salary_min": salary_min, "salary_max": salary_max,
                "skills_required": skills_required or None,
                "tools_preferred": tools_preferred or None,
                "industry": industry or None, "company_size": company_size or None,
                "about_role": about_role or None,
                "responsibilities": responsibilities or None,
                "requirements": requirements or None,
            }
        )
        db.session.commit()
        invalidate_jobs_cache()

        logger.info("job_updated", user_id=user_id, job_id=job_id)

        return {"status": "success", "message": "Job updated successfully", "job_id": job_id}, 200

    except Exception as e:
        db.session.rollback()
        logger.error("update_job_failed", error=str(e), exc_info=True)
        return {"error": {"code": "INTERNAL_ERROR", "message": "Something went wrong"}}, 500


@bp.route("/jobs/<int:job_id>", methods=["DELETE"])
@require_auth
@require_role("employer")
def delete_employer_job(job_id):
    """Delete job — block if has applicants"""
    try:
        user_id = g.user_id

        owner_check = db.session.execute(
            text("SELECT posted_by_user_id, job_title FROM job_market_data WHERE id = :jid"),
            {"jid": job_id}
        ).first()

        if not owner_check:
            return {"error": {"code": "NOT_FOUND", "message": "Job not found"}}, 404
        if owner_check[0] != user_id:
            return {"error": {"code": "FORBIDDEN", "message": "No permission"}}, 403

        applicant_count = db.session.execute(
            text("SELECT COUNT(*) FROM applications WHERE job_id = :jid"),
            {"jid": job_id}
        ).scalar() or 0

        if applicant_count > 0:
            return {
                "error": {
                    "code": "HAS_APPLICANTS",
                    "message": f"Cannot delete — this job has {applicant_count} applicant(s). Pause it instead.",
                    "applicant_count": applicant_count,
                }
            }, 409

        db.session.execute(
            text("DELETE FROM job_market_data WHERE id = :jid AND posted_by_user_id = :uid"),
            {"jid": job_id, "uid": user_id}
        )

        db.session.commit()
        invalidate_jobs_cache()

        logger.info("job_deleted", user_id=user_id, job_id=job_id, job_title=owner_check[1])

        return {"status": "success", "message": "Job deleted successfully", "job_id": job_id}, 200

    except Exception as e:
        db.session.rollback()
        logger.error("delete_job_failed", error=str(e), exc_info=True)
        return {"error": {"code": "INTERNAL_ERROR", "message": "Something went wrong"}}, 500


@bp.route("/jobs/<int:job_id>/status", methods=["PATCH"])
@require_auth
@require_role("employer")
def update_job_status(job_id):
    """Pause / Activate job"""
    try:
        user_id = g.user_id

        data = request.json or {}
        new_status = (data.get("status") or "").lower().strip()

        if new_status not in ("active", "paused"):
            return {"error": {"code": "INVALID_STATUS", "message": "Status must be 'active' or 'paused'"}}, 400

        owner_check = db.session.execute(
            text("SELECT posted_by_user_id FROM job_market_data WHERE id = :jid"),
            {"jid": job_id}
        ).first()

        if not owner_check:
            return {"error": {"code": "NOT_FOUND", "message": "Job not found"}}, 404
        if owner_check[0] != user_id:
            return {"error": {"code": "FORBIDDEN", "message": "No permission"}}, 403

        db.session.execute(
            text("""
                UPDATE job_market_data 
                SET status = :status
                WHERE id = :jid AND posted_by_user_id = :uid
            """),
            {"jid": job_id, "uid": user_id, "status": new_status}
        )
        
        db.session.commit()
        invalidate_jobs_cache()

        logger.info("job_status_updated", user_id=user_id, job_id=job_id, new_status=new_status)

        return {
            "status": "success",
            "message": f"Job is now {new_status}",
            "job_id": job_id,
            "new_status": new_status,
        }, 200

    except Exception as e:
        db.session.rollback()
        logger.error("update_job_status_failed", error=str(e), exc_info=True)
        return {"error": {"code": "INTERNAL_ERROR", "message": "Something went wrong"}}, 500


@bp.route("/jobs/<int:job_id>/applications", methods=["GET"])
@require_auth
@require_role("employer")
def get_job_applications(job_id):
    try:
        job_check = db.session.execute(
            text("SELECT id, job_title, posted_by_user_id FROM job_market_data WHERE id = :jid"),
            {"jid": job_id}
        ).first()
        if not job_check:
            return {"error": "Job not found"}, 404

        if job_check[2] != g.user_id:
            return {"error": {"code": "FORBIDDEN", "message": "ไม่มีสิทธิ์"}}, 403

        result = db.session.execute(
            text("""
                SELECT 
                    a.id, a.user_id, a.full_name, a.email, a.phone,
                    a.location, a.status, a.applied_date, a.resume_filename,
                    a.resume_url,
                    u.resume_url AS user_resume_url
                FROM applications a
                LEFT JOIN users u ON a.user_id = u.id
                WHERE a.job_id = :jid
                ORDER BY a.applied_date DESC
            """),
            {"jid": job_id}
        )

        rows = result.mappings().all()

        applications = []
        for row in rows:
            applications.append({
                "id": row["id"],
                "user_id": row["user_id"],
                "full_name": row["full_name"],
                "email": row["email"],
                "phone": row["phone"],
                "location": row["location"],
                "status": row["status"] or "applied",
                "applied_date": row["applied_date"].isoformat() if row["applied_date"] else None,
                "resume_filename": row["resume_filename"],
                "resume_url": row["resume_url"],
                "user_resume_url": row["user_resume_url"],
            })

        return {
            "job_id": job_id,
            "job_title": job_check[1],
            "applications": applications,
            "total": len(applications),
        }, 200

    except Exception as e:
        logger.error("get_job_applications_failed", error=str(e), exc_info=True)
        return {"error": str(e)}, 500
