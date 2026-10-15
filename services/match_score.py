"""
services/match_score.py — Match score calculation (v1 + v2)

Functions:
- load_user_data: load skills + experience (legacy, 3 queries)
- load_user_data_combined: load in 1 query (preferred)
- calculate_match_score_fast: v1 (legacy)
- calculate_match_score_v2: v2 (non-prepared, slower)
- prepare_user_data: pre-normalize for batch
- calculate_match_score_v2_prepared: v2 fast path (preferred)

Constants:
- INDUSTRY_RELATED (v1)
- SKILL_ALIASES, SKILL_HIERARCHY, INDUSTRY_RELATED_V2,
  LEVEL_REQUIREMENTS_V2, FIXED_WEIGHTS (v2)

Experience calculation:
- SUM(end_date - start_date) across user_experience rows
- NULL end_date → CURRENT_DATE (current job)
- Negative durations clamped to 0
- Rows with NULL start_date are skipped

Response shape includes detail fields for UI:
- skills_detail: { matched, missing, total }
- experience_detail: { user_years, required_years, job_level }
- industry_detail: { user_industry, job_industry }
"""
import unicodedata
from datetime import datetime

from sqlalchemy import text

from core.logging_config import get_logger

logger = get_logger(__name__)


# =============================================================================
# MATCH SCORE v1 (LEGACY)
# =============================================================================

INDUSTRY_RELATED = {
    "tech": ["e-commerce", "finance", "education"],
    "finance": ["tech", "e-commerce"],
    "e-commerce": ["tech", "retail", "finance"],
    "retail": ["e-commerce"],
    "healthcare": ["education"],
    "education": ["healthcare", "tech"],
    "automotive": ["tech"],
}


def load_user_data(user_id, db_session):
    """
    Legacy loader — 3 queries (kept for backward compat).

    Experience = SUM(end_date - start_date) across user_experience rows.
    - NULL end_date → now (current job)
    - Negative durations clamped to 0
    - Rows with NULL start_date are skipped
    """
    try:
        user = db_session.execute(
            text("SELECT industry, location FROM users WHERE id = :uid"),
            {"uid": user_id}
        ).mappings().first()

        skills_result = db_session.execute(
            text("SELECT skill_name FROM user_skills WHERE user_id = :uid"),
            {"uid": user_id}
        ).fetchall()

        exp_result = db_session.execute(
            text("""
                SELECT start_date, end_date
                FROM user_experience
                WHERE user_id = :uid
                  AND start_date IS NOT NULL
            """),
            {"uid": user_id}
        ).mappings().all()

        now = datetime.now()
        total_days = 0
        for exp in exp_result:
            start = exp.get("start_date")
            end = exp.get("end_date") or now
            days = (end - start).days
            total_days += max(days, 0)   # clamp negative to 0

        return {
            "industry": user["industry"] if user else "",
            "location": user["location"] if user else "",
            "skills": [s[0] for s in skills_result],
            "total_years": total_days / 365.25,
        }
    except Exception as e:
        logger.error("load_user_data_failed", error=str(e), exc_info=True)
        return {"industry": "", "location": "", "skills": [], "total_years": 0}


