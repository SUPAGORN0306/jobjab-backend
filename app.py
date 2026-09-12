from flask import Flask, render_template, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from dotenv import load_dotenv
import os
import bcrypt
import re
import uuid
from werkzeug.utils import secure_filename

import cloudinary
import cloudinary.uploader
import cloudinary.api

# =============================================================================
# CLOUDINARY CONFIG
# =============================================================================

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True,
)

# =============================================================================
# CONSTANTS
# =============================================================================

ALLOWED_JOB_TITLES = {
    'AI Product Manager',
    'AI Researcher',
    'Computer Vision Engineer',
    'Data Analyst',
    'Data Scientist',
    'ML Engineer',
    'NLP Engineer',
    'Quant Researcher',
}


load_dotenv()

db = SQLAlchemy()

app = Flask(__name__)
CORS(app)

app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

# =============================================================================
# UPLOAD CONFIG
# =============================================================================

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
ALLOWED_RESUME_EXTENSIONS = {'pdf', 'doc', 'docx'}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB

app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE


def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def allowed_resume_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_RESUME_EXTENSIONS


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def format_salary(min_val, max_val):
    """จัดรูปแบบเงินเดือน"""
    try:
        if min_val is None and max_val is None:
            return "N/A"
        min_val = float(min_val) if min_val else 0
        max_val = float(max_val) if max_val else 0
        return f"${int(min_val):,} - ${int(max_val):,}"
    except:
        return "N/A"


def get_company_initial(company_name):
    """ดึงอักษรแรกของชื่อบริษัท"""
    if not company_name:
        return "J"
    return company_name.strip()[0].upper()


def serialize_row(row):
    """แปลง row ให้เป็น dict ที่ JSON serialize ได้"""
    result = dict(row)
    for key, value in result.items():
        if hasattr(value, 'isoformat'):
            result[key] = value.isoformat()
    return result


def generate_username_from_email(email, db_session):
    """สร้าง username จาก email + ถ้าซ้ำเติมตัวเลข"""
    base = email.split("@")[0].lower()
    base = re.sub(r"[^a-z0-9_]", "_", base)
    
    username = base
    counter = 1
    while True:
        exists = db_session.execute(
            text("SELECT id FROM users WHERE username = :u"),
            {"u": username}
        ).first()
        if not exists:
            return username
        counter += 1
        username = f"{base}_{counter}"


def extract_cloudinary_public_id(url):
    """
    ดึง public_id จาก Cloudinary URL
    เช่น https://res.cloudinary.com/xxx/image/upload/v123/jobjab/avatars/user_1_abc.jpg
    → jobjab/avatars/user_1_abc
    """
    try:
        if "res.cloudinary.com" not in url:
            return None
        # ตัดส่วนหลัง /upload/ ออก
        part = url.split("/upload/")[-1]
        # ตัด version (v1234567890/) ออกถ้ามี
        if part.startswith("v") and "/" in part:
            part = part.split("/", 1)[1]
        # ตัด extension ออก
        if "." in part:
            part = part.rsplit(".", 1)[0]
        return part
    except Exception as e:
        print(f"extract_cloudinary_public_id error: {e}")
        return None


# =============================================================================
# MATCH SCORE CALCULATOR
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


def calculate_match_score(user_id, job_id, db_session):
    """
    คำนวณ Match Score ระหว่าง user กับ job
    Weight:
    - Skills: 50%
    - Experience: 30%
    - Industry: 20%
    """
    try:
        # === 1. โหลด Job ===
        job = db_session.execute(
            text("""
                SELECT skills_required, experience_level, industry
                FROM job_market_data WHERE id = :jid
            """),
            {"jid": job_id}
        ).mappings().first()
        
        if not job:
            return {
                "overall": 0,
                "skills_match": 0,
                "experience_match": 0,
                "industry_match": 0,
                "matched_skills": [],
                "missing_skills": [],
                "total_years": 0,
            }
        
        # === 2. SKILLS MATCH ===
        job_skills_raw = (job["skills_required"] or "").lower()
        job_skills = [s.strip() for s in job_skills_raw.split(",") if s.strip()]
        
        user_skills_result = db_session.execute(
            text("SELECT skill_name FROM user_skills WHERE user_id = :uid"),
            {"uid": user_id}
        ).fetchall()
        user_skills = [s[0].lower().strip() for s in user_skills_result]
        
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
        
        # === 3. EXPERIENCE MATCH ===
        exp_result = db_session.execute(
            text("""
                SELECT 
                    SUM(
                        EXTRACT(EPOCH FROM (
                            COALESCE(end_date, NOW()) - start_date
                        )) / (365.25 * 24 * 3600)
                    ) AS total_years
                FROM user_experience 
                WHERE user_id = :uid
            """),
            {"uid": user_id}
        ).mappings().first()
        
        total_years = float(exp_result["total_years"] or 0) if exp_result else 0
        
        level_requirements = {
            "entry": 0,
            "junior": 0,
            "mid": 2,
            "senior": 5,
            "lead": 7,
        }
        
        job_level = (job["experience_level"] or "mid").lower()
        required_years = level_requirements.get(job_level, 2)
        
        if required_years == 0:
            exp_match = 100
        else:
            exp_match = min(round((total_years / required_years) * 100), 100)
        
        # === 4. INDUSTRY FIT ===
        user_result = db_session.execute(
            text("SELECT industry FROM users WHERE id = :uid"),
            {"uid": user_id}
        ).mappings().first()
        
        user_industry = (user_result["industry"] or "").lower().strip() if user_result else ""
        job_industry = (job["industry"] or "").lower().strip()
        
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
        
        # === 5. OVERALL ===
        overall = round(
            skills_match * 0.5 +
            exp_match * 0.3 +
            industry_match * 0.2
        )
        
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
        print(f"Match score error: {e}")
        import traceback
        traceback.print_exc()
        return {
            "overall": 0,
            "skills_match": 0,
            "experience_match": 0,
            "industry_match": 0,
            "matched_skills": [],
            "missing_skills": [],
            "total_years": 0,
        }


