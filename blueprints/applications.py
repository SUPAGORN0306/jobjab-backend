"""
blueprints/applications.py — Candidate application routes

Routes:
- GET /api/applications
- POST /api/applications
- GET /api/applications/user/<int:user_id>
- GET /api/applications/<int:application_id>/detail
"""
from sqlalchemy import text
from flask import Blueprint, request, jsonify, g

from core.extensions import db, invalidate_jobs_cache
from core.security import (
    require_auth,
    is_valid_email, is_valid_phone, is_supabase_url, sanitize_text,
)
from core.logging_config import get_logger
from services.serializers import serialize_row
from services.users import normalize_phone

bp = Blueprint("applications", __name__, url_prefix="/api")
logger = get_logger(__name__)


@bp.route("/applications", methods=["GET"])
@require_auth
def get_applications():
    # ⭐ return เฉพาะของตัวเอง
    try:
        sql = text("""
            SELECT 
                a.id, a.job_id, a.status, a.full_name, a.email, a.phone,
                a.applied_date, a.updated_at,
                j.job_title as title, j.company_name as company, 
                j.location, j.salary_min, j.salary_max
            FROM applications a
            LEFT JOIN job_market_data j ON a.job_id = j.id
            WHERE a.user_id = :user_id
            ORDER BY a.applied_date DESC
        """)
        result = db.session.execute(sql, {"user_id": g.user_id})
        rows = result.mappings().all()

        return jsonify({
            "applications": [serialize_row(row) for row in rows]
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/applications/user/<int:user_id>", methods=["GET"])
@require_auth
def get_user_applications(user_id):
    # ⭐ ตรวจเจ้าของ
    if user_id != g.user_id:
        return {"error": {"code": "FORBIDDEN", "message": "ไม่มีสิทธิ์"}}, 403

    try:
        result = db.session.execute(
            text("""
                SELECT 
                    a.id, a.job_id, a.status, a.full_name, a.email, a.phone,
                    a.cover_letter, a.applied_date, a.updated_at,
                    j.job_title, j.company_name as company,
                    j.location, j.salary_min, j.salary_max
                FROM applications a
                LEFT JOIN job_market_data j ON a.job_id = j.id
                WHERE a.user_id = :user_id
                ORDER BY a.applied_date DESC
            """),
            {"user_id": user_id}
        )
        rows = result.mappings().all()
        return {"applications": [serialize_row(row) for row in rows]}
    except Exception as e:
        return {"error": str(e)}, 500


@bp.route("/applications/<int:application_id>/detail", methods=["GET"])
@require_auth
def get_application_detail(application_id):
    try:
        app_result = db.session.execute(
            text("""
                SELECT 
                    a.id, a.user_id, a.job_id, a.status,
                    a.full_name, a.email, a.phone, a.location,
                    a.resume_filename, a.resume_url, a.cover_letter,
                    a.applied_date, a.updated_at,
                    j.job_title, j.company_name, j.location AS job_location,
                    j.salary_min, j.salary_max, j.employment_type,
                    u.resume_url AS user_resume_url
                FROM applications a
                LEFT JOIN job_market_data j ON a.job_id = j.id
                LEFT JOIN users u ON a.user_id = u.id
                WHERE a.id = :application_id
            """),
            {"application_id": application_id}
        )

        application = app_result.mappings().first()
        if not application:
            return {"error": "Application not found"}, 404

        # ⭐ ตรวจเจ้าของ
        if application["user_id"] != g.user_id:
            return {"error": {"code": "FORBIDDEN", "message": "ไม่มีสิทธิ์"}}, 403

        skills_result = db.session.execute(
            text("""
                SELECT skill_name, skill_level 
                FROM application_skills WHERE application_id = :application_id
            """),
            {"application_id": application_id}
        )
        skills = [serialize_row(row) for row in skills_result.mappings().all()]

        exp_result = db.session.execute(
            text("""
                SELECT job_title, company_name, location,
                       start_date, end_date, is_current, description
                FROM application_experiences WHERE application_id = :application_id
                ORDER BY start_date DESC NULLS FIRST
            """),
            {"application_id": application_id}
        )
        experiences = [serialize_row(row) for row in exp_result.mappings().all()]

        edu_result = db.session.execute(
            text("""
                SELECT institution, degree, field_of_study,
                       start_date, end_date, is_current, gpa
                FROM application_educations WHERE application_id = :application_id
                ORDER BY start_date DESC NULLS FIRST
            """),
            {"application_id": application_id}
        )
        educations = [serialize_row(row) for row in edu_result.mappings().all()]

        return {
            "application": serialize_row(application),
            "skills": skills,
            "experiences": experiences,
            "educations": educations
        }, 200

    except Exception as e:
        logger.error("operation_failed", error=str(e), exc_info=True)
        return {"error": {"code": "INTERNAL_ERROR", "message": "เกิดข้อผิดพลาด"}}, 500


@bp.route("/applications", methods=["POST"])
@require_auth
def create_application():
    try:
        data = request.json

        if not data:
            return {"error": "No data provided"}, 400

        # ⭐ ใช้ g.user_id
        user_id = g.user_id
        job_id = data.get("jobId") or data.get("job_id")

        # ⭐ Sprint 3: Validation + Sanitize
        full_name = sanitize_text(
            data.get("fullName") or data.get("full_name") or "",
            max_length=100,
        ) or None

        email_raw = (data.get("email") or "").strip().lower()
        if email_raw and not is_valid_email(email_raw):
            return {"error": {"code": "INVALID_EMAIL", "message": "อีเมลไม่ถูกต้อง"}}, 400
        email = email_raw or None

        phone = data.get("phone")
        if phone:
            if not is_valid_phone(phone):
                return {"error": {"code": "INVALID_PHONE", "message": "เบอร์โทรไม่ถูกต้อง"}}, 400
            phone = normalize_phone(phone)

        location = sanitize_text(
            data.get("location") or "",
            max_length=200,
        ) or ""

        resume_filename = sanitize_text(
            data.get("resumeFilename") or data.get("resume_filename") or "",
            max_length=255,
        ) or ""

        resume_url = data.get("resumeUrl") or data.get("resume_url") or ""
        if resume_url and not is_supabase_url(resume_url):
            return {"error": {"code": "INVALID_RESUME_URL", "message": "URL resume ไม่ถูกต้อง"}}, 400

        cover_letter = sanitize_text(
            data.get("coverLetter") or data.get("cover_letter") or "",
            max_length=5000,
        ) or ""

        skills = data.get("skills", [])
        experiences = data.get("experiences", [])
        educations = data.get("educations", [])

        if not job_id:
            return {"error": "job_id is required"}, 400
        if not full_name or not email:
            return {"error": "full_name and email are required"}, 400

        if not resume_url:
            return {"error": "Please upload a resume first"}, 400

        check = db.session.execute(
            text("""
                SELECT id FROM applications
                WHERE user_id = :user_id AND job_id = :job_id
            """),
            {"user_id": user_id, "job_id": job_id}
        )
        if check.first():
            return {"error": "You have already applied for this position"}, 400

        job_check = db.session.execute(
            text("""
                SELECT id, posted_by_user_id
                FROM job_market_data WHERE id = :job_id
            """),
            {"job_id": job_id}
        )

        job_row = job_check.first()
        if not job_row:
            return {"error": "Job not found"}, 404

        if job_row[1] and job_row[1] == user_id:
            return {"error": "You cannot apply to a job that you posted"}, 400

        result = db.session.execute(
            text("""
                INSERT INTO applications
                    (user_id, job_id, full_name, email, phone, location,
                     resume_filename, resume_url, cover_letter, status, applied_date)
                VALUES
                    (:user_id, :job_id, :full_name, :email, :phone, :location,
                     :resume_filename, :resume_url, :cover_letter, 'applied', NOW())
                RETURNING id
            """),
            {
                "user_id": user_id,
                "job_id": job_id,
                "full_name": full_name,
                "email": email,
                "phone": phone,
                "location": location,
                "resume_filename": resume_filename,
                "resume_url": resume_url,
                "cover_letter": cover_letter,
            }
        )
        application_id = result.scalar()

        if skills:
            db.session.execute(
                text("""
                    INSERT INTO application_skills
                        (application_id, skill_name, skill_level)
                    VALUES
                        (:application_id, :skill_name, :skill_level)
                """),
                [
                    {
                        "application_id": application_id,
                        "skill_name": s.get("name") or s.get("skill_name"),
                        "skill_level": s.get("level") or s.get("skill_level", "Intermediate")
                    }
                    for s in skills
                ]
            )

        if experiences:
            db.session.execute(
                text("""
                    INSERT INTO application_experiences
                        (application_id, job_title, company_name, location,
                         start_date, end_date, is_current, description)
                    VALUES
                        (:application_id, :job_title, :company_name, :location,
                         :start_date, :end_date, :is_current, :description)
                """),
                [
                    {
                        "application_id": application_id,
                        "job_title": e.get("job_title"),
                        "company_name": e.get("company_name"),
                        "location": e.get("location"),
                        "start_date": e.get("start_date") or None,
                        "end_date": e.get("end_date") or None,
                        "is_current": e.get("is_current", False),
                        "description": e.get("description", "")
                    }
                    for e in experiences
                ]
            )

        if educations:
            db.session.execute(
                text("""
                    INSERT INTO application_educations
                        (application_id, institution, degree, field_of_study,
                         start_date, end_date, is_current, gpa)
                    VALUES
                        (:application_id, :institution, :degree, :field_of_study,
                         :start_date, :end_date, :is_current, :gpa)
                """),
                [
                    {
                        "application_id": application_id,
                        "institution": ed.get("institution"),
                        "degree": ed.get("degree"),
                        "field_of_study": ed.get("field_of_study"),
                        "start_date": ed.get("start_date") or None,
                        "end_date": ed.get("end_date") or None,
                        "is_current": ed.get("is_current", False),
                        "gpa": ed.get("gpa") or None
                    }
                    for ed in educations
                ]
            )

        db.session.commit()
        invalidate_jobs_cache()

        logger.info(
            "application_submitted",
            user_id=user_id,
            job_id=job_id,
            application_id=application_id,
        )

        return {
            "status": "success",
            "message": "Application submitted successfully!",
            "application_id": application_id
        }, 201

    except IntegrityError as e:
        db.session.rollback()
        return {"error": "Integrity error", "detail": str(e)}, 409
    except Exception as e:
        db.session.rollback()
        logger.error("operation_failed", error=str(e), exc_info=True)
        return {"error": {"code": "INTERNAL_ERROR", "message": "เกิดข้อผิดพลาด"}}, 500