def calculate_match_score_fast(job_row, user_data):
    """Legacy v1 — kept for backward compat. Use v2 instead."""
    try:
        job_skills_raw = (job_row.get("skills_required") or "").lower()
        job_skills = [s.strip() for s in job_skills_raw.split(",") if s.strip()]
        user_skills = user_data["skills"]

        matched_skills = []
        missing_skills = []

        for js in job_skills:
            matched = False
            for us in user_skills:
                if js in us or us in js:
                    matched = True
                    matched_skills.append(js)
                    break
            if not matched:
                missing_skills.append(js)

        skills_match = round((len(matched_skills) / len(job_skills)) * 100) if job_skills else 0

        total_years = user_data["total_years"]
        level_requirements = {"entry": 0, "mid": 2, "senior": 5}
        job_level = (job_row.get("experience_level") or "mid").lower()
        required_years = level_requirements.get(job_level, 2)

        if required_years == 0:
            exp_match = 100
        else:
            exp_match = min(round((total_years / required_years) * 100), 100)

        user_industry = user_data["industry"]
        job_industry = (job_row.get("industry") or "").lower().strip()

        if not user_industry or not job_industry:
            industry_match = 50
        elif user_industry == job_industry:
            industry_match = 100
        elif job_industry in INDUSTRY_RELATED.get(user_industry, []):
            industry_match = 75
        elif user_industry in INDUSTRY_RELATED.get(job_industry, []):
            industry_match = 75
        else:
            industry_match = 40

        overall = round(skills_match * 0.5 + exp_match * 0.3 + industry_match * 0.2)

        return {
            "overall": overall,
            "skills_match": skills_match,
            "experience_match": exp_match,
            "industry_match": industry_match,
            "matched_skills": matched_skills,
            "missing_skills": missing_skills,
            "total_years": round(total_years, 1),
        }
    except Exception as e:
        logger.error("calculate_match_score_failed", error=str(e), exc_info=True)
        return {
            "overall": 0, "skills_match": 0, "experience_match": 0,
            "industry_match": 0, "matched_skills": [], "missing_skills": [],
            "total_years": 0,
        }


# =============================================================================
# MATCH SCORE v2
# =============================================================================

SKILL_ALIASES = {
    'ml': 'machine learning',
    'ai': 'artificial intelligence',
    'js': 'javascript',
    'ts': 'typescript',
    'nlp': 'natural language processing',
    'cv': 'computer vision',
    'dl': 'deep learning',
    'ds': 'data science',
    'py': 'python',
    'k8s': 'kubernetes',
    'aws': 'amazon web services',
    'gcp': 'google cloud platform',
    'postgres': 'postgresql',
    'mongo': 'mongodb',
    'tf': 'tensorflow',
    'torch': 'pytorch',
    'sklearn': 'scikit-learn',
}

# parent → {children}
SKILL_HIERARCHY = {
    'deep learning': {'pytorch', 'tensorflow', 'keras', 'neural networks'},
    'machine learning': {'deep learning', 'scikit-learn', 'xgboost', 'random forest'},
    'data analysis': {'pandas', 'numpy', 'statistics', 'data visualization'},
    'computer vision': {'opencv', 'image processing', 'cuda'},
    'natural language processing': {'transformers', 'llm', 'hugging face'},
    'cloud': {'aws', 'gcp', 'kubernetes', 'docker'},
    'frontend': {'react', 'vue', 'angular', 'javascript', 'typescript'},
    'backend': {'node.js', 'django', 'flask', 'fastapi', 'express'},
}

INDUSTRY_RELATED_V2 = {
    'tech': {'e-commerce', 'finance', 'education', 'healthcare'},
    'finance': {'tech', 'e-commerce', 'quant', 'banking'},
    'e-commerce': {'tech', 'retail', 'finance'},
    'retail': {'e-commerce', 'tech'},
    'healthcare': {'tech', 'education', 'biotech'},
    'education': {'tech', 'healthcare'},
    'automotive': {'tech', 'manufacturing'},
}

# Active levels in DB: Entry, Mid, Senior
# Required years for exp_match calculation
LEVEL_REQUIREMENTS_V2 = {
    'entry': 0,
    'mid': 2,
    'senior': 5,
}

# ⭐ Fixed weights — no level dependency
FIXED_WEIGHTS = {
    "skills": 0.5,
    "experience": 0.3,
    "industry": 0.2,
}


# ═══════════════════════════════════════════════════════════════════
# Skill normalization
# ═══════════════════════════════════════════════════════════════════
def _normalize_skill(skill):
    """Normalize a single skill to canonical lowercase form."""
    if not skill:
        return ""
    s = unicodedata.normalize('NFKD', str(skill))
    s = s.lower().strip()
    s = ' '.join(s.split())
    return SKILL_ALIASES.get(s, s)


