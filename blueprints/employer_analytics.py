"""
blueprints/employer_analytics.py — Employer analytics dashboard

Routes:
- GET /api/employer/analytics          — period-aware aggregate stats
- GET /api/employer/analytics/export   — full CSV export data
- GET /api/employer/analytics/widgets  — top matches / funnel / activity
"""
from datetime import datetime

from flask import Blueprint, request, g
from sqlalchemy import text

from core.extensions import db
from core.security import require_auth, require_role
from core.logging_config import get_logger
from services.match_score import calculate_match_score_v2

bp = Blueprint("employer_analytics", __name__, url_prefix="/api/employer")
logger = get_logger(__name__)


@bp.route("/analytics", methods=["GET"])
@require_auth
@require_role("employer")
def get_employer_analytics():
    """Employer analytics — period-aware aggregate stats."""
    try:
        user_id = g.user_id

        period_raw = request.args.get("period", "30")
        period = period_raw if period_raw in ("7", "30", "90") else "30"
        days = int(period)

        summary_row = db.session.execute(
            text("""
                SELECT
                    COUNT(DISTINCT j.id) AS total_jobs,
                    COUNT(DISTINCT CASE WHEN COALESCE(j.status, 'active') = 'active'
                                        THEN j.id END) AS active_jobs
                FROM job_market_data j
                WHERE j.posted_by_user_id = :uid
            """),
            {"uid": user_id}
        ).mappings().first()

        total_jobs  = summary_row["total_jobs"]  or 0
        active_jobs = summary_row["active_jobs"] or 0

        app_row = db.session.execute(
            text("""
                SELECT
                    COUNT(a.id) AS total_applicants,
                    COUNT(CASE WHEN a.status IN ('reviewing','interview') THEN 1 END) AS responded,
                    COUNT(CASE WHEN a.status = 'interview' THEN 1 END) AS interviews,
                    COUNT(CASE WHEN a.status = 'rejected'  THEN 1 END) AS rejected
                FROM job_market_data j
                INNER JOIN applications a ON j.id = a.job_id
                WHERE j.posted_by_user_id = :uid
                  AND a.applied_date >= NOW() - (:days || ' days')::interval
            """),
            {"uid": user_id, "days": days}
        ).mappings().first()

        total_applicants = app_row["total_applicants"] or 0
        responded        = app_row["responded"]        or 0
        interviews       = app_row["interviews"]       or 0
        rejected         = app_row["rejected"]         or 0

        response_rate = (
            round((responded / total_applicants) * 100) if total_applicants else 0
        )

        prev_row = db.session.execute(
            text("""
                SELECT COUNT(a.id) AS prev_count
                FROM job_market_data j
                INNER JOIN applications a ON j.id = a.job_id
                WHERE j.posted_by_user_id = :uid
                  AND a.applied_date >= NOW() - ((:days * 2) || ' days')::interval
                  AND a.applied_date <  NOW() - (:days || ' days')::interval
            """),
            {"uid": user_id, "days": days}
        ).mappings().first()

        prev_count = prev_row["prev_count"] or 0
        if prev_count > 0:
            applicants_delta = round(((total_applicants - prev_count) / prev_count) * 100)
        else:
            applicants_delta = 100 if total_applicants > 0 else 0

        timeline_rows = db.session.execute(
            text("""
                SELECT
                    d::date AS date,
                    COALESCE(cnt.c, 0) AS count
                FROM generate_series(
                    (NOW()::date - (:days - 1)::int),
                    NOW()::date,
                    '1 day'
                ) AS d
                LEFT JOIN (
                    SELECT DATE(a.applied_date) AS day, COUNT(*) AS c
                    FROM job_market_data j
                    INNER JOIN applications a ON j.id = a.job_id
                    WHERE j.posted_by_user_id = :uid
                      AND a.applied_date >= NOW() - (:days || ' days')::interval
                    GROUP BY DATE(a.applied_date)
                ) cnt ON cnt.day = d::date
                ORDER BY d
            """),
            {"uid": user_id, "days": days}
        ).mappings().all()

        applicants_by_date = [
            {"date": row["date"].isoformat() if row["date"] else None,
             "count": int(row["count"])}
            for row in timeline_rows
        ]

        status_rows = db.session.execute(
            text("""
                SELECT a.status, COUNT(*) AS count
                FROM job_market_data j
                INNER JOIN applications a ON j.id = a.job_id
                WHERE j.posted_by_user_id = :uid
                  AND a.applied_date >= NOW() - (:days || ' days')::interval
                GROUP BY a.status
            """),
            {"uid": user_id, "days": days}
        ).mappings().all()

        applicants_by_status = [
            {"status": row["status"], "count": int(row["count"])}
            for row in status_rows
        ]

        top_rows = db.session.execute(
            text("""
                SELECT
                    j.id, j.job_title, j.company_name,
                    COALESCE(j.status, 'active') AS status,
                    COUNT(a.id) AS applicant_count
                FROM job_market_data j
                LEFT JOIN applications a
                    ON a.job_id = j.id
                   AND a.applied_date >= NOW() - (:days || ' days')::interval
                WHERE j.posted_by_user_id = :uid
                GROUP BY j.id, j.job_title, j.company_name, j.status
                ORDER BY applicant_count DESC, j.id DESC
                LIMIT 5
            """),
            {"uid": user_id, "days": days}
        ).mappings().all()

        top_jobs = [
            {"id": row["id"],
             "job_title": row["job_title"],
             "company_name": row["company_name"],
             "status": row["status"],
             "applicants": int(row["applicant_count"])}
            for row in top_rows
        ]

        return {
            "period": days,
            "summary": {
                "total_jobs": total_jobs,
                "active_jobs": active_jobs,
                "total_applicants": total_applicants,
                "response_rate": response_rate,
                "applicants_delta": applicants_delta,
                "interviews": interviews,
                "rejected": rejected,
            },
            "applicants_by_date": applicants_by_date,
            "applicants_by_status": applicants_by_status,
            "top_jobs": top_jobs,
        }, 200

    except Exception as e:
        logger.error("get_employer_analytics_failed", error=str(e), exc_info=True)
        return {"error": {"code": "INTERNAL_ERROR", "message": "เกิดข้อผิดพลาด"}}, 500