# =============================================================================
# HOME / DEBUG
# =============================================================================

@app.route("/")
def home():
    return {"message": "Flask is running"}


@app.route("/api/check-columns")
def check_columns():
    try:
        sql = text("""
            SELECT table_name, column_name, data_type 
            FROM information_schema.columns 
            WHERE table_schema = 'public' 
            ORDER BY table_name, ordinal_position;
        """)
        result = db.session.execute(sql)
        
        tables_data = {}
        for row in result:
            t_name = row.table_name
            c_info = {"column_name": row.column_name, "data_type": row.data_type}
            if t_name not in tables_data:
                tables_data[t_name] = []
            tables_data[t_name].append(c_info)
            
        return {"database_structure": tables_data}, 200
    except Exception as e:
        return {"error": str(e)}, 500


@app.route("/debug/routes")
def debug_routes():
    routes = []
    for rule in app.url_map.iter_rules():
        routes.append({
            "endpoint": rule.endpoint,
            "methods": sorted(list(rule.methods - {"HEAD", "OPTIONS"})),
            "path": str(rule)
        })
    return {"total": len(routes), "routes": sorted(routes, key=lambda x: x["path"])}


# =============================================================================
# JOBS
# =============================================================================

@app.route("/api/jobs")
def get_jobs():
    """ดึงงานทั้งหมด พร้อม Match Score + matched/missing skills"""
    try:
        user_id = request.args.get("user_id", type=int)
        
        result = db.session.execute(text("""
            SELECT 
                j.id, j.company_name, j.industry, j.job_title,
                j.skills_required, j.experience_level, j.employment_type,
                j.location, j.posted_date, j.company_size, j.tools_preferred,
                j.salary_min, j.salary_max,
                COUNT(a.id) AS applicant_count
            FROM job_market_data j
            LEFT JOIN applications a ON j.id = a.job_id
            GROUP BY j.id
            ORDER BY j.id
        """))
        rows = result.mappings().all()
        
        jobs_list = []
        for row in rows:
            job_dict = dict(row)
            count = job_dict.get("applicant_count", 0)
            
            if user_id:
                match = calculate_match_score(user_id, job_dict["id"], db.session)
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
        
        return {"jobs": jobs_list}
        
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e), "jobs": []}, 500


@app.route("/api/jobs/<int:job_id>")
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
            match = calculate_match_score(user_id, job_id, db.session)
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
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}, 500


# =============================================================================
# PROFILE
# =============================================================================

@app.route("/api/profile/<int:user_id>", methods=["GET"])
def get_profile(user_id):
    try:
        result = db.session.execute(
            text("""
                SELECT id, username, email, full_name, phone, location, 
                       bio, role, profile_image, created_at
                FROM users 
                WHERE id = :user_id
            """),
            {"user_id": user_id}
        )
        user = result.mappings().first()
        
        if not user:
            return {"error": "User not found"}, 404
        
        return {"profile": serialize_row(user)}
    except Exception as e:
        return {"error": str(e)}, 500


@app.route("/api/profile/<int:user_id>/full", methods=["GET"])
def get_full_profile(user_id):
    try:
        user_result = db.session.execute(
            text("""
                SELECT id, username, email, full_name, phone, location, 
                    bio, role, profile_image, industry, resume_url, 
                    created_at, updated_at
                FROM users WHERE id = :user_id
            """),
            {"user_id": user_id}
        )
        user = user_result.mappings().first()
        if not user:
            return {"error": "User not found"}, 404
        
        skills_result = db.session.execute(
            text("""
                SELECT id, skill_name, skill_level 
                FROM user_skills WHERE user_id = :user_id
                ORDER BY id
            """),
            {"user_id": user_id}
        )
        skills = [serialize_row(row) for row in skills_result.mappings().all()]
        
        exp_result = db.session.execute(
            text("""
                SELECT id, job_title, company_name, location, 
                       start_date, end_date, is_current, description
                FROM user_experience WHERE user_id = :user_id
                ORDER BY start_date DESC NULLS FIRST
            """),
            {"user_id": user_id}
        )
        experiences = [serialize_row(row) for row in exp_result.mappings().all()]
        
        edu_result = db.session.execute(
            text("""
                SELECT id, institution, degree, field_of_study,
                       start_date, end_date, is_current, gpa
                FROM user_education WHERE user_id = :user_id
                ORDER BY start_date DESC NULLS FIRST
            """),
            {"user_id": user_id}
        )
        educations = [serialize_row(row) for row in edu_result.mappings().all()]
        
        return {
            "profile": serialize_row(user),
            "skills": skills,
            "experiences": experiences,
            "educations": educations
        }, 200
        
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}, 500