def _parse_skills_list(skills_raw):
    """Parse skills from string/list → list of ORIGINAL strings."""
    if not skills_raw:
        return []
    if isinstance(skills_raw, str):
        return [s.strip() for s in skills_raw.split(',') if s.strip()]
    return [str(s).strip() for s in skills_raw if s]


def _normalize_skills(skills_raw):
    """Parse + normalize. Returns dict {normalized: original}."""
    skills_list = _parse_skills_list(skills_raw)
    result = {}
    for s in skills_list:
        norm = _normalize_skill(s)
        if norm and norm not in result:
            result[norm] = s
    return result


# ═══════════════════════════════════════════════════════════════════
# Skill hierarchy
# ═══════════════════════════════════════════════════════════════════
def _build_hierarchy_cache(user_skills_normalized):
    """
    Expand user skills with BOTH directions:
      - children of user skill  → user has parent → covers children
      - parents of user skill   → user has child → counts as parent skill
    """
    expanded = set(user_skills_normalized)

    # Build reverse map: child → set of parents
    child_to_parents = {}
    for parent, children in SKILL_HIERARCHY.items():
        for child in children:
            child_to_parents.setdefault(child, set()).add(parent)

    for us in user_skills_normalized:
        expanded |= SKILL_HIERARCHY.get(us, set())
        expanded |= child_to_parents.get(us, set())

    return expanded


# ═══════════════════════════════════════════════════════════════════
# Sub-score calculators
# ═══════════════════════════════════════════════════════════════════
def _calculate_industry_match_v2(user_industry, job_industry):
    """Industry match score (0-100)."""
    user_ind = (user_industry or "").strip().lower()
    job_ind = (job_industry or "").strip().lower()
    if not user_ind or not job_ind:
        return 50
    if user_ind == job_ind:
        return 100
    if job_ind in INDUSTRY_RELATED_V2.get(user_ind, set()):
        return 75
    if user_ind in INDUSTRY_RELATED_V2.get(job_ind, set()):
        return 75
    return 40


def _calculate_experience_match_v2(total_years, job_level):
    """Experience match score (0-100). Cap at 100."""
    required = LEVEL_REQUIREMENTS_V2.get((job_level or "mid").lower(), 2)
    if required == 0:
        return 100
    if total_years >= required:
        return 100
    return round((total_years / required) * 100)


def _apply_location_bonus(overall, user_loc, job_loc):
    """Location bonus as % multiplier. Caps at 100."""
    if not user_loc or not job_loc:
        return overall

    user_loc = user_loc.lower()
    job_loc = job_loc.lower()

    if user_loc in job_loc or job_loc in user_loc:
        overall *= 1.03   # +3%
    elif 'remote' in job_loc or 'remote' in user_loc:
        overall *= 1.02   # +2%

    return max(0, min(100, round(overall)))


def _compute_overall(skills_match, exp_match, industry_match, weights, has_job_skills):
    """
    Weighted overall. If no job skills, re-distribute skills weight
    to experience + industry by their relative ratio.
    """
    if not has_job_skills:
        w_exp = weights['experience']
        w_ind = weights['industry']
        total = w_exp + w_ind
        if total == 0:
            return (exp_match + industry_match) / 2
        return (exp_match * (w_exp / total)) + (industry_match * (w_ind / total))

    return (
        skills_match * weights['skills'] +
        exp_match * weights['experience'] +
        industry_match * weights['industry']
    )


def _build_match_response(
    *,
    overall,
    skills_match,
    has_job_skills,
    exp_match,
    industry_match,
    matched_skills,
    missing_skills,
    total_years,
    user_industry,
    job_industry,
    job_level,
    required_years,
    weights,
):
    """
    Build consistent response dict with detail fields for UI.

    Detail fields:
    - skills_detail: { matched, missing, total }
    - experience_detail: { user_years, required_years, job_level }
    - industry_detail: { user_industry, job_industry }
    """
    return {
        "overall": overall,
        "skills_match": skills_match,
        "has_skills": has_job_skills,
        "experience_match": exp_match,
        "industry_match": industry_match,

        # Detail for UI
        "skills_detail": {
            "matched": matched_skills,
            "missing": missing_skills,
            "total": len(matched_skills) + len(missing_skills),
        },
        "experience_detail": {
            "user_years": round(total_years, 1),
            "required_years": required_years,
            "job_level": job_level,
        },
        "industry_detail": {
            "user_industry": user_industry or "",
            "job_industry": job_industry or "",
        },

        # Legacy flat fields
        "matched_skills": matched_skills,
        "missing_skills": missing_skills,
        "total_years": round(total_years, 1),
        "weights": weights,
    }


