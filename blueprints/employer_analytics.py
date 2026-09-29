"""
blueprints/employer_analytics.py — Employer analytics dashboard

Routes:
- GET /api/employer/analytics          — period-aware aggregate stats
- GET /api/employer/analytics/export   — full CSV export data
- GET /api/employer/analytics/widgets  — top matches / funnel / activity

Performance:
- Cache per (employer_id, period) — TTL 180s (export 300s)
- Uses calculate_match_score_v2_prepared for batch scoring
- Invalidation: cache.clear() on job/applicant mutations
"""
from datetime import datetime

from flask import Blueprint, request, g
from sqlalchemy import text

from core.extensions import db, cache
from core.security import require_auth, require_role
from core.logging_config import get_logger
from services.match_score import (
    prepare_user_data,
    calculate_match_score_v2_prepared,
)

bp = Blueprint("employer_analytics", __name__, url_prefix="/api/employer")
logger = get_logger(__name__)

# ---- Constants ----
ANALYTICS_CACHE_TTL = 180          # 3 min
EXPORT_CACHE_TTL = 300             # 5 min
ALLOWED_PERIODS = ("7", "30", "90")
DEFAULT_PERIOD = "30"


def _normalize_period(raw):
    """Validate period param → int days"""
    period = raw if raw in ALLOWED_PERIODS else DEFAULT_PERIOD
    return period, int(period)


def _compute_total_years_map(user_ids, db_session):
    """
    ดึง total_years ของ users หลายคนใน query เดียว
    Returns: {user_id: years}
    """
    if not user_ids:
        return {}
    rows = db_session.execute(
        text("""
            SELECT
                user_id,
                COALESCE(SUM(
                    EXTRACT(EPOCH FROM (COALESCE(end_date, NOW()) - start_date))
                ), 0) AS total_seconds
            FROM user_experience
            WHERE user_id = ANY(:uids)
            GROUP BY user_id
        """),
        {"uids": user_ids},
    ).mappings().all()
    # Convert seconds → years
    return {r["user_id"]: float(r["total_seconds"] or 0) / (365.25 * 24 * 3600) for r in rows}


def _fetch_skills_map(user_ids, db_session):
    """ดึง skills ของ users หลายคนใน query เดียว"""
    if not user_ids:
        return {}
    rows = db_session.execute(
        text("SELECT user_id, skill_name FROM user_skills WHERE user_id = ANY(:uids)"),
        {"uids": user_ids},
    ).mappings().all()
    skills_map = {}
    for r in rows:
        skills_map.setdefault(r["user_id"], []).append(r["skill_name"])
    return skills_map


def _score_applicants_batch(applicants, db_session):
    """
    ให้คะแนน applicant หลายคนโดยใช้ prepared data
    applicants: list of dict ที่มี keys: id, user_id, skills_required, experience_level, industry, job_location, location
    Returns: list of dict ที่มี match_score + matched_skills (string)
    """
    if not applicants:
        return []

    user_ids = list({a["user_id"] for a in applicants if a.get("user_id")})
    skills_map = _fetch_skills_map(user_ids, db_session)
    years_map = _compute_total_years_map(user_ids, db_session)

    results = []
    for a in applicants:
        try:
            cand_data = {
                "skills": skills_map.get(a["user_id"], []),
                "total_years": years_map.get(a["user_id"], 0),
                "industry": a.get("cand_industry") or "",
                "location": a.get("location") or "",
            }
            prepared = prepare_user_data(cand_data)
            job_dict = {
                "skills_required": a.get("skills_required"),
                "experience_level": a.get("experience_level"),
                "industry": a.get("industry"),
                "location": a.get("job_location"),
            }
            match = calculate_match_score_v2_prepared(job_dict, prepared)
            results.append({
                **a,
                "match_score": match["overall"],
                "matched_skills_str": ", ".join(match.get("matched_skills", [])),
            })
        except Exception as e:
            logger.warning("batch_score_failed", app_id=a.get("id"), error=str(e))
            results.append({
                **a,
                "match_score": 0,
                "matched_skills_str": "",
            })
    return results


# =============================================================================
# /analytics
# =============================================================================