@app.route("/api/profile/<int:user_id>", methods=["PUT"])
def update_profile(user_id):
    try:
        data = request.json
        
        if not data:
            return {"error": "No data provided"}, 400
        
        db.session.execute(
            text("""
                UPDATE users 
                SET full_name = COALESCE(:full_name, full_name),
                    phone = COALESCE(:phone, phone),
                    location = COALESCE(:location, location),
                    bio = COALESCE(:bio, bio),
                    industry = COALESCE(:industry, industry),
                    profile_image = COALESCE(:profile_image, profile_image),
                    updated_at = NOW()
                WHERE id = :user_id
            """),
            {
                "user_id": user_id,
                "full_name": data.get("full_name"),
                "phone": data.get("phone"),
                "location": data.get("location"),
                "bio": data.get("bio"),
                "industry": data.get("industry"),
                "profile_image": data.get("profile_image"),
            }
        )
        
        if "skills" in data:
            db.session.execute(
                text("DELETE FROM user_skills WHERE user_id = :user_id"),
                {"user_id": user_id}
            )
            if data["skills"]:
                db.session.execute(
                    text("""
                        INSERT INTO user_skills (user_id, skill_name, skill_level)
                        VALUES (:user_id, :skill_name, :skill_level)
                    """),
                    [
                        {
                            "user_id": user_id,
                            "skill_name": s.get("name") or s.get("skill_name"),
                            "skill_level": s.get("level") or s.get("skill_level", "Intermediate")
                        }
                        for s in data["skills"]
                    ]
                )
        
        if "experiences" in data:
            db.session.execute(
                text("DELETE FROM user_experience WHERE user_id = :user_id"),
                {"user_id": user_id}
            )
            if data["experiences"]:
                db.session.execute(
                    text("""
                        INSERT INTO user_experience 
                            (user_id, job_title, company_name, location,
                             start_date, end_date, is_current, description)
                        VALUES 
                            (:user_id, :job_title, :company_name, :location,
                             :start_date, :end_date, :is_current, :description)
                    """),
                    [
                        {
                            "user_id": user_id,
                            "job_title": e.get("job_title"),
                            "company_name": e.get("company_name"),
                            "location": e.get("location"),
                            "start_date": e.get("start_date") or None,
                            "end_date": e.get("end_date") or None,
                            "is_current": e.get("is_current", False),
                            "description": e.get("description", "")
                        }
                        for e in data["experiences"]
                    ]
                )
        
        if "educations" in data:
            db.session.execute(
                text("DELETE FROM user_education WHERE user_id = :user_id"),
                {"user_id": user_id}
            )
            if data["educations"]:
                db.session.execute(
                    text("""
                        INSERT INTO user_education 
                            (user_id, institution, degree, field_of_study,
                             start_date, end_date, is_current, gpa)
                        VALUES 
                            (:user_id, :institution, :degree, :field_of_study,
                             :start_date, :end_date, :is_current, :gpa)
                    """),
                    [
                        {
                            "user_id": user_id,
                            "institution": ed.get("institution"),
                            "degree": ed.get("degree"),
                            "field_of_study": ed.get("field_of_study"),
                            "start_date": ed.get("start_date") or None,
                            "end_date": ed.get("end_date") or None,
                            "is_current": ed.get("is_current", False),
                            "gpa": ed.get("gpa") or None
                        }
                        for ed in data["educations"]
                    ]
                )
        
        db.session.commit()
        return {"status": "success", "message": "Profile updated successfully"}, 200
        
    except IntegrityError as e:
        db.session.rollback()
        return {"error": "Constraint violation", "detail": str(e)}, 409
    except Exception as e:
        db.session.rollback()
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}, 500


# =============================================================================
# APPLICATIONS
# =============================================================================