def _empty_match_result():
    """Fallback empty result — returned on error."""
    return {
        "overall": 0,
        "skills_match": None,
        "has_skills": False,
        "experience_match": 0,
        "industry_match": 0,
        "skills_detail": {"matched": [], "missing": [], "total": 0},
        "experience_detail": {"user_years": 0, "required_years": 0, "job_level": ""},
        "industry_detail": {"user_industry": "", "job_industry": ""},
        "matched_skills": [],
        "missing_skills": [],
        "total_years": 0,
        "weights": FIXED_WEIGHTS,
    }


# ═══════════════════════════════════════════════════════════════════
# Main: calculate_match_score_v2 (non-prepared)
# ═══════════════════════════════════════════════════════════════════
def calculate_match_score_v2(job_row, user_data):
    """v2 — non-prepared path. Use prepared version for batch."""
    try:
        job_skills_map = _normalize_skills(job_row.get("skills_required") or "")
        user_skills_map = _normalize_skills(user_data.get("skills", []))
        user_skills = set(user_skills_map.keys())
        hierarchy = _build_hierarchy_cache(user_skills)

        matched_skills = []
        missing_skills = []

        for js_norm, js_original in job_skills_map.items():
            if js_norm in user_skills or js_norm in hierarchy:
                matched_skills.append(js_original)
            else:
                missing_skills.append(js_original)

        has_job_skills = len(job_skills_map) > 0
        if has_job_skills:
            skills_match = round((len(matched_skills) / len(job_skills_map)) * 100)
        else:
            skills_match = None

        total_years = user_data.get("total_years", 0)
        job_level = (job_row.get("experience_level") or "mid").lower()
        required_years = LEVEL_REQUIREMENTS_V2.get(job_level, 2)
        exp_match = _calculate_experience_match_v2(total_years, job_level)

        user_industry = user_data.get("industry", "")
        job_industry = job_row.get("industry", "")
        industry_match = _calculate_industry_match_v2(user_industry, job_industry)

        weights = FIXED_WEIGHTS
        effective_skills = skills_match if skills_match is not None else 0
        overall = _compute_overall(
            effective_skills, exp_match, industry_match,
            weights, has_job_skills
        )

        overall = _apply_location_bonus(
            overall,
            user_data.get("location", ""),
            job_row.get("location", "")
        )

        overall = max(0, min(100, round(overall)))

        return _build_match_response(
            overall=overall,
            skills_match=skills_match,
            has_job_skills=has_job_skills,
            exp_match=exp_match,
            industry_match=industry_match,
            matched_skills=matched_skills,
            missing_skills=missing_skills,
            total_years=total_years,
            user_industry=user_industry,
            job_industry=job_industry,
            job_level=job_level,
            required_years=required_years,
            weights=weights,
        )
    except Exception as e:
        logger.error("calculate_match_score_v2_failed", error=str(e), exc_info=True)
        return _empty_match_result()


# ═══════════════════════════════════════════════════════════════════
# Prepared user data (fast path)
# ═══════════════════════════════════════════════════════════════════
def prepare_user_data(user_data):
    """Pre-normalize user data once before looping many jobs."""
    skills_map = _normalize_skills(user_data.get("skills", []))
    skills_normalized = set(skills_map.keys())
    return {
        "industry": (user_data.get("industry") or "").lower().strip(),
        "industry_raw": (user_data.get("industry") or "").strip(),   # ⭐ เก็บค่าจริง
        "location": (user_data.get("location") or "").lower().strip(),
        "total_years": user_data.get("total_years", 0),
        "skills_normalized": skills_normalized,
        "hierarchy_cache": _build_hierarchy_cache(skills_normalized),
    }


