"""
blueprints/employer_jobs.py — Employer job management

Routes:
- GET    /api/employer/jobs
- POST   /api/employer/jobs
- PUT    /api/employer/jobs/<id>
- DELETE /api/employer/jobs/<id>
- PATCH  /api/employer/jobs/<id>/status
- GET    /api/employer/jobs/<id>/applications (paginated)

Auto-healing policy:
- salary: int ≥ 0, max ≥ min
- float → FLOOR
- negative → treat as None → fill median
- None/0/"" → fill median ของ column ตัวเอง
- categorical: fill mode
- job_title: no-only-digits, has-letter
"""
import math
import re
from datetime import datetime, timezone

from flask import Blueprint, request, g
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from core.extensions import db, cache, invalidate_jobs_cache
from core.security import require_auth, require_role, sanitize_text
from core.logging_config import get_logger

bp = Blueprint("employer_jobs", __name__, url_prefix="/api/employer")
logger = get_logger(__name__)

# ============================================================
# CONSTANTS
# ============================================================
ALLOWED_EMPLOYMENT_TYPES = {"Full-time", "Part-time", "Contract", "Internship"}
ALLOWED_EXPERIENCE_LEVELS = {"Entry", "Mid", "Senior"}
ALLOWED_COMPANY_SIZES = {"Small", "Mid", "Large"}
ALLOWED_VISA_SPONSORSHIP = {"Yes", "No"}

JOBS_LIST_TTL = 120
APPLICANTS_TTL = 60

JOB_TITLE_MIN_LEN = 2
JOB_TITLE_MAX_LEN = 100
_JOB_TITLE_ALLOWED_RE = re.compile(r"^[\w\s\.\,\-\/&\(\)\+]+$", re.UNICODE)

SALARY_HARD_MAX = 10_000_000_000


# ═══════════════════════════════════════════════════════════════════
# Helpers — Parse / Validate
# ═══════════════════════════════════════════════════════════════════
def _parse_expires_at(raw):
    """Return (datetime|None, error_str|None)"""
    if not raw:
        return None, None
    if isinstance(raw, datetime):
        # Ensure timezone-aware
        if raw.tzinfo is None:
            raw = raw.replace(tzinfo=timezone.utc)
        return raw, None
    try:
        cleaned = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt, None
    except (ValueError, TypeError):
        return None, "Invalid expires_at format (expected ISO 8601)"
    

def _validate_job_title(raw: str):
    """Return (cleaned_title, error_message|None)"""
    if not raw:
        return None, "Job title is required"

    title = raw.strip()

    if len(title) < JOB_TITLE_MIN_LEN:
        return None, f"Job title must be at least {JOB_TITLE_MIN_LEN} characters"
    if len(title) > JOB_TITLE_MAX_LEN:
        return None, f"Job title must be at most {JOB_TITLE_MAX_LEN} characters"
    if title.isdigit():
        return None, "Job title cannot be only numbers"
    if not re.search(r"[A-Za-zก-๙]", title):
        return None, "Job title must contain at least one letter"
    if not _JOB_TITLE_ALLOWED_RE.match(title):
        return None, "Job title contains invalid characters"

    return re.sub(r"\s+", " ", title), None


def _parse_salary_strict(raw, field_name="salary"):
    """
    Strict parse (API side) — reject float, reject negative.
    Return (value|None, error|None)

    - None / "" / 0 → (None, None)  [→ auto-fill median later]
    - float (with decimals) → error
    - int < 0 → error
    - normal int → (value, None)
    """
    if raw in (None, "", 0, "0"):
        return None, None

    try:
        if isinstance(raw, float):
            if not raw.is_integer():
                return None, f"{field_name} must be an integer (no decimals)"
            val = int(raw)
        else:
            s = str(raw).strip()
            if "." in s:
                return None, f"{field_name} must be an integer (no decimals)"
            val = int(s)
    except (ValueError, TypeError):
        return None, f"{field_name} must be an integer"

    if val < 0:
        return None, f"{field_name} cannot be negative"

    if val > SALARY_HARD_MAX:
        return None, f"{field_name} exceeds allowed range"

    return val, None


