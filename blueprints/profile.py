"""
blueprints/profile.py — Candidate profile routes

Routes:
- GET /api/profile/<int:user_id>
- GET /api/profile/<int:user_id>/full
- PUT /api/profile/<int:user_id>
"""
from sqlalchemy import text
from flask import Blueprint, request, g

from core.extensions import db, invalidate_jobs_cache
from core.security import require_auth, is_valid_phone, sanitize_text
from core.logging_config import get_logger
from services.serializers import serialize_row
from services.users import normalize_phone

bp = Blueprint("profile", __name__, url_prefix="/api")
logger = get_logger(__name__)


@bp.route("/profile/<int:user_id>", methods=["GET"])
@require_auth
def get_profile(user_id):
    # ⭐ ตรวจเจ้าของ
    if user_id != g.user_id:
        return {"error": {"code": "FORBIDDEN", "message": "ไม่มีสิทธิ์"}}, 403

    try:
        result = db.session.execute(
            text("""
                SELECT id, username, email, full_name, phone, location, 
                       bio, profile_image, created_at
                FROM users 
                WHERE id = :user_id
            """),
            {"user_id": user_id}
        )
        user = result.mappings().first()

        if not user:
            return {"error": "User not found"}, 404

        return {"profile": serialize_row(user)}
    except Exception as e:
        return {"error": str(e)}, 500


@bp.route("/profile/<int:user_id>/full", methods=["GET"])
@require_auth
def get_full_profile(user_id):
    # ⭐ ตรวจเจ้าของ
    if user_id != g.user_id:
        return {"error": {"code": "FORBIDDEN", "message": "ไม่มีสิทธิ์"}}, 403

    try:
        user_result = db.session.execute(
            text("""
                SELECT id, username, email, full_name, phone, location, 
                    bio, profile_image, industry, resume_url, resume_filename,
                    created_at, updated_at
                FROM users WHERE id = :user_id
            """),
            {"user_id": user_id}
        )
        user = user_result.mappings().first()
        if not user:
            return {"error": "User not found"}, 404

        skills_result = db.session.execute(
            text("""
                SELECT id, skill_name, skill_level 
                FROM user_skills WHERE user_id = :user_id
                ORDER BY id
            """),
            {"user_id": user_id}
        )
        skills = [serialize_row(row) for row in skills_result.mappings().all()]

        exp_result = db.session.execute(
            text("""
                SELECT id, job_title, company_name, location, 
                       start_date, end_date, is_current, description
                FROM user_experience WHERE user_id = :user_id
                ORDER BY start_date DESC NULLS FIRST
            """),
            {"user_id": user_id}
        )
        experiences = [serialize_row(row) for row in exp_result.mappings().all()]

        edu_result = db.session.execute(
            text("""
                SELECT id, institution, degree, field_of_study,
                       start_date, end_date, is_current, gpa
                FROM user_education WHERE user_id = :user_id
                ORDER BY start_date DESC NULLS FIRST
            """),
            {"user_id": user_id}
        )
        educations = [serialize_row(row) for row in edu_result.mappings().all()]

        return {
            "profile": serialize_row(user),
            "skills": skills,
            "experiences": experiences,
            "educations": educations
        }, 200

    except Exception as e:
        logger.error("operation_failed", error=str(e), exc_info=True)
        return {"error": {"code": "INTERNAL_ERROR", "message": "เกิดข้อผิดพลาด"}}, 500