@app.route("/api/applications", methods=["GET"])
def get_applications():
    try:
        sql = text("""
            SELECT 
                a.id, a.job_id, a.status, a.full_name, a.email, a.phone,
                a.applied_date, a.updated_at,
                j.job_title as title, j.company_name as company, 
                j.location, j.salary_min, j.salary_max
            FROM applications a
            LEFT JOIN job_market_data j ON a.job_id = j.id
            ORDER BY a.applied_date DESC
        """)
        result = db.session.execute(sql)
        rows = result.mappings().all()
        
        return jsonify({
            "applications": [serialize_row(row) for row in rows]
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/applications/user/<int:user_id>", methods=["GET"])
def get_user_applications(user_id):
    try:
        result = db.session.execute(
            text("""
                SELECT 
                    a.id, a.job_id, a.status, a.full_name, a.email, a.phone,
                    a.cover_letter, a.applied_date, a.updated_at,
                    j.job_title, j.company_name as company,
                    j.location, j.salary_min, j.salary_max
                FROM applications a
                LEFT JOIN job_market_data j ON a.job_id = j.id
                WHERE a.user_id = :user_id
                ORDER BY a.applied_date DESC
            """),
            {"user_id": user_id}
        )
        rows = result.mappings().all()
        return {"applications": [serialize_row(row) for row in rows]}
    except Exception as e:
        return {"error": str(e)}, 500


@app.route("/api/applications/<int:application_id>/detail", methods=["GET"])
def get_application_detail(application_id):
    try:
        app_result = db.session.execute(
            text("""
                SELECT 
                    a.id, a.user_id, a.job_id, a.status,
                    a.full_name, a.email, a.phone, a.location,
                    a.resume_filename, a.cover_letter,
                    a.applied_date, a.updated_at,
                    j.job_title, j.company_name, j.location AS job_location,
                    j.salary_min, j.salary_max, j.employment_type
                FROM applications a
                LEFT JOIN job_market_data j ON a.job_id = j.id
                WHERE a.id = :application_id
            """),
            {"application_id": application_id}
        )
        application = app_result.mappings().first()
        if not application:
            return {"error": "Application not found"}, 404
        
        skills_result = db.session.execute(
            text("""
                SELECT skill_name, skill_level 
                FROM application_skills WHERE application_id = :application_id
            """),
            {"application_id": application_id}
        )
        skills = [serialize_row(row) for row in skills_result.mappings().all()]
        
        exp_result = db.session.execute(
            text("""
                SELECT job_title, company_name, location,
                       start_date, end_date, is_current, description
                FROM application_experiences WHERE application_id = :application_id
                ORDER BY start_date DESC NULLS FIRST
            """),
            {"application_id": application_id}
        )
        experiences = [serialize_row(row) for row in exp_result.mappings().all()]
        
        edu_result = db.session.execute(
            text("""
                SELECT institution, degree, field_of_study,
                       start_date, end_date, is_current, gpa
                FROM application_educations WHERE application_id = :application_id
                ORDER BY start_date DESC NULLS FIRST
            """),
            {"application_id": application_id}
        )
        educations = [serialize_row(row) for row in edu_result.mappings().all()]
        
        return {
            "application": serialize_row(application),
            "skills": skills,
            "experiences": experiences,
            "educations": educations
        }, 200
        
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}, 500


@app.route("/api/applications", methods=["POST"])
def create_application():
    try:
        data = request.json
        
        if not data:
            return {"error": "No data provided"}, 400
        
        user_id = data.get("user_id")
        job_id = data.get("jobId") or data.get("job_id")
        full_name = data.get("fullName") or data.get("full_name")
        email = data.get("email")
        phone = data.get("phone")
        location = data.get("location", "")
        resume_filename = data.get("resumeFilename") or data.get("resume_filename", "")
        cover_letter = data.get("coverLetter") or data.get("cover_letter", "")
        
        skills = data.get("skills", [])
        experiences = data.get("experiences", [])
        educations = data.get("educations", [])
        
        if not user_id:
            return {"error": "user_id is required"}, 400
        if not job_id:
            return {"error": "job_id is required"}, 400
        if not full_name or not email:
            return {"error": "full_name and email are required"}, 400
        
        check = db.session.execute(
            text("""
                SELECT id FROM applications 
                WHERE user_id = :user_id AND job_id = :job_id
            """),
            {"user_id": user_id, "job_id": job_id}
        )
        if check.first():
            return {"error": "You have already applied for this position"}, 400
        
        job_check = db.session.execute(
            text("SELECT id FROM job_market_data WHERE id = :job_id"),
            {"job_id": job_id}
        )
        if not job_check.first():
            return {"error": "Job not found"}, 404
        
        result = db.session.execute(
            text("""
                INSERT INTO applications 
                    (user_id, job_id, full_name, email, phone, location,
                     resume_filename, cover_letter, status, applied_date)
                VALUES 
                    (:user_id, :job_id, :full_name, :email, :phone, :location,
                     :resume_filename, :cover_letter, 'applied', NOW())
                RETURNING id
            """),
            {
                "user_id": user_id,
                "job_id": job_id,
                "full_name": full_name,
                "email": email,
                "phone": phone,
                "location": location,
                "resume_filename": resume_filename,
                "cover_letter": cover_letter,
            }
        )
        application_id = result.scalar()
        
        if skills:
            db.session.execute(
                text("""
                    INSERT INTO application_skills 
                        (application_id, skill_name, skill_level)
                    VALUES 
                        (:application_id, :skill_name, :skill_level)
                """),
                [
                    {
                        "application_id": application_id,
                        "skill_name": s.get("name") or s.get("skill_name"),
                        "skill_level": s.get("level") or s.get("skill_level", "Intermediate")
                    }
                    for s in skills
                ]
            )
        
        if experiences:
            db.session.execute(
                text("""
                    INSERT INTO application_experiences 
                        (application_id, job_title, company_name, location,
                         start_date, end_date, is_current, description)
                    VALUES 
                        (:application_id, :job_title, :company_name, :location,
                         :start_date, :end_date, :is_current, :description)
                """),
                [
                    {
                        "application_id": application_id,
                        "job_title": e.get("job_title"),
                        "company_name": e.get("company_name"),
                        "location": e.get("location"),
                        "start_date": e.get("start_date") or None,
                        "end_date": e.get("end_date") or None,
                        "is_current": e.get("is_current", False),
                        "description": e.get("description", "")
                    }
                    for e in experiences
                ]
            )
        
        if educations:
            db.session.execute(
                text("""
                    INSERT INTO application_educations 
                        (application_id, institution, degree, field_of_study,
                         start_date, end_date, is_current, gpa)
                    VALUES 
                        (:application_id, :institution, :degree, :field_of_study,
                         :start_date, :end_date, :is_current, :gpa)
                """),
                [
                    {
                        "application_id": application_id,
                        "institution": ed.get("institution"),
                        "degree": ed.get("degree"),
                        "field_of_study": ed.get("field_of_study"),
                        "start_date": ed.get("start_date") or None,
                        "end_date": ed.get("end_date") or None,
                        "is_current": ed.get("is_current", False),
                        "gpa": ed.get("gpa") or None
                    }
                    for ed in educations
                ]
            )
        
        db.session.commit()
        
        return {
            "status": "success",
            "message": "Application submitted successfully!",
            "application_id": application_id
        }, 201
        
    except IntegrityError as e:
        db.session.rollback()
        return {"error": "Integrity error", "detail": str(e)}, 409
    except Exception as e:
        db.session.rollback()
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}, 500


# =============================================================================
# FAVORITES
# =============================================================================

@app.route("/api/favorites")
def get_favorites():
    try:
        result = db.session.execute(text("""
            SELECT 
                f.id, f.user_id, f.job_id,
                j.job_title, j.company_name,
                f.created_at
            FROM favorites f
            LEFT JOIN job_market_data j ON f.job_id = j.id
            ORDER BY f.id
        """))
        rows = result.mappings().all()
        return {"favorites": [serialize_row(row) for row in rows]}
    except Exception as e:
        return {"error": str(e)}, 500


@app.route("/api/favorites/toggle", methods=["POST"])
def toggle_favorite():
    data = request.json
    user_id = data.get("user_id")
    job_id = data.get("job_id")
    
    try:
        check = db.session.execute(
            text("SELECT id FROM favorites WHERE user_id = :user_id AND job_id = :job_id"),
            {"user_id": user_id, "job_id": job_id}
        )
        existing = check.first()
        
        if existing:
            db.session.execute(
                text("DELETE FROM favorites WHERE user_id = :user_id AND job_id = :job_id"),
                {"user_id": user_id, "job_id": job_id}
            )
            db.session.commit()
            return {"favorited": False, "message": "Removed from favorites"}
        else:
            db.session.execute(
                text("INSERT INTO favorites (user_id, job_id) VALUES (:user_id, :job_id)"),
                {"user_id": user_id, "job_id": job_id}
            )
            db.session.commit()
            return {"favorited": True, "message": "Added to favorites"}
            
    except Exception as e:
        db.session.rollback()
        return {"error": str(e)}, 500


# =============================================================================
# AUTH
# =============================================================================