def _parse_team_size(raw):
    """
    Parse team_size → int|None
    - None / "" / 0 / "0" → None
    - non-numeric → None
    - negative / > 100000 → None
    - valid int → int
    """
    if raw in (None, "", 0, "0"):
        return None
    try:
        val = int(raw)
    except (ValueError, TypeError):
        return None
    if val < 0 or val > 100000:
        return None
    return val


# ═══════════════════════════════════════════════════════════════════
# Helpers — Stats
# ═══════════════════════════════════════════════════════════════════
def _get_median_salary(db_session, column):
    """Median of salary_min or salary_max — return int (FLOOR)"""
    if column not in ("salary_min", "salary_max"):
        raise ValueError("invalid column")
    row = db_session.execute(text(f"""
        SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY {column})
        FROM job_market_data
        WHERE {column} IS NOT NULL
          AND {column} >= 0
          AND (status = 'active' OR status IS NULL)
    """)).scalar()
    if row is None:
        return 0
    return int(math.floor(float(row)))   # FLOOR


def _get_mode(db_session, column, allowed: set, fallback: str):
    """Mode of categorical column"""
    if column not in ("employment_type", "experience_level",
                      "company_size", "visa_sponsorship"):
        raise ValueError("invalid column")
    row = db_session.execute(
        text(f"""
            SELECT {column}, COUNT(*) AS c
            FROM job_market_data
            WHERE {column} IS NOT NULL
              AND {column} = ANY(:allowed)
            GROUP BY {column}
            ORDER BY c DESC, {column} ASC
            LIMIT 1
        """),
        {"allowed": list(allowed)}
    ).first()
    return row[0] if row else fallback


# ═══════════════════════════════════════════════════════════════════
# Helpers — Auto-healing
# ═══════════════════════════════════════════════════════════════════
def _sanitize_salary_pair(salary_min, salary_max, db_session):
    """
    Auto-heal salary pair (policy B):
      1. Negative → treat as None
      2. None → fill median of own column
      3. FLOOR decimals (safety)
      4. min > max → swap
      5. Safety → max ≥ min
    """
    # 1. Negative → None
    if salary_min is not None and salary_min < 0:
        logger.warning("salary_min_negative_treated_as_none", original=salary_min)
        salary_min = None
    if salary_max is not None and salary_max < 0:
        logger.warning("salary_max_negative_treated_as_none", original=salary_max)
        salary_max = None

    # 2. None → fill median
    if salary_min is None:
        salary_min = _get_median_salary(db_session, "salary_min")
        logger.info("salary_min_filled_median", filled=salary_min)
    if salary_max is None:
        salary_max = _get_median_salary(db_session, "salary_max")
        logger.info("salary_max_filled_median", filled=salary_max)

    # 3. FLOOR
    salary_min = int(math.floor(float(salary_min)))
    salary_max = int(math.floor(float(salary_max)))

    # 4. swap if min > max
    if salary_min > salary_max:
        logger.warning("salary_swapped",
                       original_min=salary_min, original_max=salary_max)
        salary_min, salary_max = salary_max, salary_min

    # 5. Safety
    if salary_max < salary_min:
        salary_max = salary_min

    return salary_min, salary_max


def _fill_job_defaults(db_session, payload: dict) -> dict:
    """Fill median (salary) / mode (categorical) + auto-heal"""
    salary_min, salary_max = _sanitize_salary_pair(
        payload.get("salary_min"),
        payload.get("salary_max"),
        db_session,
    )
    payload["salary_min"] = salary_min
    payload["salary_max"] = salary_max

    if not payload.get("employment_type"):
        payload["employment_type"] = _get_mode(
            db_session, "employment_type", ALLOWED_EMPLOYMENT_TYPES, "Full-time"
        )
    if not payload.get("experience_level"):
        payload["experience_level"] = _get_mode(
            db_session, "experience_level", ALLOWED_EXPERIENCE_LEVELS, "Mid"
        )
    if not payload.get("company_size"):
        payload["company_size"] = _get_mode(
            db_session, "company_size", ALLOWED_COMPANY_SIZES, "Mid"
        )
    if not payload.get("visa_sponsorship"):
        payload["visa_sponsorship"] = _get_mode(
            db_session, "visa_sponsorship", ALLOWED_VISA_SPONSORSHIP, "No"
        )

    return payload


