"""
blueprints/employer_applications.py — Employer view applicant details

Routes:
- GET /api/employer/applications/<int:application_id>/detail
- PUT /api/employer/applications/<int:application_id>/status

Performance:
- Cache per application_id (TTL 120s)
- Uses calculate_match_score_v2_prepared for snapshot scoring
- Invalidation: cache.clear() on status update
"""
from datetime import datetime

from flask import Blueprint, request, g
from sqlalchemy import text

from core.extensions import db, cache, invalidate_jobs_cache
from core.security import require_auth, require_role
from core.logging_config import get_logger
from services.match_score import (
    prepare_user_data,
    calculate_match_score_v2_prepared,
)
from services.serializers import serialize_row

bp = Blueprint("employer_applications", __name__, url_prefix="/api/employer")
logger = get_logger(__name__)

DETAIL_CACHE_TTL = 120   # 2 min


@bp.route("/applications/<int:application_id>/detail", methods=["GET"])
@require_auth
@require_role("employer")
def get_application_snapshot(application_id):
    user_id = g.user_id

    # Cache per application
    cache_key = f"app_detail:app{application_id}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached, 200

    try:
        # 1. Application + job + user info (single query)
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
                    j.posted_by_user_id,
                    u.resume_url AS user_resume_url,
                    u.industry AS user_industry,
                    u.location AS user_location
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

        # 2. Ownership check (use posted_by_user_id already in main query)
        if application["posted_by_user_id"] != user_id:
            return {"error": {"code": "FORBIDDEN", "message": "ไม่มีสิทธิ์"}}, 403

        # 3. Snapshot: skills / experiences / educations
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

        # 4. Match score (using prepared path)
        try:
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

            cand_data = {
                "skills": [s["skill_name"] for s in skills],
                "total_years": total_years,
                "industry": application["user_industry"] or "",
                "location": application["user_location"] or "",
            }
            prepared = prepare_user_data(cand_data)

            job_dict = {
                "skills_required": None,  # need to fetch — see below
                "experience_level": application["experience_level"],
                "industry": None,
                "location": application["job_location"],
            }

            # Fetch skills_required + industry from job
            job_extra = db.session.execute(
                text("""
                    SELECT skills_required, industry 
                    FROM job_market_data WHERE id = :jid
                """),
                {"jid": application["job_id"]}
            ).mappings().first()

            if job_extra:
                job_dict["skills_required"] = job_extra["skills_required"]
                job_dict["industry"] = job_extra["industry"]

            match_result = calculate_match_score_v2_prepared(job_dict, prepared)

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

        # 5. Build response
        app_serialized = serialize_row(application)
        # ลบ fields ที่ไม่ควรส่งออก (internal)
        app_serialized.pop("posted_by_user_id", None)
        app_serialized.pop("user_industry", None)
        app_serialized.pop("user_location", None)

        response = {
            "application": app_serialized,
            "skills": skills,
            "experiences": experiences,
            "educations": educations,
            "match": match_result,
        }

        cache.set(cache_key, response, timeout=DETAIL_CACHE_TTL)
        return response, 200

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

        # 1. Ownership check (single query with JOIN)
        check = db.session.execute(
            text("""
                SELECT a.id, a.status, a.job_id, j.posted_by_user_id
                FROM applications a
                INNER JOIN job_market_data j ON j.id = a.job_id
                WHERE a.id = :aid
            """),
            {"aid": application_id}
        ).first()
        if not check:
            return {"error": "Application not found"}, 404

        if check[3] != g.user_id:
            return {"error": {"code": "FORBIDDEN", "message": "ไม่มีสิทธิ์"}}, 403

        # 2. Update
        db.session.execute(
            text("""
                UPDATE applications 
                SET status = :status, updated_at = NOW()
                WHERE id = :aid
            """),
            {"status": new_status, "aid": application_id}
        )
        db.session.commit()

        # 3. Invalidate related caches
        #    - detail cache ของ application นี้
        cache.delete(f"app_detail:app{application_id}")
        #    - analytics cache ของ employer นี้ (มี status breakdown + funnel)
        #      ใช้ invalidate_jobs_cache() ล้างทั้งหมด (เรียบง่าย)
        invalidate_jobs_cache()

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


# ============================================================
# BFF: All Applications (Bulk endpoint)
# ============================================================
# ใช้แทน N+1 loop fetchJobApplications ที่ frontend
# - 1 query JOIN jobs + applications + users
# - return jobs + applications + stats พร้อมใช้
# - cache 60s ต่อ employer

ALL_APPS_CACHE_TTL = 60   # 1 min


