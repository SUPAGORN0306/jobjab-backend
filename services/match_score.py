"""
services/match_score.py — Match score calculation (v1 + v2)

Functions:
- load_user_data: โหลด skills + experience ของ user
- calculate_match_score_fast: v1 — เร็วแต่ไม่ context-aware
- calculate_match_score_v2: v2 — context-aware weights ตาม level

Constants:
- INDUSTRY_RELATED (v1)
- SKILL_ALIASES, SKILL_HIERARCHY, INDUSTRY_RELATED_V2, LEVEL_REQUIREMENTS_V2, LEVEL_WEIGHTS (v2)
"""
import unicodedata

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
            text("SELECT start_date, end_date FROM user_experience WHERE user_id = :uid"),
            {"uid": user_id}
        ).mappings().all()

        from datetime import datetime
        now = datetime.now()
        total_years = 0.0
        for exp in exp_result:
            start = exp.get("start_date")
            end = exp.get("end_date") or now
            if start:
                days = (end - start).days
                total_years += days / 365.25

        return {
            "industry": user["industry"] if user else "",
            "location": user["location"] if user else "",
            "skills": [s[0] for s in skills_result],
            "total_years": total_years,
        }
    except Exception as e:
        logger.error("load_user_data_failed", error=str(e), exc_info=True)
        return {"industry": "", "location": "", "skills": [], "total_years": 0}


def calculate_match_score_fast(job_row, user_data):
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
        level_requirements = {"entry": 0, "junior": 0, "mid": 2, "senior": 5, "lead": 7}
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
# MATCH SCORE v2 — Improved (Sprint 15)
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

LEVEL_REQUIREMENTS_V2 = {
    'entry': 0, 'junior': 0, 'mid': 2, 'middle': 2,
    'senior': 5, 'lead': 7, 'principal': 10,
}

LEVEL_WEIGHTS = {
    'entry':    {'skills': 0.60, 'experience': 0.20, 'industry': 0.20},
    'junior':   {'skills': 0.60, 'experience': 0.20, 'industry': 0.20},
    'mid':      {'skills': 0.50, 'experience': 0.30, 'industry': 0.20},
    'middle':   {'skills': 0.50, 'experience': 0.30, 'industry': 0.20},
    'senior':   {'skills': 0.40, 'experience': 0.40, 'industry': 0.20},
    'lead':     {'skills': 0.35, 'experience': 0.40, 'industry': 0.25},
    'principal':{'skills': 0.30, 'experience': 0.45, 'industry': 0.25},
}


def _normalize_skill(skill):
    if not skill:
        return ""
    s = unicodedata.normalize('NFKD', str(skill))
    s = s.lower().strip()
    s = ' '.join(s.split())
    return SKILL_ALIASES.get(s, s)


def _normalize_skills(skills_raw):
    if not skills_raw:
        return set()
    if isinstance(skills_raw, str):
        skills = [s.strip() for s in skills_raw.split(',')]
    else:
        skills = list(skills_raw)
    return {_normalize_skill(s) for s in skills if s}


def _skill_matches(job_skill, user_skills_normalized):
    if job_skill in user_skills_normalized:
        return True
    for user_skill in user_skills_normalized:
        children = SKILL_HIERARCHY.get(user_skill, set())
        if job_skill in children:
            return True
        job_children = SKILL_HIERARCHY.get(job_skill, set())
        if user_skill in job_children:
            return True
    return False


def _calculate_industry_match_v2(user_industry, job_industry):
    user_ind = (user_industry or "").strip().lower()
    job_ind = (job_industry or "").strip().lower()
    if not user_ind or not job_ind:
        return 50
    if user_ind == job_ind:
        return 100
    user_related = INDUSTRY_RELATED_V2.get(user_ind, set())
    if job_ind in user_related:
        return 75
    job_related = INDUSTRY_RELATED_V2.get(job_ind, set())
    if user_ind in job_related:
        return 75
    return 40


def _calculate_experience_match_v2(total_years, job_level):
    required = LEVEL_REQUIREMENTS_V2.get((job_level or "mid").lower(), 2)
    if required == 0:
        return 100
    if total_years >= required:
        return 100
    return round((total_years / required) * 100)