# ═══════════════════════════════════════════════════════════════════
# GET /jobs
# ═══════════════════════════════════════════════════════════════════
@bp.route("/jobs", methods=["GET"])
@require_auth
@require_role("employer")
def get_employer_jobs():
    user_id = g.user_id
    cache_key = f"employer_jobs:emp{user_id}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached, 200

    try:
        result = db.session.execute(
            text("""
                SELECT
                    j.id, j.job_title, j.company_name, j.location,
                    j.employment_type, j.experience_level,
                    j.salary_min, j.salary_max, j.posted_date, j.expires_at,
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
                "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
                "applicant_count": row["applicant_count"],
                "status": status_raw.capitalize() if status_raw else "Active",
                "status_key": status_raw,
            })

        response = {"jobs": jobs}
        cache.set(cache_key, response, timeout=JOBS_LIST_TTL)
        return response, 200

    except Exception as e:
        logger.error("get_employer_jobs_failed", error=str(e), exc_info=True)
        return {"error": {"code": "INTERNAL_ERROR", "message": "Failed to load jobs"}}, 500


# ═══════════════════════════════════════════════════════════════════
# POST /jobs
# ═══════════════════════════════════════════════════════════════════
@bp.route("/jobs", methods=["POST"])
@require_auth
@require_role("employer")
def create_employer_job():
    try:
        data = request.json
        user_id = g.user_id

        emp_profile = db.session.execute(
            text("SELECT company_name, industry FROM employer_profiles WHERE user_id = :uid"),
            {"uid": user_id}
        ).mappings().first()

        if not emp_profile or not emp_profile.get("company_name"):
            return {"error": {"code": "NO_COMPANY_PROFILE",
                    "message": "Please complete your company profile before posting a job"}}, 400

        company_name = emp_profile["company_name"]
        industry_from_profile = emp_profile["industry"]

        # Sanitize
        job_title = sanitize_text(data.get("job_title") or "", max_length=JOB_TITLE_MAX_LEN)
        location = sanitize_text(data.get("location") or "", max_length=200)
        skills_required = sanitize_text(data.get("skills_required") or "", max_length=1000)
        company_size = sanitize_text(data.get("company_size") or "", max_length=50)
        team_size = _parse_team_size(data.get("team_size"))
        visa_sponsorship = (data.get("visa_sponsorship") or "").strip()
        about_role = sanitize_text(data.get("about_role") or "", max_length=5000)
        responsibilities = sanitize_text(data.get("responsibilities") or "", max_length=5000)
        requirements = sanitize_text(data.get("requirements") or "", max_length=5000)

        job_title, title_err = _validate_job_title(job_title)
        if title_err:
            return {"error": {"code": "INVALID_JOB_TITLE", "message": title_err}}, 400

        employment_type = (data.get("employment_type") or "").strip()
        if employment_type and employment_type not in ALLOWED_EMPLOYMENT_TYPES:
            return {"error": {"code": "INVALID_EMPLOYMENT_TYPE",
                    "message": f"Must be one of: {', '.join(sorted(ALLOWED_EMPLOYMENT_TYPES))}"}}, 400

        experience_level = (data.get("experience_level") or "").strip()
        if experience_level and experience_level not in ALLOWED_EXPERIENCE_LEVELS:
            return {"error": {"code": "INVALID_EXPERIENCE_LEVEL",
                    "message": f"Must be one of: {', '.join(sorted(ALLOWED_EXPERIENCE_LEVELS))}"}}, 400

        if company_size and company_size not in ALLOWED_COMPANY_SIZES:
            return {"error": {"code": "INVALID_COMPANY_SIZE",
                    "message": f"Must be one of: {', '.join(sorted(ALLOWED_COMPANY_SIZES))}"}}, 400

        if visa_sponsorship and visa_sponsorship not in ALLOWED_VISA_SPONSORSHIP:
            return {"error": {"code": "INVALID_VISA_SPONSORSHIP",
                    "message": "Must be Yes or No"}}, 400

        # Parse salary — strict
        salary_min, err = _parse_salary_strict(data.get("salary_min"), "salary_min")
        if err:
            return {"error": {"code": "INVALID_SALARY", "message": err}}, 400

        salary_max, err = _parse_salary_strict(data.get("salary_max"), "salary_max")
        if err:
            return {"error": {"code": "INVALID_SALARY", "message": err}}, 400

        if salary_min is not None and salary_max is not None and salary_max < salary_min:
            return {"error": {"code": "INVALID_SALARY",
                    "message": "Maximum salary must be >= minimum salary"}}, 400

        # Auto-heal + fill median/mode
        payload = _fill_job_defaults(db.session, {
            "salary_min": salary_min,
            "salary_max": salary_max,
            "employment_type": employment_type or None,
            "experience_level": experience_level or None,
            "company_size": company_size or None,
            "visa_sponsorship": visa_sponsorship or None,
        })

        salary_min = payload["salary_min"]
        salary_max = payload["salary_max"]
        employment_type = payload["employment_type"]
        experience_level = payload["experience_level"]
        company_size = payload["company_size"]
        visa_sponsorship = payload["visa_sponsorship"]

        expires_at, err = _parse_expires_at(data.get("expires_at"))
        if err:
            return {"error": {"code": "INVALID_EXPIRES_AT", "message": err}}, 400

        # INSERT — int only
        result = db.session.execute(
            text("""
                INSERT INTO job_market_data (
                    job_title, company_name, location,
                    employment_type, experience_level,
                    salary_min, salary_max, skills_required,
                    industry, company_size, team_size, visa_sponsorship,
                    about_role, responsibilities, requirements,
                    posted_by_user_id, posted_date, expires_at, status
                ) VALUES (
                    :job_title, :company_name, :location,
                    :employment_type, :experience_level,
                    :salary_min, :salary_max, :skills_required,
                    :industry, :company_size, :team_size, :visa_sponsorship,
                    :about_role, :responsibilities, :requirements,
                    :user_id, NOW(),
                    COALESCE(:expires_at, NOW() + INTERVAL '60 days'),
                    'active'
                )
                RETURNING id
            """),
            {
                "job_title": job_title,
                "company_name": company_name,
                "location": location or None,
                "employment_type": employment_type,
                "experience_level": experience_level,
                "salary_min": salary_min,
                "salary_max": salary_max,
                "skills_required": skills_required or None,
                "industry": industry_from_profile or None,
                "company_size": company_size or None,
                "team_size": team_size,
                "visa_sponsorship": visa_sponsorship,
                "about_role": about_role or None,
                "responsibilities": responsibilities or None,
                "requirements": requirements or None,
                "user_id": user_id,
                "expires_at": expires_at,
            }
        )
        job_id = result.scalar()
        db.session.commit()

        invalidate_jobs_cache()
        cache.delete(f"employer_jobs:emp{user_id}")

        logger.info("job_created", user_id=user_id, job_id=job_id,
                    job_title=job_title, salary_min=salary_min, salary_max=salary_max)
        return {"status": "success", "message": "Job posted successfully", "job_id": job_id}, 201

    except IntegrityError as e:
        db.session.rollback()
        logger.warning("create_job_integrity_error", error=str(e))
        return {"error": {"code": "INTEGRITY_ERROR",
                "message": "Invalid data. Please check your input."}}, 409
    except Exception as e:
        db.session.rollback()
        logger.error("create_job_failed", error=str(e), exc_info=True)
        return {"error": {"code": "INTERNAL_ERROR",
                "message": "Something went wrong"}}, 500


# ═══════════════════════════════════════════════════════════════════
# PUT /jobs/<id>
# ═══════════════════════════════════════════════════════════════════
@bp.route("/jobs/<int:job_id>", methods=["PUT"])
@require_auth
@require_role("employer")
def update_employer_job(job_id):
    try:
        user_id = g.user_id

        owner_check = db.session.execute(
            text("SELECT posted_by_user_id FROM job_market_data WHERE id = :jid"),
            {"jid": job_id}
        ).first()
        if not owner_check:
            return {"error": {"code": "NOT_FOUND", "message": "Job not found"}}, 404
        if owner_check[0] != user_id:
            return {"error": {"code": "FORBIDDEN", "message": "Forbidden"}}, 403

        emp_profile = db.session.execute(
            text("SELECT company_name, industry FROM employer_profiles WHERE user_id = :uid"),
            {"uid": user_id}
        ).mappings().first()
        if not emp_profile or not emp_profile.get("company_name"):
            return {"error": {"code": "NO_COMPANY_PROFILE",
                    "message": "Please complete your company profile first"}}, 400

        company_name = emp_profile["company_name"]
        industry_from_profile = emp_profile["industry"]

        data = request.json
        if not data:
            return {"error": {"code": "NO_DATA", "message": "No data provided"}}, 400

        job_title = sanitize_text(data.get("job_title") or "", max_length=JOB_TITLE_MAX_LEN)
        location = sanitize_text(data.get("location") or "", max_length=200)
        skills_required = sanitize_text(data.get("skills_required") or "", max_length=1000)
        company_size = sanitize_text(data.get("company_size") or "", max_length=50)
        team_size = _parse_team_size(data.get("team_size"))
        visa_sponsorship = (data.get("visa_sponsorship") or "").strip()
        about_role = sanitize_text(data.get("about_role") or "", max_length=5000)
        responsibilities = sanitize_text(data.get("responsibilities") or "", max_length=5000)
        requirements = sanitize_text(data.get("requirements") or "", max_length=5000)

        job_title, title_err = _validate_job_title(job_title)
        if title_err:
            return {"error": {"code": "INVALID_JOB_TITLE", "message": title_err}}, 400

        employment_type = (data.get("employment_type") or "").strip()
        if employment_type and employment_type not in ALLOWED_EMPLOYMENT_TYPES:
            return {"error": {"code": "INVALID_EMPLOYMENT_TYPE",
                    "message": f"Must be one of: {', '.join(sorted(ALLOWED_EMPLOYMENT_TYPES))}"}}, 400

        experience_level = (data.get("experience_level") or "").strip()
        if experience_level and experience_level not in ALLOWED_EXPERIENCE_LEVELS:
            return {"error": {"code": "INVALID_EXPERIENCE_LEVEL",
                    "message": f"Must be one of: {', '.join(sorted(ALLOWED_EXPERIENCE_LEVELS))}"}}, 400

        if company_size and company_size not in ALLOWED_COMPANY_SIZES:
            return {"error": {"code": "INVALID_COMPANY_SIZE",
                    "message": f"Must be one of: {', '.join(sorted(ALLOWED_COMPANY_SIZES))}"}}, 400

        if visa_sponsorship and visa_sponsorship not in ALLOWED_VISA_SPONSORSHIP:
            return {"error": {"code": "INVALID_VISA_SPONSORSHIP",
                    "message": "Must be Yes or No"}}, 400

        salary_min, err = _parse_salary_strict(data.get("salary_min"), "salary_min")
        if err:
            return {"error": {"code": "INVALID_SALARY", "message": err}}, 400

        salary_max, err = _parse_salary_strict(data.get("salary_max"), "salary_max")
        if err:
            return {"error": {"code": "INVALID_SALARY", "message": err}}, 400

        if salary_min is not None and salary_max is not None and salary_max < salary_min:
            return {"error": {"code": "INVALID_SALARY",
                    "message": "Maximum salary must be >= minimum salary"}}, 400

        payload = _fill_job_defaults(db.session, {
            "salary_min": salary_min,
            "salary_max": salary_max,
            "employment_type": employment_type or None,
            "experience_level": experience_level or None,
            "company_size": company_size or None,
            "visa_sponsorship": visa_sponsorship or None,
        })

        salary_min = payload["salary_min"]
        salary_max = payload["salary_max"]
        employment_type = payload["employment_type"]
        experience_level = payload["experience_level"]
        company_size = payload["company_size"]
        visa_sponsorship = payload["visa_sponsorship"]

        expires_at, err = _parse_expires_at(data.get("expires_at"))
        if err:
            return {"error": {"code": "INVALID_EXPIRES_AT", "message": err}}, 400

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
                    industry = :industry,
                    company_size = :company_size,
                    team_size = :team_size,
                    visa_sponsorship = :visa_sponsorship,
                    about_role = :about_role,
                    responsibilities = :responsibilities,
                    requirements = :requirements,
                    expires_at = COALESCE(:expires_at, expires_at)
                WHERE id = :jid AND posted_by_user_id = :uid
            """),
            {
                "jid": job_id, "uid": user_id,
                "job_title": job_title,
                "company_name": company_name,
                "location": location or None,
                "employment_type": employment_type,
                "experience_level": experience_level,
                "salary_min": salary_min,
                "salary_max": salary_max,
                "skills_required": skills_required or None,
                "industry": industry_from_profile or None,
                "company_size": company_size or None,
                "team_size": team_size or None,
                "visa_sponsorship": visa_sponsorship,
                "about_role": about_role or None,
                "responsibilities": responsibilities or None,
                "requirements": requirements or None,
                "expires_at": expires_at,
            }
        )
        db.session.commit()

        invalidate_jobs_cache()
        cache.delete(f"employer_jobs:emp{user_id}")

        logger.info("job_updated", user_id=user_id, job_id=job_id)
        return {"status": "success", "message": "Job updated successfully", "job_id": job_id}, 200

    except Exception as e:
        db.session.rollback()
        logger.error("update_job_failed", error=str(e), exc_info=True)
        return {"error": {"code": "INTERNAL_ERROR",
                "message": "Something went wrong"}}, 500


