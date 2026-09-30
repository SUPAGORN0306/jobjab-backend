"""
blueprints/jobs.py — Jobs routes

Routes:
- GET /api/jobs
- GET /api/jobs/<int:job_id>
"""
from sqlalchemy import text
from flask import Blueprint, request

from core.extensions import db, cache
from core.logging_config import get_logger
from services.match_score import (
    load_user_data, load_user_data_combined, prepare_user_data,
    calculate_match_score_v2, calculate_match_score_v2_prepared
)
from services.serializers import format_salary, get_company_initial, serialize_row

bp = Blueprint("jobs", __name__, url_prefix="/api")
logger = get_logger(__name__)


@bp.route("/jobs")
def get_jobs():
    try:
        # ═══════════════════════════════════════
        # 1. PARSE PARAMS
        # ═══════════════════════════════════════
        user_id = request.args.get("user_id", type=int)

        page = max(1, request.args.get("page", type=int, default=1))
        limit = request.args.get("limit", type=int, default=50)
        limit = max(1, min(limit, 100))

        if "offset" in request.args:
            offset = max(0, request.args.get("offset", type=int, default=0))
            page = (offset // limit) + 1
        else:
            offset = (page - 1) * limit

        # Filters
        q = (request.args.get("q") or "").strip()
        position = (request.args.get("position") or "").strip()
        level = (request.args.get("level") or "").strip()
        type_ = (request.args.get("type") or "").strip()
        industry = (request.args.get("industry") or "").strip()
        salary_min = request.args.get("salary_min", type=int)
        salary_max = request.args.get("salary_max", type=int)
        sort = (request.args.get("sort") or "newest").strip()

        # ═══════════════════════════════════════
        # 2. CACHE KEY
        # ═══════════════════════════════════════
        cache_key = (
            f"jobs:u{user_id or 0}"
            f":q{q}"
            f":pos{position}"
            f":l{level}"
            f":t{type_}"
            f":i{industry}"
            f":s{salary_min or 0}-{salary_max or 0}"
            f":sort{sort}"
            f":p{page}:lim{limit}"
        )
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        # ═══════════════════════════════════════
        # 3. WHERE CLAUSE
        # ═══════════════════════════════════════
        where_parts = []
        params = {}

        if q:
            where_parts.append("""(
                j.job_title ILIKE :q
                OR j.company_name ILIKE :q
                OR j.skills_required ILIKE :q
            )""")
            params["q"] = f"%{q}%"

        if position and position != "all":
            where_parts.append("j.job_title = :position")
            params["position"] = position

        if level and level != "all":
            where_parts.append("j.experience_level = :level")
            params["level"] = level

        if type_ and type_ != "all":
            where_parts.append("j.employment_type = :type")
            params["type"] = type_

        if industry and industry != "all":
            where_parts.append("j.industry = :industry")
            params["industry"] = industry

        if salary_min and salary_min > 0:
            where_parts.append("j.salary_max >= :salary_min")
            params["salary_min"] = salary_min

        if salary_max and salary_max > 0 and salary_max < 1000000:
            where_parts.append("j.salary_min <= :salary_max")
            params["salary_max"] = salary_max

        where_sql = " AND ".join(where_parts) if where_parts else "1=1"

        # ═══════════════════════════════════════
        # 4. ORDER BY
        # ═══════════════════════════════════════
        order_by = {
            "newest": "j.posted_date DESC NULLS LAST",
            "salary_high": "j.salary_max DESC NULLS LAST",
            "salary_low": "j.salary_min ASC NULLS LAST",
            "match": "j.id",
        }.get(sort, "j.posted_date DESC NULLS LAST")

        # ═══════════════════════════════════════
        # 5. COUNT TOTAL (filtered)
        # ═══════════════════════════════════════
        total = db.session.execute(
            text(f"SELECT COUNT(*) FROM job_market_data j WHERE {where_sql}"),
            params
        ).scalar() or 0

        # ═══════════════════════════════════════
        # 6. FETCH PAGE
        # ═══════════════════════════════════════
        query_params = {**params, "limit": limit, "offset": offset}

        result = db.session.execute(text(f"""
            SELECT
                j.id, j.company_name, j.industry, j.job_title,
                j.skills_required, j.experience_level, j.employment_type,
                j.location, j.posted_date, j.company_size, j.tools_preferred,
                j.salary_min, j.salary_max,
                COUNT(a.id) AS applicant_count
            FROM job_market_data j
            LEFT JOIN applications a ON j.id = a.job_id
            WHERE {where_sql}
            GROUP BY j.id
            ORDER BY {order_by}
            LIMIT :limit OFFSET :offset
        """), query_params)
        rows = result.mappings().all()

        # ═══════════════════════════════════════
        # 7. MATCH SCORE
        # ═══════════════════════════════════════
        user_data = None
        if user_id:
            user_data = prepare_user_data(
                load_user_data_combined(user_id, db.session)
            )

        jobs_list = []
        for row in rows:
            job_dict = dict(row)
            count = job_dict.get("applicant_count", 0)

            if user_id and user_data:
                match = calculate_match_score_v2_prepared(job_dict, user_data)
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

        # ═══════════════════════════════════════
        # 8. GLOBAL STATS
        # ═══════════════════════════════════════
        total_companies = db.session.execute(
            text("SELECT COUNT(DISTINCT company_name) FROM job_market_data")
        ).scalar() or 0

        total_applicants = db.session.execute(
            text("SELECT COUNT(*) FROM applications")
        ).scalar() or 0

        total_all_jobs = db.session.execute(
            text("SELECT COUNT(*) FROM job_market_data")
        ).scalar() or 0

        # ═══════════════════════════════════════
        # 9. RESPONSE
        # ═══════════════════════════════════════
        total_pages = (total + limit - 1) // limit if total > 0 else 0

        response = {
            "jobs": jobs_list,
            "pagination": {
                "total": total,
                "page": page,
                "limit": limit,
                "offset": offset,
                "total_pages": total_pages,
                "has_more": (offset + len(jobs_list)) < total,
                "total_all_jobs": total_all_jobs,
                "total_companies": total_companies,
                "total_applicants": total_applicants,
            },
        }

        cache.set(cache_key, response, timeout=60)

        return response

    except Exception as e:
        logger.error("get_jobs_failed", error=str(e), exc_info=True)
        return {"error": {"code": "INTERNAL_ERROR", "message": "ไม่สามารถโหลดงานได้"}}, 500


@bp.route("/_debug/cache")
def debug_cache():
    """Debug endpoint — ดู cache state"""
    from core.extensions import cache
    keys = []
    if hasattr(cache.cache, '_cache'):
        keys = list(cache.cache._cache.keys())
    return {
        "cache_class": cache.cache.__class__.__name__,
        "keys_count": len(keys),
        "keys": keys[:20],
    }


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
            # ⚠️ Bug fix: ต้อง load_user_data_combined ก่อน prepare
            user_data = prepare_user_data(load_user_data_combined(user_id, db.session))
            match = calculate_match_score_v2_prepared(job_dict, user_data)
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