@bp.route("/analytics", methods=["GET"])
@require_auth
@require_role("employer")
def get_employer_analytics():
    """Employer analytics — period-aware aggregate stats."""
    user_id = g.user_id
    period, days = _normalize_period(request.args.get("period", DEFAULT_PERIOD))

    cache_key = f"analytics:emp{user_id}:p{period}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached, 200

    try:
        # 1. Summary: jobs
        summary_row = db.session.execute(
            text("""
                SELECT
                    COUNT(DISTINCT j.id) AS total_jobs,
                    COUNT(DISTINCT CASE WHEN COALESCE(j.status, 'active') = 'active'
                                        THEN j.id END) AS active_jobs
                FROM job_market_data j
                WHERE j.posted_by_user_id = :uid
            """),
            {"uid": user_id},
        ).mappings().first()

        total_jobs = summary_row["total_jobs"] or 0
        active_jobs = summary_row["active_jobs"] or 0

        # 2. Applications stats (current period)
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
            {"uid": user_id, "days": days},
        ).mappings().first()

        total_applicants = app_row["total_applicants"] or 0
        responded = app_row["responded"] or 0
        interviews = app_row["interviews"] or 0
        rejected = app_row["rejected"] or 0
        response_rate = round((responded / total_applicants) * 100) if total_applicants else 0

        # 3. Previous period (for delta)
        prev_row = db.session.execute(
            text("""
                SELECT COUNT(a.id) AS prev_count
                FROM job_market_data j
                INNER JOIN applications a ON j.id = a.job_id
                WHERE j.posted_by_user_id = :uid
                  AND a.applied_date >= NOW() - ((:days * 2) || ' days')::interval
                  AND a.applied_date <  NOW() - (:days || ' days')::interval
            """),
            {"uid": user_id, "days": days},
        ).mappings().first()

        prev_count = prev_row["prev_count"] or 0
        if prev_count > 0:
            applicants_delta = round(((total_applicants - prev_count) / prev_count) * 100)
        else:
            applicants_delta = 100 if total_applicants > 0 else 0

        # 4. Timeline
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
            {"uid": user_id, "days": days},
        ).mappings().all()

        applicants_by_date = [
            {
                "date": row["date"].isoformat() if row["date"] else None,
                "count": int(row["count"]),
            }
            for row in timeline_rows
        ]

        # 5. Status breakdown
        status_rows = db.session.execute(
            text("""
                SELECT a.status, COUNT(*) AS count
                FROM job_market_data j
                INNER JOIN applications a ON j.id = a.job_id
                WHERE j.posted_by_user_id = :uid
                  AND a.applied_date >= NOW() - (:days || ' days')::interval
                GROUP BY a.status
            """),
            {"uid": user_id, "days": days},
        ).mappings().all()

        applicants_by_status = [
            {"status": row["status"], "count": int(row["count"])}
            for row in status_rows
        ]

        # 6. Top jobs
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
            {"uid": user_id, "days": days},
        ).mappings().all()

        top_jobs = [
            {
                "id": row["id"],
                "job_title": row["job_title"],
                "company_name": row["company_name"],
                "status": row["status"],
                "applicants": int(row["applicant_count"]),
            }
            for row in top_rows
        ]

        response = {
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
        }

        cache.set(cache_key, response, timeout=ANALYTICS_CACHE_TTL)
        return response, 200

    except Exception as e:
        logger.error("get_employer_analytics_failed", error=str(e), exc_info=True)
        return {"error": {"code": "INTERNAL_ERROR", "message": "เกิดข้อผิดพลาด"}}, 500


# =============================================================================
# /analytics/export
# =============================================================================