# ═══════════════════════════════════════════════════════════════════
# DELETE /jobs/<id>
# ═══════════════════════════════════════════════════════════════════
@bp.route("/jobs/<int:job_id>", methods=["DELETE"])
@require_auth
@require_role("employer")
def delete_employer_job(job_id):
    try:
        user_id = g.user_id

        owner_check = db.session.execute(
            text("SELECT posted_by_user_id, job_title FROM job_market_data WHERE id = :jid"),
            {"jid": job_id}
        ).first()
        if not owner_check:
            return {"error": {"code": "NOT_FOUND", "message": "Job not found"}}, 404
        if owner_check[0] != user_id:
            return {"error": {"code": "FORBIDDEN", "message": "Forbidden"}}, 403

        applicant_count = db.session.execute(
            text("SELECT COUNT(*) FROM applications WHERE job_id = :jid"),
            {"jid": job_id}
        ).scalar() or 0

        if applicant_count > 0:
            return {"error": {"code": "HAS_APPLICANTS",
                    "message": f"Cannot delete — this job has {applicant_count} applicant(s). Pause it instead.",
                    "applicant_count": applicant_count}}, 409

        db.session.execute(
            text("DELETE FROM job_market_data WHERE id = :jid AND posted_by_user_id = :uid"),
            {"jid": job_id, "uid": user_id}
        )
        db.session.commit()

        invalidate_jobs_cache()
        cache.delete(f"employer_jobs:emp{user_id}")

        logger.info("job_deleted", user_id=user_id, job_id=job_id, job_title=owner_check[1])
        return {"status": "success", "message": "Job deleted successfully", "job_id": job_id}, 200

    except Exception as e:
        db.session.rollback()
        logger.error("delete_job_failed", error=str(e), exc_info=True)
        return {"error": {"code": "INTERNAL_ERROR",
                "message": "Something went wrong"}}, 500


