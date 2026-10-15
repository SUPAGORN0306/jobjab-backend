"""
scripts/heal_job_data.py — Auto-heal job_market_data

Usage:
    python -m scripts.heal_job_data           # scan only (dry-run)
    python -m scripts.heal_job_data --fix     # scan + fix

Fixes:
- salary_min < 0 → treat as None → median
- salary_max < 0 → treat as None → median
- salary_min > salary_max → swap
- salary_min IS NULL → median(salary_min)
- salary_max IS NULL → median(salary_max)
- float decimals → FLOOR
- employment_type / experience_level / company_size / visa_sponsorship
    IS NULL OR not in allowed set → mode
- job_title = only digits → flag (no auto-fix, log only)
"""
import argparse
import math
from sqlalchemy import text

from core.extensions import db
from core.logging_config import get_logger

logger = get_logger(__name__)

ALLOWED_EMPLOYMENT_TYPES = {"Full-time", "Part-time", "Contract", "Internship"}
ALLOWED_EXPERIENCE_LEVELS = {"Entry", "Mid", "Senior"}
ALLOWED_COMPANY_SIZES = {"Small", "Mid", "Large"}
ALLOWED_VISA_SPONSORSHIP = {"Yes", "No"}


def scan():
    """Scan all issues — return dict."""
    results = {}

    results["salary_min_negative"] = db.session.execute(text("""
        SELECT COUNT(*) FROM job_market_data WHERE salary_min < 0
    """)).scalar() or 0

    results["salary_max_negative"] = db.session.execute(text("""
        SELECT COUNT(*) FROM job_market_data WHERE salary_max < 0
    """)).scalar() or 0

    results["salary_max_lt_min"] = db.session.execute(text("""
        SELECT COUNT(*) FROM job_market_data
        WHERE salary_min IS NOT NULL
          AND salary_max IS NOT NULL
          AND salary_max < salary_min
    """)).scalar() or 0

    results["salary_min_null"] = db.session.execute(text("""
        SELECT COUNT(*) FROM job_market_data WHERE salary_min IS NULL
    """)).scalar() or 0

    results["salary_max_null"] = db.session.execute(text("""
        SELECT COUNT(*) FROM job_market_data WHERE salary_max IS NULL
    """)).scalar() or 0

    results["employment_type_invalid"] = db.session.execute(text("""
        SELECT COUNT(*) FROM job_market_data
        WHERE employment_type IS NULL OR NOT (employment_type = ANY(:allowed))
    """), {"allowed": list(ALLOWED_EMPLOYMENT_TYPES)}).scalar() or 0

    results["experience_level_invalid"] = db.session.execute(text("""
        SELECT COUNT(*) FROM job_market_data
        WHERE experience_level IS NULL OR NOT (experience_level = ANY(:allowed))
    """), {"allowed": list(ALLOWED_EXPERIENCE_LEVELS)}).scalar() or 0

    results["company_size_invalid"] = db.session.execute(text("""
        SELECT COUNT(*) FROM job_market_data
        WHERE company_size IS NULL OR NOT (company_size = ANY(:allowed))
    """), {"allowed": list(ALLOWED_COMPANY_SIZES)}).scalar() or 0

    results["visa_sponsorship_invalid"] = db.session.execute(text("""
        SELECT COUNT(*) FROM job_market_data
        WHERE visa_sponsorship IS NULL OR NOT (visa_sponsorship = ANY(:allowed))
    """), {"allowed": list(ALLOWED_VISA_SPONSORSHIP)}).scalar() or 0

    results["job_title_numeric"] = db.session.execute(text("""
        SELECT COUNT(*) FROM job_market_data
        WHERE job_title ~ '^[0-9]+$'
    """)).scalar() or 0

    return results


def get_median(db_session, column):
    row = db_session.execute(text(f"""
        SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY {column})
        FROM job_market_data
        WHERE {column} IS NOT NULL AND {column} >= 0
    """)).scalar()
    return int(math.floor(float(row))) if row is not None else 0