@bp.route("/analytics/export", methods=["GET"])
@require_auth
@require_role("employer")
def export_employer_analytics():
    """Full export data for CSV — summary + applicants + top jobs"""
    try:
        user_id = g.user_id

        period_raw = request.args.get("period", "30")
        period = period_raw if period_raw in ("7", "30", "90") else "30"
        days = int(period)

        summary_row = db.session.execute(
            text("""
                SELECT
                    COUNT(DISTINCT j.id) AS total_jobs,
                    COUNT(DISTINCT CASE WHEN COALESCE(j.status, 'active') = 'active'
                                        THEN j.id END) AS active_jobs
                FROM job_market_data j
                WHERE j.posted_by_user_id = :uid
            """),
            {"uid": user_id}
        ).mappings().first()

        app_row = db.session.execute(
            text("""
                SELECT
                    COUNT(a.id) AS total_applicants,
                    COUNT(CASE WHEN LOWER(a.status) IN ('reviewing','interview') THEN 1 END) AS responded,
                    COUNT(CASE WHEN LOWER(a.status) = 'interview' THEN 1 END) AS interviews,
                    COUNT(CASE WHEN LOWER(a.status) = 'rejected'  THEN 1 END) AS rejected
                FROM job_market_data j
                INNER JOIN applications a ON j.id = a.job_id
                WHERE j.posted_by_user_id = :uid
                  AND a.applied_date >= NOW() - (:days || ' days')::interval
            """),
            {"uid": user_id, "days": days}
        ).mappings().first()

        total_applicants = app_row["total_applicants"] or 0
        responded        = app_row["responded"]        or 0
        interviews       = app_row["interviews"]       or 0
        rejected         = app_row["rejected"]         or 0

        response_rate = round((responded / total_applicants) * 100) if total_applicants else 0
        interview_rate = round((interviews / total_applicants) * 100) if total_applicants else 0

        applicants_rows = db.session.execute(
            text("""
                SELECT
                    a.id, a.full_name, a.email, a.phone, a.location,
                    a.status, a.applied_date, a.resume_url, a.resume_filename,
                    j.id AS job_id, j.job_title, j.company_name,
                    j.location AS job_location, j.skills_required,
                    j.experience_level, j.industry
                FROM applications a
                INNER JOIN job_market_data j ON j.id = a.job_id
                WHERE j.posted_by_user_id = :uid
                  AND a.applied_date >= NOW() - (:days || ' days')::interval
                ORDER BY a.applied_date DESC
            """),
            {"uid": user_id, "days": days}
        ).mappings().all()

        applicant_ids = [r["id"] for r in applicants_rows]

        user_map = {}
        if applicant_ids:
            uid_rows = db.session.execute(
                text("SELECT a.id AS app_id, a.user_id FROM applications a WHERE a.id = ANY(:ids)"),
                {"ids": applicant_ids}
            ).mappings().all()
            user_map = {r["app_id"]: r["user_id"] for r in uid_rows}

        user_ids = list(set(user_map.values()))

        skills_map = {}
        if user_ids:
            skill_rows = db.session.execute(
                text("SELECT user_id, skill_name FROM user_skills WHERE user_id = ANY(:uids)"),
                {"uids": user_ids}
            ).mappings().all()
            for r in skill_rows:
                skills_map.setdefault(r["user_id"], []).append(r["skill_name"])

        years_map = {}
        if user_ids:
            years_rows = db.session.execute(
                text("SELECT user_id, COALESCE(SUM(EXTRACT(EPOCH FROM (COALESCE(end_date, NOW()) - start_date)) / (365.25 * 24 * 3600)), 0) AS total_years FROM user_experience WHERE user_id = ANY(:uids) GROUP BY user_id"),
                {"uids": user_ids}
            ).mappings().all()
            years_map = {r["user_id"]: float(r["total_years"] or 0) for r in years_rows}

        applicants = []
        for row in applicants_rows:
            try:
                uid = user_map.get(row["id"])
                cand_data = {
                    "skills": skills_map.get(uid, []),
                    "total_years": years_map.get(uid, 0),
                    "industry": "",
                    "location": row["location"] or "",
                }

                job_dict = {
                    "skills_required": row["skills_required"],
                    "experience_level": row["experience_level"],
                    "industry": row["industry"],
                    "location": row["job_location"],
                }

                match = calculate_match_score_v2(job_dict, cand_data)
                match_score = match["overall"]
                matched_skills = ", ".join(match.get("matched_skills", []))
            except Exception as e:
                logger.warning("match_calc_failed", app_id=row["id"], error=str(e))
                match_score = 0
                matched_skills = ""

            applicants.append({
                "id": row["id"],
                "full_name": row["full_name"],
                "email": row["email"],
                "phone": row["phone"] or "",
                "location": row["location"] or "",
                "job_title": row["job_title"],
                "company_name": row["company_name"],
                "status": row["status"],
                "applied_date": row["applied_date"].isoformat() if row["applied_date"] else "",
                "match_score": match_score,
                "matched_skills": matched_skills,
                "resume_url": row["resume_url"] or "",
                "resume_filename": row["resume_filename"] or "",
            })

        top_rows = db.session.execute(
            text("""
                SELECT
                    j.id, j.job_title, j.company_name,
                    COALESCE(j.status, 'active') AS status,
                    COUNT(a.id) AS applicant_count
                FROM job_market_data j
                LEFT JOIN applications a
                    ON a.job_id = j.id
                   AND a.applied_date >= NOW() - (:days || ' days')::interval
                WHERE j.posted_by_user_id = :uid
                GROUP BY j.id, j.job_title, j.company_name, j.status
                ORDER BY applicant_count DESC, j.id DESC
            """),
            {"uid": user_id, "days": days}
        ).mappings().all()

        top_jobs = [
            {
                "id": r["id"],
                "job_title": r["job_title"],
                "company_name": r["company_name"],
                "status": r["status"],
                "applicants": int(r["applicant_count"]),
            }
            for r in top_rows
        ]

        return {
            "period": days,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "summary": {
                "total_jobs": summary_row["total_jobs"] or 0,
                "active_jobs": summary_row["active_jobs"] or 0,
                "total_applicants": total_applicants,
                "response_rate": response_rate,
                "interview_rate": interview_rate,
                "interviews": interviews,
                "rejected": rejected,
            },
            "applicants": applicants,
            "top_jobs": top_jobs,
        }, 200

    except Exception as e:
        logger.error("export_analytics_failed", error=str(e), exc_info=True)
        return {"error": {"code": "INTERNAL_ERROR", "message": str(e)}}, 500


