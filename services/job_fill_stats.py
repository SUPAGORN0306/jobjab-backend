"""
services/job_fill_stats.py — Fill-NULL stats for job fields

Provides cached aggregate stats used to fill NULL values in job
responses without mutating the DB.

Fill rules:
- employment_type  → forward fill (per company_name+industry), fallback = mode
- team_size        → mean
- salary_min       → median
- salary_max       → median
- experience_level → mode

If the DB has no data (all NULL) → use hardcoded defaults so that
responses NEVER contain None for these fields.

Cache TTL: 1 hour
"""
from sqlalchemy import text

from core.extensions import db, cache
from core.logging_config import get_logger

logger = get_logger(__name__)

_STATS_KEY = "job:fill_stats:v1"
_STATS_TTL = 3600  # 1 hour

# ── fallback defaults (ใช้เมื่อ DB ไม่มีข้อมูลจริง) ──
_DEFAULT_SALARY_MIN = 30000
_DEFAULT_SALARY_MAX = 60000
_DEFAULT_TEAM_SIZE  = 10
_DEFAULT_EXPERIENCE = "Mid"
_DEFAULT_EMPLOYMENT = "Full-time"


def get_fill_stats():
    """
    Return cached stats dict:
        {
            "salary_min": <int>,              # median
            "salary_max": <int>,              # median
            "team_size":  <int>,              # mean (rounded)
            "experience_level_mode": <str>,
            "employment_type_mode":  <str>,
        }

    Guarantees: values are NEVER None.
    """
    cached = cache.get(_STATS_KEY)
    if cached is not None:
        return cached

    stats = {
        "salary_min": _DEFAULT_SALARY_MIN,
        "salary_max": _DEFAULT_SALARY_MAX,
        "team_size":  _DEFAULT_TEAM_SIZE,
        "experience_level_mode": _DEFAULT_EXPERIENCE,
        "employment_type_mode":  _DEFAULT_EMPLOYMENT,
    }

    try:
        with db.engine.connect() as conn:
            # ── median salary_min/max + mean team_size ──
            row = conn.execute(text("""
                SELECT
                    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY salary_min) AS med_min,
                    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY salary_max) AS med_max,
                    AVG(team_size) AS mean_team
                FROM job_market_data
                WHERE salary_min IS NOT NULL
                   OR salary_max IS NOT NULL
                   OR team_size IS NOT NULL
            """)).mappings().first()

            if row:
                if row["med_min"] is not None:
                    stats["salary_min"] = int(row["med_min"])
                if row["med_max"] is not None:
                    stats["salary_max"] = int(row["med_max"])
                if row["mean_team"] is not None:
                    stats["team_size"] = int(round(row["mean_team"]))

            # ── mode: experience_level ──
            exp_mode = conn.execute(text("""
                SELECT experience_level
                FROM job_market_data
                WHERE experience_level IS NOT NULL
                  AND experience_level <> ''
                GROUP BY experience_level
                ORDER BY COUNT(*) DESC
                LIMIT 1
            """)).scalar()
            if exp_mode:
                stats["experience_level_mode"] = exp_mode

            # ── mode: employment_type (fallback for forward fill) ──
            emp_mode = conn.execute(text("""
                SELECT employment_type
                FROM job_market_data
                WHERE employment_type IS NOT NULL
                  AND employment_type <> ''
                GROUP BY employment_type
                ORDER BY COUNT(*) DESC
                LIMIT 1
            """)).scalar()
            if emp_mode:
                stats["employment_type_mode"] = emp_mode

        cache.set(_STATS_KEY, stats, timeout=_STATS_TTL)
        return stats

    except Exception as e:
        logger.error("get_fill_stats_failed", error=str(e), exc_info=True)
        return stats