# ═══════════════════════════════════════════════════════════════════
# PATCH /jobs/<id>/status
# ═══════════════════════════════════════════════════════════════════
@bp.route("/jobs/<int:job_id>/status", methods=["PATCH"])
@require_auth
@require_role("employer")
def update_job_status(job_id):
    try:
        user_id = g.user_id
        data = request.json or {}
        new_status = (data.get("status") or "").lower().strip()

        if new_status not in ("active", "paused"):
            return {"error": {"code": "INVALID_STATUS",
                    "message": "Status must be 'active' or 'paused'"}}, 400

        check = db.session.execute(
            text("SELECT posted_by_user_id, expires_at FROM job_market_data WHERE id = :jid"),
            {"jid": job_id}
        ).first()
        if not check:
            return {"error": {"code": "NOT_FOUND", "message": "Job not found"}}, 404
        if check[0] != user_id:
            return {"error": {"code": "FORBIDDEN", "message": "Forbidden"}}, 403

        if new_status == "active" and check[1] is not None:
            expires_at = check[1]
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at < datetime.now(timezone.utc):
                return {"error": {"code": "JOB_EXPIRED",
                        "message": "Cannot activate — job has expired. Please extend expires_at first."}}, 400

        db.session.execute(
            text("UPDATE job_market_data SET status = :status WHERE id = :jid AND posted_by_user_id = :uid"),
            {"jid": job_id, "uid": user_id, "status": new_status}
        )
        db.session.commit()

        invalidate_jobs_cache()
        cache.delete(f"employer_jobs:emp{user_id}")

        logger.info("job_status_updated", user_id=user_id, job_id=job_id, new_status=new_status)
        return {"status": "success", "message": f"Job is now {new_status}",
                "job_id": job_id, "new_status": new_status}, 200

    except Exception as e:
        db.session.rollback()
        logger.error("update_job_status_failed", error=str(e), exc_info=True)
        return {"error": {"code": "INTERNAL_ERROR",
                "message": "Something went wrong"}}, 500