@bp.route("/applications/all", methods=["GET"])
@require_auth
@require_role("employer")
def get_all_employer_applications():
    """
    BFF endpoint — return ทุก jobs + applications ของ employer ใน 1 call
    
    ใช้แทนการ loop fetchJobApplications ทีละ job ที่ frontend
    (เดิม: 10 requests × 1-3s = 10-30s → ตอนนี้: 1 request ~500ms)
    
    Query params:
    - limit: default 500, max 1000 (safety guard)
    - offset: default 0
    """
    user_id = g.user_id
    limit = request.args.get("limit", type=int, default=500)
    offset = request.args.get("offset", type=int, default=0)
    limit = max(1, min(limit, 1000))

    # Cache per employer
    cache_key = f"emp_all_apps:emp{user_id}:l{limit}:o{offset}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached, 200

    try:
        # ─────────────────────────────────────────────────
        # 1. Jobs ของ employer (พร้อม applicant_count)
        # ─────────────────────────────────────────────────
        jobs_result = db.session.execute(
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
                WHERE j.posted_by_user_id = :uid
                GROUP BY j.id
                ORDER BY j.posted_date DESC
            """),
            {"uid": user_id}
        )
        jobs_rows = jobs_result.mappings().all()

        jobs = []
        job_ids = []
        for row in jobs_rows:
            status_raw = row.get("status") or "active"
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
                "status": status_raw.capitalize() if status_raw else "Active",
                "status_key": status_raw,
            })
            job_ids.append(row["id"])

        # ถ้าไม่มี job เลย → return empty (เร็ว)
        if not job_ids:
            response = {
                "jobs": [],
                "applications": [],
                "stats": {
                    "total_jobs": 0,
                    "active_jobs": 0,
                    "total_applicants": 0,
                    "responded_count": 0,
                    "response_rate": 0,
                },
            }
            cache.set(cache_key, response, timeout=ALL_APPS_CACHE_TTL)
            return response, 200

        # ─────────────────────────────────────────────────
        # 2. All applications ของ jobs เหล่านี้ (1 query)
        # ─────────────────────────────────────────────────
        # ⭐ ใช้ ANY(:job_ids) แทน IN — ปลอดภัย + ใช้ index
        apps_result = db.session.execute(
            text("""
                SELECT 
                    a.id, a.user_id, a.job_id, a.status,
                    a.full_name, a.email, a.phone, a.location,
                    a.resume_filename, a.resume_url,
                    a.applied_date, a.updated_at,
                    j.job_title,
                    u.resume_url AS user_resume_url
                FROM applications a
                INNER JOIN job_market_data j ON j.id = a.job_id
                LEFT JOIN users u ON a.user_id = u.id
                WHERE a.job_id = ANY(:job_ids)
                ORDER BY a.applied_date DESC
                LIMIT :limit OFFSET :offset
            """),
            {"job_ids": job_ids, "limit": limit, "offset": offset}
        )
        apps_rows = apps_result.mappings().all()

        applications = []
        for row in apps_rows:
            applications.append({
                "id": row["id"],
                "user_id": row["user_id"],
                "job_id": row["job_id"],
                "job_title": row["job_title"],        # ⭐ frontend ไม่ต้อง map เอง
                "status": row["status"] or "applied",
                "full_name": row["full_name"],
                "email": row["email"],
                "phone": row["phone"],
                "location": row["location"],
                "resume_filename": row["resume_filename"],
                "resume_url": row["resume_url"],
                "user_resume_url": row["user_resume_url"],
                "applied_date": row["applied_date"].isoformat() if row["applied_date"] else None,
                "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
            })

        # ─────────────────────────────────────────────────
        # 3. Stats (คำนวณให้เสร็จ — frontend ไม่ต้องนับเอง)
        # ─────────────────────────────────────────────────
        total_applicants = sum(j["applicant_count"] for j in jobs)
        active_jobs = sum(1 for j in jobs if j["status_key"] == "active")
        responded_count = sum(
            1 for a in applications 
            if a["status"] in ("reviewing", "interview")
        )
        response_rate = (
            round((responded_count / len(applications)) * 100)
            if applications else 0
        )

        response = {
            "jobs": jobs,
            "applications": applications,
            "stats": {
                "total_jobs": len(jobs),
                "active_jobs": active_jobs,
                "total_applicants": total_applicants,
                "responded_count": responded_count,
                "response_rate": response_rate,
            },
        }

        cache.set(cache_key, response, timeout=ALL_APPS_CACHE_TTL)

        logger.info(
            "all_employer_applications_fetched",
            user_id=user_id,
            jobs=len(jobs),
            applications=len(applications),
        )

        return response, 200

    except Exception as e:
        logger.error("get_all_employer_applications_failed", error=str(e), exc_info=True)
        return {"error": str(e)}, 500