@app.route("/api/auth/register", methods=["POST"])
def auth_register():
    try:
        data = request.json
        
        full_name = (data.get("full_name") or "").strip()
        email = (data.get("email") or "").strip().lower()
        password = data.get("password") or ""
        role = (data.get("role") or "candidate").lower()
        company_name = (data.get("company_name") or "").strip()
        industry = (data.get("industry") or "").strip()
        
        if not email or not password:
            return {"error": "Email and password are required"}, 400
        
        if len(password) < 6:
            return {"error": "Password must be at least 6 characters"}, 400
        
        if role not in ("candidate", "employer"):
            return {"error": "Invalid role"}, 400
        
        if role == "employer" and not company_name:
            return {"error": "Company name is required for employer"}, 400
        
        existing = db.session.execute(
            text("SELECT id FROM users WHERE email = :e"),
            {"e": email}
        ).first()
        if existing:
            return {"error": "Email already registered. Please login first to add a new role."}, 409
        
        username = generate_username_from_email(email, db.session)
        
        password_bytes = password.encode("utf-8")
        salt = bcrypt.gensalt(rounds=12)
        password_hash = bcrypt.hashpw(password_bytes, salt).decode("utf-8")
        
        result = db.session.execute(
            text("""
                INSERT INTO users 
                    (username, email, password_hash, full_name, role, created_at, updated_at)
                VALUES 
                    (:username, :email, :password_hash, :full_name, :role, NOW(), NOW())
                RETURNING id, username, email, full_name
            """),
            {
                "username": username,
                "email": email,
                "password_hash": password_hash,
                "full_name": full_name or None,
                "role": role,
            }
        )
        user_row = result.mappings().first()
        user_id = user_row["id"]
        
        db.session.execute(
            text("""
                INSERT INTO user_roles (user_id, role, created_at)
                VALUES (:user_id, :role, NOW())
            """),
            {"user_id": user_id, "role": role}
        )
        
        if role == "employer":
            db.session.execute(
                text("""
                    INSERT INTO employer_profiles 
                        (user_id, company_name, industry, created_at, updated_at)
                    VALUES 
                        (:user_id, :company_name, :industry, NOW(), NOW())
                """),
                {
                    "user_id": user_id,
                    "company_name": company_name,
                    "industry": industry or None,
                }
            )
        
        db.session.commit()
        
        return {
            "status": "success",
            "message": "Account created successfully",
            "user": {
                "id": user_row["id"],
                "username": user_row["username"],
                "email": user_row["email"],
                "full_name": user_row["full_name"],
                "roles": [role],
                "role": role,
            }
        }, 201
        
    except IntegrityError as e:
        db.session.rollback()
        return {"error": "Integrity error", "detail": str(e)}, 409
    except Exception as e:
        db.session.rollback()
        print(f"Register error: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}, 500


@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    try:
        data = request.json
        email = (data.get("email") or "").strip().lower()
        password = data.get("password") or ""
        
        if not email or not password:
            return {"error": "Email and password are required"}, 400
        
        result = db.session.execute(
            text("""
                SELECT id, username, email, password_hash, full_name
                FROM users WHERE email = :e
            """),
            {"e": email}
        )
        user = result.mappings().first()
        
        if not user:
            return {"error": "Invalid email or password"}, 401
        
        password_bytes = password.encode("utf-8")
        hash_bytes = user["password_hash"].encode("utf-8")
        
        if not bcrypt.checkpw(password_bytes, hash_bytes):
            return {"error": "Invalid email or password"}, 401
        
        roles_result = db.session.execute(
            text("""
                SELECT role FROM user_roles 
                WHERE user_id = :uid
                ORDER BY role
            """),
            {"uid": user["id"]}
        )
        roles = [row[0] for row in roles_result]
        
        if not roles:
            roles = ["candidate"]
        
        response_user = {
            "id": user["id"],
            "username": user["username"],
            "email": user["email"],
            "full_name": user["full_name"],
            "roles": roles,
            "role": roles[0] if len(roles) == 1 else None,
        }
        
        if "employer" in roles:
            emp_result = db.session.execute(
                text("""
                    SELECT company_name, industry
                    FROM employer_profiles WHERE user_id = :uid
                """),
                {"uid": user["id"]}
            )
            emp = emp_result.mappings().first()
            if emp:
                response_user["company_name"] = emp["company_name"]
                response_user["industry"] = emp["industry"]
        
        return {
            "status": "success",
            "message": "Login successful",
            "user": response_user
        }, 200
        
    except Exception as e:
        print(f"Login error: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}, 500


@app.route("/api/auth/add-role", methods=["POST"])
def auth_add_role():
    try:
        data = request.json
        user_id = data.get("user_id")
        role = (data.get("role") or "").lower()
        company_name = (data.get("company_name") or "").strip()
        industry = (data.get("industry") or "").strip()
        
        if not user_id or not role:
            return {"error": "user_id and role required"}, 400
        
        if role not in ("candidate", "employer"):
            return {"error": "Invalid role"}, 400
        
        if role == "employer" and not company_name:
            return {"error": "Company name is required"}, 400
        
        user_check = db.session.execute(
            text("SELECT id, email FROM users WHERE id = :uid"),
            {"uid": user_id}
        ).first()
        if not user_check:
            return {"error": "User not found"}, 404
        
        existing = db.session.execute(
            text("SELECT id FROM user_roles WHERE user_id = :uid AND role = :r"),
            {"uid": user_id, "r": role}
        ).first()
        
        if existing:
            return {"error": f"Role '{role}' already exists for this user"}, 409
        
        db.session.execute(
            text("""
                INSERT INTO user_roles (user_id, role, created_at)
                VALUES (:uid, :r, NOW())
            """),
            {"uid": user_id, "r": role}
        )
        
        if role == "employer":
            existing_profile = db.session.execute(
                text("SELECT id FROM employer_profiles WHERE user_id = :uid"),
                {"uid": user_id}
            ).first()
            
            if not existing_profile:
                db.session.execute(
                    text("""
                        INSERT INTO employer_profiles 
                            (user_id, company_name, industry, created_at, updated_at)
                        VALUES (:uid, :cn, :ind, NOW(), NOW())
                    """),
                    {"uid": user_id, "cn": company_name, "ind": industry or None}
                )
        
        db.session.commit()
        
        return {
            "status": "success",
            "message": f"Role '{role}' added successfully"
        }, 201
        
    except IntegrityError as e:
        db.session.rollback()
        return {"error": "Integrity error", "detail": str(e)}, 409
    except Exception as e:
        db.session.rollback()
        print(f"Add role error: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}, 500


# =============================================================================
# OTHERS
# =============================================================================

@app.route("/api/all-tables-data")
def all_tables_data():
    try:
        tables_result = db.session.execute(text("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public'
            ORDER BY table_name
        """))
        tables = [row[0] for row in tables_result]
        
        result = {}
        for table_name in tables:
            try:
                data_result = db.session.execute(
                    text(f"SELECT * FROM {table_name} ORDER BY 1")
                )
                rows = data_result.mappings().all()
                
                result[table_name] = {
                    "columns": list(rows[0].keys()) if rows else [],
                    "data": [serialize_row(row) for row in rows]
                }
            except Exception as e:
                result[table_name] = {
                    "columns": [],
                    "data": [],
                    "error": str(e)
                }
        
        return result
    except Exception as e:
        return {"error": str(e)}, 500


@app.route("/jobs-page")
def jobs_page():
    return render_template("jobs.html")


@app.route("/tables")
def tables_page():
    return render_template("table_selector.html")


# =============================================================================
# EMPLOYER
# =============================================================================

@app.route("/api/employer/jobs", methods=["GET"])
def get_employer_jobs():
    try:
        user_id = request.args.get("user_id", type=int)
        
        if not user_id:
            return {"error": "user_id is required"}, 400
        
        result = db.session.execute(
            text("""
                SELECT 
                    j.id,
                    j.job_title,
                    j.company_name,
                    j.location,
                    j.employment_type,
                    j.experience_level,
                    j.salary_min,
                    j.salary_max,
                    j.posted_date,
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
                "applicant_count": row["applicant_count"],
                "status": "Active",
            })
        
        return {"jobs": jobs}, 200
        
    except Exception as e:
        print(f"Get employer jobs error: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}, 500


@app.route("/api/employer/jobs", methods=["POST"])
def create_employer_job():
    try:
        data = request.json
        
        user_id = data.get("user_id")
        job_title = (data.get("job_title") or "").strip()
        company_name = (data.get("company_name") or "").strip()
        location = (data.get("location") or "").strip()
        employment_type = data.get("employment_type", "Full-time")
        experience_level = data.get("experience_level", "Mid")
        salary_min = data.get("salary_min")
        salary_max = data.get("salary_max")
        skills_required = (data.get("skills_required") or "").strip()
        tools_preferred = (data.get("tools_preferred") or "").strip()
        industry = (data.get("industry") or "").strip()
        company_size = (data.get("company_size") or "").strip()
        about_role = (data.get("about_role") or "").strip()
        responsibilities = (data.get("responsibilities") or "").strip()
        requirements = (data.get("requirements") or "").strip()
        
        if not user_id:
            return {"error": "user_id is required"}, 400
        if not job_title:
            return {"error": "Job title is required"}, 400
        if job_title not in ALLOWED_JOB_TITLES:
            return {
                "error": (
                    f"Invalid job title. Allowed: "
                    f"{', '.join(sorted(ALLOWED_JOB_TITLES))}"
                )
            }, 400
        if not company_name:
            return {"error": "Company name is required"}, 400
        
        check = db.session.execute(
            text("""
                SELECT 1 FROM user_roles 
                WHERE user_id = :uid AND role = 'employer'
            """),
            {"uid": user_id}
        ).first()
        
        if not check:
            return {"error": "User is not an employer"}, 403
        
        result = db.session.execute(
            text("""
                INSERT INTO job_market_data (
                    job_title, company_name, location,
                    employment_type, experience_level,
                    salary_min, salary_max,
                    skills_required, tools_preferred,
                    industry, company_size,
                    about_role, responsibilities, requirements,
                    posted_by_user_id, posted_date
                ) VALUES (
                    :job_title, :company_name, :location,
                    :employment_type, :experience_level,
                    :salary_min, :salary_max,
                    :skills_required, :tools_preferred,
                    :industry, :company_size,
                    :about_role, :responsibilities, :requirements,
                    :user_id, NOW()
                )
                RETURNING id
            """),
            {
                "job_title": job_title,
                "company_name": company_name,
                "location": location or None,
                "employment_type": employment_type,
                "experience_level": experience_level,
                "salary_min": float(salary_min) if salary_min else None,
                "salary_max": int(salary_max) if salary_max else None,
                "skills_required": skills_required or None,
                "tools_preferred": tools_preferred or None,
                "industry": industry or None,
                "company_size": company_size or None,
                "about_role": about_role or None,
                "responsibilities": responsibilities or None,
                "requirements": requirements or None,
                "user_id": user_id,
            }
        )
        job_id = result.scalar()
        
        db.session.commit()
        
        return {
            "status": "success",
            "message": "Job posted successfully",
            "job_id": job_id,
        }, 201
        
    except IntegrityError as e:
        db.session.rollback()
        return {"error": "Integrity error", "detail": str(e)}, 409
    except Exception as e:
        db.session.rollback()
        print(f"Create job error: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}, 500


@app.route("/api/employer/jobs/<int:job_id>/applications", methods=["GET"])
def get_job_applications(job_id):
    try:
        job_check = db.session.execute(
            text("SELECT id, job_title FROM job_market_data WHERE id = :jid"),
            {"jid": job_id}
        ).first()
        if not job_check:
            return {"error": "Job not found"}, 404
        
        result = db.session.execute(
            text("""
                SELECT 
                    a.id, a.user_id, a.full_name, a.email, a.phone,
                    a.location, a.status, a.applied_date, a.resume_filename
                FROM applications a
                WHERE a.job_id = :jid
                ORDER BY a.applied_date DESC
            """),
            {"jid": job_id}
        )
        rows = result.mappings().all()
        
        applications = []
        for row in rows:
            applications.append({
                "id": row["id"],
                "user_id": row["user_id"],
                "full_name": row["full_name"],
                "email": row["email"],
                "phone": row["phone"],
                "location": row["location"],
                "status": row["status"] or "applied",
                "applied_date": row["applied_date"].isoformat() if row["applied_date"] else None,
                "resume_filename": row["resume_filename"],
            })
        
        return {
            "job_id": job_id,
            "job_title": job_check[1],
            "applications": applications,
            "total": len(applications),
        }, 200
        
    except Exception as e:
        print(f"Get job applications error: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}, 500


@app.route("/api/employer/applications/<int:application_id>/detail", methods=["GET"])
def get_application_snapshot(application_id):
    try:
        app_result = db.session.execute(
            text("""
                SELECT 
                    a.id, a.user_id, a.job_id, a.status,
                    a.full_name, a.email, a.phone, a.location,
                    a.resume_filename, a.cover_letter,
                    a.applied_date, a.updated_at,
                    j.job_title, j.company_name,
                    j.employment_type, j.experience_level,
                    j.location AS job_location,
                    j.salary_min, j.salary_max
                FROM applications a
                LEFT JOIN job_market_data j ON a.job_id = j.id
                WHERE a.id = :aid
            """),
            {"aid": application_id}
        )
        application = app_result.mappings().first()
        if not application:
            return {"error": "Application not found"}, 404
        
        skills_result = db.session.execute(
            text("""
                SELECT skill_name, skill_level 
                FROM application_skills WHERE application_id = :aid
                ORDER BY id
            """),
            {"aid": application_id}
        )
        skills = [serialize_row(row) for row in skills_result.mappings().all()]
        
        exp_result = db.session.execute(
            text("""
                SELECT job_title, company_name, location,
                       start_date, end_date, is_current, description
                FROM application_experiences WHERE application_id = :aid
                ORDER BY start_date DESC NULLS FIRST
            """),
            {"aid": application_id}
        )
        experiences = [serialize_row(row) for row in exp_result.mappings().all()]
        
        edu_result = db.session.execute(
            text("""
                SELECT institution, degree, field_of_study,
                       start_date, end_date, is_current, gpa
                FROM application_educations WHERE application_id = :aid
                ORDER BY start_date DESC NULLS FIRST
            """),
            {"aid": application_id}
        )
        educations = [serialize_row(row) for row in edu_result.mappings().all()]
        
        return {
            "application": serialize_row(application),
            "skills": skills,
            "experiences": experiences,
            "educations": educations,
        }, 200
        
    except Exception as e:
        print(f"Get application detail error: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}, 500


@app.route("/api/employer/applications/<int:application_id>/status", methods=["PUT"])
def update_application_status(application_id):
    try:
        data = request.json
        new_status = (data.get("status") or "").lower()
        
        allowed = {"applied", "reviewing", "interview", "rejected"}
        if new_status not in allowed:
            return {"error": f"Invalid status. Allowed: {', '.join(allowed)}"}, 400
        
        check = db.session.execute(
            text("SELECT id, status FROM applications WHERE id = :aid"),
            {"aid": application_id}
        ).first()
        if not check:
            return {"error": "Application not found"}, 404
        
        db.session.execute(
            text("""
                UPDATE applications 
                SET status = :status, updated_at = NOW()
                WHERE id = :aid
            """),
            {"status": new_status, "aid": application_id}
        )
        
        db.session.commit()
        
        return {
            "status": "success",
            "message": f"Status updated to '{new_status}'",
            "application_id": application_id,
            "new_status": new_status,
        }, 200
        
    except Exception as e:
        db.session.rollback()
        print(f"Update status error: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}, 500


# =============================================================================
# SKILLS
# =============================================================================

@app.route("/api/skills", methods=["GET"])
def get_skills():
    try:
        result = db.session.execute(
            text("""
                SELECT DISTINCT skill_name 
                FROM user_skills 
                WHERE skill_name IS NOT NULL
                ORDER BY skill_name
            """)
        )
        db_skills = [row[0] for row in result]
        
        default_skills = [
            "Python", "JavaScript", "TypeScript", "Java", "C++", "Go", "Rust",
            "React", "Vue", "Angular", "Node.js", "Express", "Django", "Flask",
            "SQL", "PostgreSQL", "MySQL", "MongoDB", "Redis",
            "PyTorch", "TensorFlow", "Scikit-learn", "Pandas", "NumPy",
            "Docker", "Kubernetes", "AWS", "GCP", "Azure",
            "Git", "CI/CD", "Linux", "REST API", "GraphQL",
            "Figma", "UI Design", "UX Research", "Prototyping",
            "Data Analysis", "Machine Learning", "Deep Learning", "NLP",
            "Computer Vision", "Data Visualization", "Tableau", "Power BI",
            "Statistics", "A/B Testing", "Excel",
        ]
        
        all_skills = sorted(set(db_skills + default_skills))
        
        return {"skills": all_skills}, 200
    except Exception as e:
        return {"error": str(e)}, 500


# =============================================================================
# UPLOAD: Resume (Cloudinary)
# =============================================================================

@app.route("/api/upload/resume", methods=["POST"])
def upload_resume():
    """อัปโหลด Resume → Cloudinary"""
    try:
        user_id = request.form.get("user_id")
        if not user_id:
            return {"error": "user_id is required"}, 400
        
        user_id = int(user_id)
        
        if "resume" not in request.files:
            return {"error": "No file provided"}, 400
        
        file = request.files["resume"]
        
        if file.filename == "":
            return {"error": "Empty filename"}, 400
        
        if not allowed_resume_file(file.filename):
            return {
                "error": f"Invalid file type. Allowed: {', '.join(ALLOWED_RESUME_EXTENSIONS)}"
            }, 400
        
        # สร้าง public_id (unique)
        ext = file.filename.rsplit(".", 1)[1].lower()
        public_id = f"jobjab/resumes/resume_user_{user_id}_{uuid.uuid4().hex[:8]}"
        
        # อัปโหลดขึ้น Cloudinary
        upload_result = cloudinary.uploader.upload(
            file,
            public_id=public_id,
            resource_type="raw",   # raw = pdf/doc/docx
            overwrite=True,
        )
        
        resume_url = upload_result["secure_url"]
        filename = upload_result["public_id"].split("/")[-1] + "." + ext
        
        # ลบไฟล์เก่าใน Cloudinary (ถ้ามี)
        old = db.session.execute(
            text("SELECT resume_url FROM users WHERE id = :uid"),
            {"uid": user_id}
        ).first()
        
        if old and old[0] and "res.cloudinary.com" in old[0]:
            try:
                old_public_id = extract_cloudinary_public_id(old[0])
                if old_public_id:
                    cloudinary.uploader.destroy(old_public_id, resource_type="raw")
            except Exception as e:
                print(f"Delete old resume from Cloudinary failed: {e}")
        
        # Update DB
        db.session.execute(
            text("""
                UPDATE users 
                SET resume_url = :url, updated_at = NOW()
                WHERE id = :uid
            """),
            {"url": resume_url, "uid": user_id}
        )
        db.session.commit()
        
        return {
            "status": "success",
            "message": "Resume uploaded successfully",
            "resume_url": resume_url,
            "filename": filename,
        }, 200
        
    except Exception as e:
        db.session.rollback()
        print(f"Upload resume error: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}, 500


@app.route("/api/resume/<int:user_id>", methods=["DELETE"])
def delete_resume(user_id):
    """ลบ Resume (จาก Cloudinary + DB)"""
    try:
        old = db.session.execute(
            text("SELECT resume_url FROM users WHERE id = :uid"),
            {"uid": user_id}
        ).first()
        
        if not old or not old[0]:
            return {"error": "No resume to delete"}, 404
        
        old_url = old[0]
        
        # ลบไฟล์จาก Cloudinary
        if "res.cloudinary.com" in old_url:
            try:
                old_public_id = extract_cloudinary_public_id(old_url)
                if old_public_id:
                    cloudinary.uploader.destroy(old_public_id, resource_type="raw")
            except Exception as e:
                print(f"Delete from Cloudinary failed: {e}")
        
        # Update DB
        db.session.execute(
            text("""
                UPDATE users 
                SET resume_url = NULL, updated_at = NOW()
                WHERE id = :uid
            """),
            {"uid": user_id}
        )
        db.session.commit()
        
        return {
            "status": "success",
            "message": "Resume deleted successfully"
        }, 200
        
    except Exception as e:
        db.session.rollback()
        print(f"Delete resume error: {e}")
        return {"error": str(e)}, 500


# =============================================================================
# UPLOAD: Avatar (Cloudinary)
# =============================================================================

@app.route("/api/upload/avatar", methods=["POST"])
def upload_avatar():
    """อัปโหลด Avatar → Cloudinary"""
    try:
        user_id = request.form.get("user_id")
        if not user_id:
            return {"error": "user_id is required"}, 400
        
        user_id = int(user_id)
        
        if "avatar" not in request.files:
            return {"error": "No file provided"}, 400
        
        file = request.files["avatar"]
        
        if file.filename == "":
            return {"error": "Empty filename"}, 400
        
        if not allowed_file(file.filename):
            return {
                "error": f"Invalid file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"
            }, 400
        
        # สร้าง public_id
        public_id = f"jobjab/avatars/user_{user_id}_{uuid.uuid4().hex[:8]}"
        
        # อัปโหลดขึ้น Cloudinary (พร้อม resize + optimize)
        upload_result = cloudinary.uploader.upload(
            file,
            public_id=public_id,
            transformation=[
                {"width": 500, "height": 500, "crop": "limit"},
                {"quality": "auto", "fetch_format": "auto"},
            ],
            overwrite=True,
        )
        
        image_url = upload_result["secure_url"]
        filename = upload_result["public_id"].split("/")[-1]
        
        # ลบไฟล์เก่าใน Cloudinary (ถ้ามี)
        old = db.session.execute(
            text("SELECT profile_image FROM users WHERE id = :uid"),
            {"uid": user_id}
        ).first()
        
        if old and old[0] and "res.cloudinary.com" in old[0]:
            try:
                old_public_id = extract_cloudinary_public_id(old[0])
                if old_public_id:
                    cloudinary.uploader.destroy(old_public_id)
            except Exception as e:
                print(f"Delete old avatar failed: {e}")
        
        # Update DB
        db.session.execute(
            text("""
                UPDATE users 
                SET profile_image = :img, updated_at = NOW()
                WHERE id = :uid
            """),
            {"img": image_url, "uid": user_id}
        )
        db.session.commit()
        
        return {
            "status": "success",
            "message": "Avatar uploaded successfully",
            "image_url": image_url,
            "filename": filename,
        }, 200
        
    except Exception as e:
        db.session.rollback()
        print(f"Upload avatar error: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}, 500


# =============================================================================
# MATCH SCORE
# =============================================================================

@app.route("/api/match-score/<int:job_id>", methods=["GET"])
def get_match_score(job_id):
    try:
        user_id = request.args.get("user_id", type=int)
        if not user_id:
            return {"error": "user_id is required"}, 400
        
        match = calculate_match_score(user_id, job_id, db.session)
        return match, 200
    except Exception as e:
        return {"error": str(e)}, 500


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)