def calculate_match_score_v2(job_row, user_data):
    try:
        job_skills = _normalize_skills(job_row.get("skills_required") or "")
        user_skills = _normalize_skills(user_data.get("skills", []))

        matched_skills = []
        missing_skills = []

        for js in job_skills:
            if _skill_matches(js, user_skills):
                matched_skills.append(js)
            else:
                missing_skills.append(js)

        skills_match = (
            round((len(matched_skills) / len(job_skills)) * 100)
            if job_skills else 50
        )

        total_years = user_data.get("total_years", 0)
        job_level = (job_row.get("experience_level") or "mid").lower()
        exp_match = _calculate_experience_match_v2(total_years, job_level)

        industry_match = _calculate_industry_match_v2(
            user_data.get("industry", ""),
            job_row.get("industry", "")
        )

        weights = LEVEL_WEIGHTS.get(job_level, LEVEL_WEIGHTS['mid'])
        w_skills = weights['skills']
        w_exp = weights['experience']
        w_ind = weights['industry']

        overall = (
            skills_match * w_skills +
            exp_match * w_exp +
            industry_match * w_ind
        )

        if job_skills and len(matched_skills) == 0:
            overall *= 0.6

        user_loc = (user_data.get("location") or "").lower()
        job_loc = (job_row.get("location") or "").lower()
        if user_loc and job_loc:
            if user_loc in job_loc or job_loc in user_loc:
                overall += 3
            elif 'remote' in job_loc or 'remote' in user_loc:
                overall += 2

        overall = max(0, min(100, round(overall)))

        return {
            "overall": overall,
            "skills_match": skills_match,
            "experience_match": exp_match,
            "industry_match": industry_match,
            "matched_skills": matched_skills,
            "missing_skills": missing_skills,
            "total_years": round(total_years, 1),
            "weights": {"skills": w_skills, "experience": w_exp, "industry": w_ind},
        }
    except Exception as e:
        logger.error("calculate_match_score_v2_failed", error=str(e), exc_info=True)
        return {
            "overall": 0, "skills_match": 0, "experience_match": 0,
            "industry_match": 0, "matched_skills": [], "missing_skills": [],
            "total_years": 0,
            "weights": {"skills": 0.5, "experience": 0.3, "industry": 0.2},
        }




# =============================================================================
# PERFORMANCE OPTIMIZATION — Prepared user data (Session 3)
# =============================================================================

def _build_hierarchy_cache(user_skills_normalized):
    """Expand user skills + children ครั้งเดียว → O(1) lookup"""
    expanded = set(user_skills_normalized)
    for us in user_skills_normalized:
        expanded |= SKILL_HIERARCHY.get(us, set())
    return expanded


def prepare_user_data(user_data):
    """Pre-normalize user data ครั้งเดียวก่อน loop หลาย job"""
    skills_normalized = _normalize_skills(user_data.get("skills", []))
    return {
        "industry": (user_data.get("industry") or "").lower().strip(),
        "location": (user_data.get("location") or "").lower().strip(),
        "total_years": user_data.get("total_years", 0),
        "skills_normalized": skills_normalized,
        "hierarchy_cache": _build_hierarchy_cache(skills_normalized),
    }


def calculate_match_score_v2_prepared(job_row, user_data_prepared):
    """Fast path — user_data ถูก prepare แล้ว"""
    try:
        job_skills = _normalize_skills(job_row.get("skills_required") or "")
        user_skills = user_data_prepared["skills_normalized"]
        hierarchy = user_data_prepared["hierarchy_cache"]

        matched_skills = []
        missing_skills = []

        for js in job_skills:
            if js in user_skills or js in hierarchy:
                matched_skills.append(js)
            else:
                missing_skills.append(js)

        skills_match = (
            round((len(matched_skills) / len(job_skills)) * 100)
            if job_skills else 50
        )

        total_years = user_data_prepared["total_years"]
        job_level = (job_row.get("experience_level") or "mid").lower()
        exp_match = _calculate_experience_match_v2(total_years, job_level)

        industry_match = _calculate_industry_match_v2(
            user_data_prepared["industry"],
            job_row.get("industry", "")
        )

        weights = LEVEL_WEIGHTS.get(job_level, LEVEL_WEIGHTS['mid'])
        w_skills = weights['skills']
        w_exp = weights['experience']
        w_ind = weights['industry']

        overall = (
            skills_match * w_skills +
            exp_match * w_exp +
            industry_match * w_ind
        )

        if job_skills and len(matched_skills) == 0:
            overall *= 0.6

        user_loc = user_data_prepared["location"]
        job_loc = (job_row.get("location") or "").lower()
        if user_loc and job_loc:
            if user_loc in job_loc or job_loc in user_loc:
                overall += 3
            elif 'remote' in job_loc or 'remote' in user_loc:
                overall += 2

        overall = max(0, min(100, round(overall)))

        return {
            "overall": overall,
            "skills_match": skills_match,
            "experience_match": exp_match,
            "industry_match": industry_match,
            "matched_skills": matched_skills,
            "missing_skills": missing_skills,
            "total_years": round(total_years, 1),
            "weights": {"skills": w_skills, "experience": w_exp, "industry": w_ind},
        }
    except Exception as e:
        logger.error("calculate_match_score_v2_prepared_failed", error=str(e), exc_info=True)
        return {
            "overall": 0, "skills_match": 0, "experience_match": 0,
            "industry_match": 0, "matched_skills": [], "missing_skills": [],
            "total_years": 0,
            "weights": {"skills": 0.5, "experience": 0.3, "industry": 0.2},
        }


def load_user_data_combined(user_id, db_session):
    """
    โหลด user data ด้วย query เดียว (ลด network round-trip)
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
                        (SELECT SUM((COALESCE(end_date, CURRENT_DATE) - start_date))
                         FROM user_experience WHERE user_id = :uid),
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