# ═══════════════════════════════════════════════════════════════════
# GET /jobs/<id>/applications
# ═══════════════════════════════════════════════════════════════════
@bp.route("/jobs/<int:job_id>/applications", methods=["GET"])
@require_auth
@require_role("employer")
def get_job_applications(job_id):
    user_id = g.user_id
    limit = request.args.get("limit", type=int, default=50)
    offset = request.args.get("offset", type=int, default=0)
    limit = max(1, min(limit, 200))

    try:
        job_check = db.session.execute(
            text("SELECT id, job_title, posted_by_user_id FROM job_market_data WHERE id = :jid"),
            {"jid": job_id}
        ).first()
        if not job_check:
            return {"error": {"code": "NOT_FOUND", "message": "Job not found"}}, 404
        if job_check[2] != user_id:
            return {"error": {"code": "FORBIDDEN", "message": "Forbidden"}}, 403

        cache_key = f"job_applicants:job{job_id}:l{limit}:o{offset}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached, 200

        total = db.session.execute(
            text("SELECT COUNT(*) FROM applications WHERE job_id = :jid"),
            {"jid": job_id}
        ).scalar() or 0

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
                LIMIT :limit OFFSET :offset
            """),
            {"jid": job_id, "limit": limit, "offset": offset}
        )
        rows = result.mappings().all()

        applications = [
            {
                "id": row["id"], "user_id": row["user_id"],
                "full_name": row["full_name"], "email": row["email"],
                "phone": row["phone"], "location": row["location"],
                "status": row["status"] or "applied",
                "applied_date": row["applied_date"].isoformat() if row["applied_date"] else None,
                "resume_filename": row["resume_filename"],
                "resume_url": row["resume_url"],
                "user_resume_url": row["user_resume_url"],
            }
            for row in rows
        ]

        response = {
            "job_id": job_id,
            "job_title": job_check[1],
            "applications": applications,
            "pagination": {
                "total": total, "limit": limit, "offset": offset,
                "has_more": (offset + len(applications)) < total,
            },
        }

        cache.set(cache_key, response, timeout=APPLICANTS_TTL)
        return response, 200

    except Exception as e:
        logger.error("get_job_applications_failed", error=str(e), exc_info=True)
        return {"error": {"code": "INTERNAL_ERROR",
                "message": "Failed to load applications"}}, 500