@bp.route("/analytics/export", methods=["GET"])
@require_auth
@require_role("employer")
def export_employer_analytics():
    """Full export data for CSV — summary + applicants + top jobs"""
    user_id = g.user_id
    period, days = _normalize_period(request.args.get("period", DEFAULT_PERIOD))

    cache_key = f"analytics_export:emp{user_id}:p{period}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached, 200

    try:
        # 1. Summary: jobs
        summary_row = db.session.execute(
            text("""
                SELECT
                    COUNT(DISTINCT j.id) AS total_jobs,
                    COUNT(DISTINCT CASE WHEN COALESCE(j.status, 'active') = 'active'
                                        THEN j.id END) AS active_jobs
                FROM job_market_data j
                WHERE j.posted_by_user_id = :uid
            """),
            {"uid": user_id},
        ).mappings().first()

        # 2. App stats
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
            {"uid": user_id, "days": days},
        ).mappings().first()

        total_applicants = app_row["total_applicants"] or 0
        responded = app_row["responded"] or 0
        interviews = app_row["interviews"] or 0
        rejected = app_row["rejected"] or 0
        response_rate = round((responded / total_applicants) * 100) if total_applicants else 0
        interview_rate = round((interviews / total_applicants) * 100) if total_applicants else 0

        # 3. Applicants — combined with user_id in single query (no extra uid query)
        applicants_rows = db.session.execute(
            text("""
                SELECT
                    a.id, a.user_id, a.full_name, a.email, a.phone, a.location,
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
            {"uid": user_id, "days": days},
        ).mappings().all()

        # 4. Batch score (single pass — fetch skills/years in 2 queries)
        applicants_for_scoring = [
            {
                "id": r["id"],
                "user_id": r["user_id"],
                "location": r["location"] or "",
                "cand_industry": "",  # not loaded — falls back to 50 in scorer
                "skills_required": r["skills_required"],
                "experience_level": r["experience_level"],
                "industry": r["industry"],
                "job_location": r["job_location"],
            }
            for r in applicants_rows
        ]
        scored = _score_applicants_batch(applicants_for_scoring, db.session)
        score_map = {s["id"]: s for s in scored}

        # 5. Build applicants payload
        applicants = []
        for row in applicants_rows:
            scored_row = score_map.get(row["id"], {})
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
                "match_score": scored_row.get("match_score", 0),
                "matched_skills": scored_row.get("matched_skills_str", ""),
                "resume_url": row["resume_url"] or "",
                "resume_filename": row["resume_filename"] or "",
            })

        # 6. Top jobs
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
            {"uid": user_id, "days": days},
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

        response = {
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
        }

        cache.set(cache_key, response, timeout=EXPORT_CACHE_TTL)
        return response, 200

    except Exception as e:
        logger.error("export_analytics_failed", error=str(e), exc_info=True)
        return {"error": {"code": "INTERNAL_ERROR", "message": str(e)}}, 500


# =============================================================================
# /analytics/widgets
# =============================================================================

@bp.route("/analytics/widgets", methods=["GET"])
@require_auth
@require_role("employer")
def get_analytics_widgets():
    """Dashboard widgets: top_matches, funnel, recent_activity"""
    user_id = g.user_id
    period, days = _normalize_period(request.args.get("period", DEFAULT_PERIOD))

    cache_key = f"analytics_widgets:emp{user_id}:p{period}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached, 200

    try:
        # 1. Funnel
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
            {"uid": user_id, "days": days},
        ).mappings().first()

        funnel = [
            {"stage": "Applied",   "count": funnel_row["total"] or 0,     "color": "#38bdf8"},
            {"stage": "Reviewing", "count": funnel_row["reviewing"] or 0, "color": "#f472b6"},
            {"stage": "Interview", "count": funnel_row["interview"] or 0, "color": "#34d399"},
            {"stage": "Rejected",  "count": funnel_row["rejected"] or 0,  "color": "#94a3b8"},
        ]

        # 2. Top applications (recent 50)
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
            {"uid": user_id, "days": days},
        ).mappings().all()

        # 3. Batch score (uses _score_applicants_batch)
        apps_for_scoring = [
            {
                "id": r["id"],
                "user_id": r["user_id"],
                "location": "",
                "cand_industry": "",
                "skills_required": r["skills_required"],
                "experience_level": r["experience_level"],
                "industry": r["industry"],
                "job_location": r["job_location"],
            }
            for r in top_apps
        ]
        scored = _score_applicants_batch(apps_for_scoring, db.session)

        # 4. Top 5 by match
        top_matches = sorted(
            [
                {
                    "id": s["id"],
                    "full_name": s["full_name"],
                    "job_title": next((r["job_title"] for r in top_apps if r["id"] == s["id"]), ""),
                    "status": next((r["status"] for r in top_apps if r["id"] == s["id"]), ""),
                    "match_score": s["match_score"],
                }
                for s in scored
            ],
            key=lambda x: x["match_score"],
            reverse=True,
        )[:5]

        # 5. Recent activity
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
            {"uid": user_id, "days": days},
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

        response = {
            "period": days,
            "funnel": funnel,
            "top_matches": top_matches,
            "recent_activity": recent_activity,
        }

        cache.set(cache_key, response, timeout=ANALYTICS_CACHE_TTL)
        return response, 200

    except Exception as e:
        logger.error("analytics_widgets_failed", error=str(e), exc_info=True)
        return {"error": {"code": "INTERNAL_ERROR", "message": str(e)}}, 500