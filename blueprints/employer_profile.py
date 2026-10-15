"""
blueprints/employer_profile.py — Employer profile

Routes:
- GET /api/employer/profile
- PUT /api/employer/profile
"""
from flask import Blueprint, g, request
from sqlalchemy import text

from core.extensions import db, cache, invalidate_jobs_cache
from core.security import require_auth, require_role, sanitize_text
from core.logging_config import get_logger
from services.supabase import sb_delete, extract_supabase_path

bp = Blueprint("employer_profile", __name__, url_prefix="/api/employer")
logger = get_logger(__name__)


def _clear_employer_caches(user_id):
    """Clear all caches related to employer"""
    invalidate_jobs_cache()
    cache.delete(f"employer_jobs:emp{user_id}")
    for period in ("7", "30", "90"):
        cache.delete(f"analytics:emp{user_id}:p{period}")
        cache.delete(f"analytics_export:emp{user_id}:p{period}")
        cache.delete(f"analytics_widgets:emp{user_id}:p{period}")
    for limit in (100, 500, 1000):
        for offset in (0, 100, 500):
            cache.delete(f"emp_all_apps:emp{user_id}:l{limit}:o{offset}")


@bp.route("/profile", methods=["GET"])
@require_auth
@require_role("employer")
def get_employer_profile():
    try:
        user_id = g.user_id
        result = db.session.execute(
            text("""
                SELECT ep.company_name, ep.industry, ep.company_logo,
                       u.email, u.full_name, u.phone, u.location, u.bio
                FROM employer_profiles ep
                JOIN users u ON u.id = ep.user_id
                WHERE ep.user_id = :uid
            """),
            {"uid": user_id}
        ).mappings().first()
        if not result:
            return {"error": {"code": "NOT_FOUND",
                    "message": "Employer profile not found"}}, 404
        return {"profile": dict(result)}, 200
    except Exception as e:
        logger.error("get_employer_profile_failed", error=str(e), exc_info=True)
        return {"error": {"code": "INTERNAL_ERROR",
                "message": "Failed to load profile"}}, 500


@bp.route("/profile", methods=["PUT"])
@require_auth
@require_role("employer")
def update_employer_profile():
    try:
        user_id = g.user_id
        data = request.get_json() or {}

        company_name = sanitize_text(data.get("company_name") or "", max_length=200)
        industry = sanitize_text(data.get("industry") or "", max_length=100)
        company_logo = data.get("company_logo")

        if not company_name:
            return {"error": {"code": "MISSING_COMPANY_NAME",
                    "message": "Company name is required"}}, 400

        # Delete old logo
        if company_logo is not None:
            old_logo = db.session.execute(
                text("SELECT company_logo FROM employer_profiles WHERE user_id = :uid"),
                {"uid": user_id}
            ).scalar()
            if old_logo and "supabase.co" in old_logo and old_logo != company_logo:
                old_path = extract_supabase_path(old_logo, "company-logos")
                if old_path:
                    try:
                        sb_delete("company-logos", old_path)
                        logger.info("old_logo_deleted", user_id=user_id, path=old_path)
                    except Exception as del_err:
                        logger.warning("delete_old_logo_failed", error=str(del_err))

        # UPSERT
        exists = db.session.execute(
            text("SELECT id FROM employer_profiles WHERE user_id = :uid"),
            {"uid": user_id}
        ).first()

        if exists:
            update_fields = ["company_name = :name", "updated_at = NOW()"]
            params = {"uid": user_id, "name": company_name}
            if industry:
                update_fields.append("industry = :industry")
                params["industry"] = industry
            if company_logo is not None:
                update_fields.append("company_logo = :logo")
                params["logo"] = company_logo or None

            db.session.execute(
                text(f"""
                    UPDATE employer_profiles
                    SET {", ".join(update_fields)}
                    WHERE user_id = :uid
                """),
                params
            )
        else:
            db.session.execute(
                text("""
                    INSERT INTO employer_profiles
                        (user_id, company_name, industry, company_logo, created_at, updated_at)
                    VALUES (:uid, :name, :industry, :logo, NOW(), NOW())
                """),
                {"uid": user_id, "name": company_name,
                 "industry": industry or None, "logo": company_logo or None}
            )

        # Sync job_market_data
        sync_fields = ["company_name = :name"]
        sync_params = {"uid": user_id, "name": company_name}
        if industry:
            sync_fields.append("industry = :industry")
            sync_params["industry"] = industry

        db.session.execute(
            text(f"""
                UPDATE job_market_data
                SET {", ".join(sync_fields)}
                WHERE posted_by_user_id = :uid
            """),
            sync_params
        )

        db.session.commit()

        _clear_employer_caches(user_id)

        logger.info("employer_profile_updated", user_id=user_id,
                    company_name=company_name, industry=industry,
                    has_logo=bool(company_logo))

        return {
            "status": "success",
            "message": "Employer profile updated",
            "profile": {
                "company_name": company_name,
                "industry": industry,
                "company_logo": company_logo,
            }
        }, 200

    except Exception as e:
        db.session.rollback()
        logger.error("update_employer_profile_failed", error=str(e), exc_info=True)
        return {"error": {"code": "INTERNAL_ERROR",
                "message": "Failed to update profile"}}, 500