@bp.route("/profile/<int:user_id>", methods=["PUT"])
@require_auth
def update_profile(user_id):
    # ⭐ ตรวจเจ้าของ
    if user_id != g.user_id:
        return {"error": {"code": "FORBIDDEN", "message": "ไม่มีสิทธิ์"}}, 403

    try:
        data = request.json

        if not data:
            return {"error": "No data provided"}, 400

        # ⭐ Sprint 3: Validation + Sanitize
        # Phone validation
        phone = data.get("phone")
        if phone:
            if not is_valid_phone(phone):
                return {"error": {"code": "INVALID_PHONE", "message": "เบอร์โทรไม่ถูกต้อง"}}, 400
            phone = normalize_phone(phone)

        # Sanitize text (XSS protection)
        full_name = sanitize_text(data.get("full_name") or "", max_length=100) or None
        bio = sanitize_text(data.get("bio") or "", max_length=2000) or None
        location = sanitize_text(data.get("location") or "", max_length=200) or None
        industry = sanitize_text(data.get("industry") or "", max_length=100) or None

        db.session.execute(
            text("""
                UPDATE users 
                SET full_name = COALESCE(:full_name, full_name),
                    phone = COALESCE(:phone, phone),
                    location = COALESCE(:location, location),
                    bio = COALESCE(:bio, bio),
                    industry = COALESCE(:industry, industry),
                    profile_image = COALESCE(:profile_image, profile_image),
                    updated_at = NOW()
                WHERE id = :user_id
            """),
            {
                "user_id": user_id,
                "full_name": full_name,
                "phone": phone,
                "location": location,
                "bio": bio,
                "industry": industry,
                "profile_image": data.get("profile_image"),
            }
        )

        if "skills" in data:
            db.session.execute(
                text("DELETE FROM user_skills WHERE user_id = :user_id"),
                {"user_id": user_id}
            )
            if data["skills"]:
                db.session.execute(
                    text("""
                        INSERT INTO user_skills (user_id, skill_name, skill_level)
                        VALUES (:user_id, :skill_name, :skill_level)
                    """),
                    [
                        {
                            "user_id": user_id,
                            "skill_name": s.get("name") or s.get("skill_name"),
                            "skill_level": s.get("level") or s.get("skill_level", "Intermediate")
                        }
                        for s in data["skills"]
                    ]
                )

        if "experiences" in data:
            db.session.execute(
                text("DELETE FROM user_experience WHERE user_id = :user_id"),
                {"user_id": user_id}
            )
            if data["experiences"]:
                db.session.execute(
                    text("""
                        INSERT INTO user_experience 
                            (user_id, job_title, company_name, location,
                             start_date, end_date, is_current, description)
                        VALUES 
                            (:user_id, :job_title, :company_name, :location,
                             :start_date, :end_date, :is_current, :description)
                    """),
                    [
                        {
                            "user_id": user_id,
                            "job_title": e.get("job_title"),
                            "company_name": e.get("company_name"),
                            "location": e.get("location"),
                            "start_date": e.get("start_date") or None,
                            "end_date": e.get("end_date") or None,
                            "is_current": e.get("is_current", False),
                            "description": e.get("description", "")
                        }
                        for e in data["experiences"]
                    ]
                )

        if "educations" in data:
            db.session.execute(
                text("DELETE FROM user_education WHERE user_id = :user_id"),
                {"user_id": user_id}
            )
            if data["educations"]:
                db.session.execute(
                    text("""
                        INSERT INTO user_education 
                            (user_id, institution, degree, field_of_study,
                             start_date, end_date, is_current, gpa)
                        VALUES 
                            (:user_id, :institution, :degree, :field_of_study,
                             :start_date, :end_date, :is_current, :gpa)
                    """),
                    [
                        {
                            "user_id": user_id,
                            "institution": ed.get("institution"),
                            "degree": ed.get("degree"),
                            "field_of_study": ed.get("field_of_study"),
                            "start_date": ed.get("start_date") or None,
                            "end_date": ed.get("end_date") or None,
                            "is_current": ed.get("is_current", False),
                            "gpa": ed.get("gpa") or None
                        }
                        for ed in data["educations"]
                    ]
                )

        db.session.commit()
        invalidate_jobs_cache()

        logger.info("profile_updated", user_id=user_id)

        return {"status": "success", "message": "Profile updated successfully"}, 200

    except IntegrityError as e:
        db.session.rollback()
        return {"error": "Constraint violation", "detail": str(e)}, 409
    except Exception as e:
        db.session.rollback()
        logger.error("operation_failed", error=str(e), exc_info=True)
        return {"error": {"code": "INTERNAL_ERROR", "message": "เกิดข้อผิดพลาด"}}, 500
