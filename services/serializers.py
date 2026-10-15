"""
services/serializers.py — Serializers and formatters

Functions:
- format_salary: format salary range (USD)
- get_company_initial: first letter of company name
- serialize_row: convert Row → JSON-safe dict
"""
from decimal import Decimal
from uuid import UUID


def format_salary(min_val, max_val):
    """
    Format salary range (USD).
    Handles None / 0 / min>max / min==max.
    """
    try:
        min_val = int(min_val) if min_val else None
        max_val = int(max_val) if max_val else None

        if min_val is None and max_val is None:
            return "N/A"
        if min_val is None:
            return f"Up to ${max_val:,}"
        if max_val is None:
            return f"From ${min_val:,}"

        if min_val > max_val:
            min_val, max_val = max_val, min_val

        if min_val == max_val:
            return f"${min_val:,}"

        return f"${min_val:,} - ${max_val:,}"
    except (ValueError, TypeError):
        return "N/A"


def get_company_initial(company_name):
    """Return first letter of company name (uppercase)."""
    if not company_name:
        return "J"
    return company_name.strip()[0].upper()


def serialize_row(row):
    """
    Convert DB row → JSON-safe dict.

    Handles:
    - datetime / date / time → isoformat string
    - Decimal → float
    - UUID → str
    """
    result = dict(row)
    for key, value in result.items():
        if value is None:
            continue
        if hasattr(value, "isoformat") and callable(value.isoformat):
            result[key] = value.isoformat()
        elif isinstance(value, Decimal):
            result[key] = float(value)
        elif isinstance(value, UUID):
            result[key] = str(value)
    return result


def apply_job_fills(job_dict, stats, prev_employment_type=None):
    """
    Fill NULL fields in-place using cached stats + forward-fill.

    Rules:
    - employment_type  → prev (ffill) → stats mode
    - team_size        → mean
    - salary_min       → median
    - salary_max       → median
    - experience_level → mode
    """
    if not stats:
        return job_dict, prev_employment_type

    # ── numeric ──
    if not job_dict.get("salary_min"):
        job_dict["salary_min"] = stats.get("salary_min")
    if not job_dict.get("salary_max"):
        job_dict["salary_max"] = stats.get("salary_max")
    if not job_dict.get("team_size"):
        job_dict["team_size"] = stats.get("team_size")

    # ── categorical ──
    if not job_dict.get("experience_level"):
        job_dict["experience_level"] = stats.get("experience_level_mode")

    # ── forward fill: employment_type ──
    if not job_dict.get("employment_type"):
        job_dict["employment_type"] = (
            prev_employment_type
            or stats.get("employment_type_mode")
        )

    return job_dict, job_dict.get("employment_type")