def get_mode(db_session, column, allowed):
    row = db_session.execute(
        text(f"""
            SELECT {column}, COUNT(*) AS c
            FROM job_market_data
            WHERE {column} IS NOT NULL AND {column} = ANY(:allowed)
            GROUP BY {column}
            ORDER BY c DESC LIMIT 1
        """),
        {"allowed": list(allowed)}
    ).first()
    return row[0] if row else None


def fix():
    """Fix all issues — return dict of counts."""
    fixed = {}

    # 1. salary_min < 0 → treat as None (will fill median)
    r = db.session.execute(text("""
        UPDATE job_market_data SET salary_min = NULL WHERE salary_min < 0
    """))
    fixed["salary_min_negative_to_null"] = r.rowcount

    # 2. salary_max < 0 → treat as None
    r = db.session.execute(text("""
        UPDATE job_market_data SET salary_max = NULL WHERE salary_max < 0
    """))
    fixed["salary_max_negative_to_null"] = r.rowcount

    # 3. min > max → swap
    r = db.session.execute(text("""
        UPDATE job_market_data
        SET salary_min = salary_max,
            salary_max = salary_min
        WHERE salary_min IS NOT NULL
          AND salary_max IS NOT NULL
          AND salary_max < salary_min
    """))
    fixed["salary_swapped"] = r.rowcount

    # 4. salary_min NULL → median
    median_min = get_median(db.session, "salary_min")
    if median_min > 0:
        r = db.session.execute(text("""
            UPDATE job_market_data SET salary_min = :m WHERE salary_min IS NULL
        """), {"m": median_min})
        fixed["salary_min_filled_median"] = r.rowcount

    # 5. salary_max NULL → median
    median_max = get_median(db.session, "salary_max")
    if median_max > 0:
        r = db.session.execute(text("""
            UPDATE job_market_data SET salary_max = :m WHERE salary_max IS NULL
        """), {"m": median_max})
        fixed["salary_max_filled_median"] = r.rowcount

    # 6. categorical — fill with mode
    for col, allowed in [
        ("employment_type", ALLOWED_EMPLOYMENT_TYPES),
        ("experience_level", ALLOWED_EXPERIENCE_LEVELS),
        ("company_size", ALLOWED_COMPANY_SIZES),
        ("visa_sponsorship", ALLOWED_VISA_SPONSORSHIP),
    ]:
        mode_val = get_mode(db.session, col, allowed)
        if not mode_val:
            continue
        r = db.session.execute(
            text(f"""
                UPDATE job_market_data SET {col} = :m
                WHERE {col} IS NULL OR NOT ({col} = ANY(:allowed))
            """),
            {"m": mode_val, "allowed": list(allowed)}
        )
        fixed[f"{col}_filled_mode"] = r.rowcount

    # 7. min > max again (after median fill)
    r = db.session.execute(text("""
        UPDATE job_market_data
        SET salary_max = salary_min
        WHERE salary_min IS NOT NULL
          AND salary_max IS NOT NULL
          AND salary_max < salary_min
    """))
    fixed["salary_max_clamped_after_fill"] = r.rowcount

    db.session.commit()
    return fixed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fix", action="store_true", help="Apply fixes")
    args = parser.parse_args()

    print("=" * 60)
    print("SCAN")
    print("=" * 60)
    issues = scan()
    for k, v in issues.items():
        status = "[!] " if v > 0 else "[OK]"
        print(f"{status} {k}: {v}")

    if not args.fix:
        print("\nRun with --fix to apply fixes")
        return

    print("\n" + "=" * 60)
    print("FIX")
    print("=" * 60)
    fixed = fix()
    for k, v in fixed.items():
        print(f"[FIX] {k}: {v}")

    print("\n" + "=" * 60)
    print("VERIFY (rescan)")
    print("=" * 60)
    issues = scan()
    all_clean = True
    for k, v in issues.items():
        status = "[!] " if v > 0 else "[OK]"
        print(f"{status} {k}: {v}")
        if v > 0:
            all_clean = False

    print("\n" + ("ALL CLEAN" if all_clean else "STILL HAS ISSUES"))


if __name__ == "__main__":
    main()