def calculate_match_score_v2_prepared(job_row, user_data_prepared):
    """Fast path — user_data already prepared."""
    try:
        job_skills_map = _normalize_skills(job_row.get("skills_required") or "")
        user_skills = user_data_prepared["skills_normalized"]
        hierarchy = user_data_prepared["hierarchy_cache"]

        matched_skills = []
        missing_skills = []

        for js_norm, js_original in job_skills_map.items():
            if js_norm in user_skills or js_norm in hierarchy:
                matched_skills.append(js_original)
            else:
                missing_skills.append(js_original)

        has_job_skills = len(job_skills_map) > 0
        if has_job_skills:
            skills_match = round((len(matched_skills) / len(job_skills_map)) * 100)
        else:
            skills_match = None

        total_years = user_data_prepared["total_years"]
        job_level = (job_row.get("experience_level") or "mid").lower()
        required_years = LEVEL_REQUIREMENTS_V2.get(job_level, 2)
        exp_match = _calculate_experience_match_v2(total_years, job_level)

        user_industry = user_data_prepared.get("industry_raw") or user_data_prepared.get("industry", "")
        job_industry = job_row.get("industry", "")
        industry_match = _calculate_industry_match_v2(user_industry, job_industry)

        weights = FIXED_WEIGHTS
        effective_skills = skills_match if skills_match is not None else 0
        overall = _compute_overall(
            effective_skills, exp_match, industry_match,
            weights, has_job_skills
        )

        overall = _apply_location_bonus(
            overall,
            user_data_prepared["location"],
            job_row.get("location", "")
        )

        overall = max(0, min(100, round(overall)))

        return _build_match_response(
            overall=overall,
            skills_match=skills_match,
            has_job_skills=has_job_skills,
            exp_match=exp_match,
            industry_match=industry_match,
            matched_skills=matched_skills,
            missing_skills=missing_skills,
            total_years=total_years,
            user_industry=user_industry,
            job_industry=job_industry,
            job_level=job_level,
            required_years=required_years,
            weights=weights,
        )
    except Exception as e:
        logger.error("calculate_match_score_v2_prepared_failed", error=str(e), exc_info=True)
        return _empty_match_result()


# ═══════════════════════════════════════════════════════════════════
# Combined loader — single query
# ═══════════════════════════════════════════════════════════════════
def load_user_data_combined(user_id, db_session):
    """
    Load user data in a single query (fewer round-trips).

    Experience = SUM(end_date - start_date) across user_experience rows.
    - NULL end_date → CURRENT_DATE (current job)
    - Negative durations clamped to 0
    - Rows with NULL start_date are skipped
    """
    try:
        row = db_session.execute(
            text("""
                SELECT
                    u.industry,
                    u.location,
                    COALESCE(
                        (SELECT json_agg(skill_name)
                         FROM user_skills WHERE user_id = :uid),
                        '[]'::json
                    ) AS skills,
                    COALESCE(
                        (SELECT SUM(
                            GREATEST(
                                COALESCE(end_date, CURRENT_DATE) - start_date,
                                0
                            )
                        )
                        FROM user_experience
                        WHERE user_id = :uid
                          AND start_date IS NOT NULL),
                        0
                    ) AS total_days
                FROM users u
                WHERE u.id = :uid
            """),
            {"uid": user_id}
        ).mappings().first()

        if not row:
            return {"industry": "", "location": "", "skills": [], "total_years": 0}

        return {
            "industry": row["industry"] or "",
            "location": row["location"] or "",
            "skills": row["skills"] or [],
            "total_years": float(row["total_days"] or 0) / 365.25,
        }
    except Exception as e:
        logger.error("load_user_data_combined_failed", error=str(e), exc_info=True)
        return {"industry": "", "location": "", "skills": [], "total_years": 0}