@bp.route("/analytics/widgets", methods=["GET"])
@require_auth
@require_role("employer")
def get_analytics_widgets():
    """Dashboard widgets: top_matches, funnel, recent_activity"""
    try:
        user_id = g.user_id

        period_raw = request.args.get("period", "30")
        period = period_raw if period_raw in ("7", "30", "90") else "30"
        days = int(period)

        funnel_row = db.session.execute(
            text("""
                SELECT
                    COUNT(a.id) AS total,
                    COUNT(CASE WHEN LOWER(a.status) IN ('reviewing','interview') THEN 1 END) AS reviewing,
                    COUNT(CASE WHEN LOWER(a.status) = 'interview' THEN 1 END) AS interview,
                    COUNT(CASE WHEN LOWER(a.status) = 'rejected' THEN 1 END) AS rejected
                FROM job_market_data j
                INNER JOIN applications a ON j.id = a.job_id
                WHERE j.posted_by_user_id = :uid
                  AND a.applied_date >= NOW() - (:days || ' days')::interval
            """),
            {"uid": user_id, "days": days}
        ).mappings().first()

        funnel = [
            {"stage": "Applied",   "count": funnel_row["total"] or 0,     "color": "#38bdf8"},
            {"stage": "Reviewing", "count": funnel_row["reviewing"] or 0, "color": "#f472b6"},
            {"stage": "Interview", "count": funnel_row["interview"] or 0, "color": "#34d399"},
            {"stage": "Rejected",  "count": funnel_row["rejected"] or 0,  "color": "#94a3b8"},
        ]

        top_apps = db.session.execute(
            text("""
                SELECT
                    a.id, a.user_id, a.full_name, a.status, a.applied_date,
                    j.job_title, j.skills_required, j.experience_level,
                    j.industry, j.location AS job_location
                FROM applications a
                INNER JOIN job_market_data j ON j.id = a.job_id
                WHERE j.posted_by_user_id = :uid
                  AND a.applied_date >= NOW() - (:days || ' days')::interval
                ORDER BY a.applied_date DESC
                LIMIT 50
            """),
            {"uid": user_id, "days": days}
        ).mappings().all()

        applicant_ids = [r["id"] for r in top_apps]
        user_map = {}
        if applicant_ids:
            uid_rows = db.session.execute(
                text("SELECT id AS app_id, user_id FROM applications WHERE id = ANY(:ids)"),
                {"ids": applicant_ids}
            ).mappings().all()
            user_map = {r["app_id"]: r["user_id"] for r in uid_rows}

        user_ids = list(set(user_map.values()))
        skills_map = {}
        if user_ids:
            skill_rows = db.session.execute(
                text("SELECT user_id, skill_name FROM user_skills WHERE user_id = ANY(:uids)"),
                {"uids": user_ids}
            ).mappings().all()
            for r in skill_rows:
                skills_map.setdefault(r["user_id"], []).append(r["skill_name"])

        years_map = {}
        if user_ids:
            years_rows = db.session.execute(
                text("""
                    SELECT user_id, COALESCE(SUM(
                        EXTRACT(EPOCH FROM (COALESCE(end_date, NOW()) - start_date)) / (365.25 * 24 * 3600)
                    ), 0) AS total_years
                    FROM user_experience
                    WHERE user_id = ANY(:uids)
                    GROUP BY user_id
                """),
                {"uids": user_ids}
            ).mappings().all()
            years_map = {r["user_id"]: float(r["total_years"] or 0) for r in years_rows}

        scored = []
        for row in top_apps:
            try:
                uid = user_map.get(row["id"])
                cand_data = {
                    "skills": skills_map.get(uid, []),
                    "total_years": years_map.get(uid, 0),
                    "industry": "",
                    "location": "",
                }
                job_dict = {
                    "skills_required": row["skills_required"],
                    "experience_level": row["experience_level"],
                    "industry": row["industry"],
                    "location": row["job_location"],
                }
                match = calculate_match_score_v2(job_dict, cand_data)
                scored.append({
                    "id": row["id"],
                    "full_name": row["full_name"],
                    "job_title": row["job_title"],
                    "status": row["status"],
                    "match_score": match["overall"],
                })
            except Exception as e:
                logger.warning("widget_match_failed", app_id=row["id"], error=str(e))

        top_matches = sorted(scored, key=lambda x: x["match_score"], reverse=True)[:5]

        activity_rows = db.session.execute(
            text("""
                SELECT
                    a.id, a.full_name, a.status, a.applied_date, a.updated_at,
                    j.job_title
                FROM applications a
                INNER JOIN job_market_data j ON j.id = a.job_id
                WHERE j.posted_by_user_id = :uid
                  AND a.applied_date >= NOW() - (:days || ' days')::interval
                ORDER BY COALESCE(a.updated_at, a.applied_date) DESC
                LIMIT 6
            """),
            {"uid": user_id, "days": days}
        ).mappings().all()

        recent_activity = [
            {
                "id": r["id"],
                "full_name": r["full_name"],
                "status": r["status"],
                "job_title": r["job_title"],
                "applied_date": r["applied_date"].isoformat() if r["applied_date"] else None,
                "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
            }
            for r in activity_rows
        ]

        return {
            "period": days,
            "funnel": funnel,
            "top_matches": top_matches,
            "recent_activity": recent_activity,
        }, 200

    except Exception as e:
        logger.error("analytics_widgets_failed", error=str(e), exc_info=True)
        return {"error": {"code": "INTERNAL_ERROR", "message": str(e)}}, 500
