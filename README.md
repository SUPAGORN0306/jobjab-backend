# JOBJAB Backend — Job Matching API

Flask REST API powering the JOBJAB job matching platform. Handles authentication, job listings, applications, file uploads, and the algorithmic match scoring system.

## Live API

- **Base URL:** https://jobjab-api.onrender.com
- **Health Check:** https://jobjab-api.onrender.com/

## Overview

This is the backend service for JOBJAB, a job matching platform connecting job seekers with employers in the technology industry. The API provides endpoints for user authentication, profile management, job listings, application tracking, and file uploads.

The core feature is a match scoring algorithm that evaluates candidate-job fit based on skills, experience, and industry alignment. Each job listing can be scored against a candidate's profile to provide personalized recommendations.

## Features

### Authentication

- User registration with role selection (candidate or employer)
- Login with bcrypt password verification
- Multi-role support for users who are both job seekers and employers
- Session identification via user ID returned on login

### Job Management

- Public job listing retrieval with optional personalized match scores
- Detailed job information including responsibilities, requirements, and company data
- Employer-specific endpoints for creating and managing job postings
- Application count tracking per job

### Application Workflow

- Application submission with full candidate snapshot (skills, experience, education)
- Application status workflow (applied, reviewing, interview, rejected)
- Applicant viewing for employers with resume access
- Duplicate application prevention

### Profile Management

- Full profile retrieval including related skills, experience, and education
- Profile updates with transactional consistency
- Resume upload to Cloudinary with automatic cleanup of previous files
- Profile image upload with automatic resizing and format optimization

### Favorites

- Save and unsave jobs
- Retrieve user's saved jobs

## Technology Stack

### Core

- Python 3.12
- Flask 3.0
- Gunicorn WSGI server for production

### Database

- PostgreSQL 15 hosted on Neon (serverless)
- SQLAlchemy ORM for session management
- Raw SQL with parameterized queries for complex queries
- Connection pooling with pool_pre_ping and pool_recycle for serverless reliability

### File Storage

- Cloudinary SDK for resume and image storage
- Automatic image transformation (resize to 500x500, quality auto, format auto)
- Resource type raw for resumes (PDF, DOC, DOCX)
- Automatic deletion of previous files when new ones are uploaded

### Security

- bcrypt password hashing with 12 rounds
- CORS restricted to specific allowed origins
- Parameterized SQL queries throughout

### Infrastructure

- Render for hosting with automatic deployments from GitHub
- UptimeRobot for uptime monitoring (14-minute interval)
- Neon for serverless PostgreSQL

## Match Score Algorithm

The match score combines three weighted criteria to evaluate candidate-job fit.

Overall Score = (Skills Match x 0.50) + (Experience Match x 0.30) + (Industry Match x 0.20)

### Skills Match (50% weight)

Percentage of required job skills present in the candidate's profile. Uses case-insensitive substring matching, so "JavaScript" matches "JavaScript" or "JS".

### Experience Match (30% weight)

Compares total years of work experience to the job's required level.

| Job Level | Required Years |
|-----------|----------------|
| Entry     | 0              |
| Junior    | 0              |
| Mid       | 2              |
| Senior    | 5              |
| Lead      | 7              |

Scoring is proportional up to a maximum of 100 for candidates exceeding the requirement.

### Industry Match (20% weight)

- Exact match: 100 points
- Related industry: 75 points
- Different industry: 40 points
- Missing data: 50 points (neutral)

Related industry mappings are predefined in the codebase (e.g., tech to e-commerce, finance to tech).

## Getting Started

### Prerequisites

- Python 3.12 or higher
- PostgreSQL database (local or Neon account)
- Cloudinary account (free tier sufficient)

### Installation

Clone the repository and create a virtual environment:

    git clone https://github.com/SUPAGORN0306/jobjab-backend.git
    cd jobjab-backend
    python -m venv venv

Activate the virtual environment:

    # Linux / macOS
    source venv/bin/activate

    # Windows
    venv\Scripts\activate

Install dependencies:

    pip install -r requirements.txt

### Configuration

Create a .env file in the project root:

    DATABASE_URL=postgresql://user:password@host:5432/database
    CLOUDINARY_CLOUD_NAME=your_cloud_name
    CLOUDINARY_API_KEY=your_api_key
    CLOUDINARY_API_SECRET=your_api_secret

### Running the Server

Start the development server:

    python app.py

The API will be available at http://localhost:5000.

For production-like execution:

    gunicorn app:app

## Production Deployment

Pushing to the main branch triggers automatic deployment on Render.

Environment variables must be configured in the Render dashboard:

| Variable              | Description                  |
|-----------------------|------------------------------|
| DATABASE_URL          | PostgreSQL connection string |
| CLOUDINARY_CLOUD_NAME | Cloudinary cloud name        |
| CLOUDINARY_API_KEY    | Cloudinary API key           |
| CLOUDINARY_API_SECRET | Cloudinary API secret        |
| PYTHON_VERSION        | Python version (3.12.3)      |

## API Reference

### Authentication

| Method | Endpoint           | Description                                 |
|--------|--------------------|---------------------------------------------|
| POST   | /api/auth/register | Create a new user account                   |
| POST   | /api/auth/login    | Authenticate existing user                  |
| POST   | /api/auth/add-role | Add a secondary role to an existing account |

### Jobs

| Method | Endpoint                 | Description                              |
|--------|--------------------------|------------------------------------------|
| GET    | /api/jobs                | List all jobs with optional match scores |
| GET    | /api/jobs/:id            | Retrieve detailed job information        |
| GET    | /api/match-score/:job_id | Get standalone match score calculation   |

### Profile

| Method | Endpoint              | Description                                                      |
|--------|-----------------------|------------------------------------------------------------------|
| GET    | /api/profile/:id      | Retrieve basic profile information                               |
| GET    | /api/profile/:id/full | Retrieve complete profile with skills, experience, and education |
| PUT    | /api/profile/:id      | Update profile information                                       |
| POST   | /api/upload/resume    | Upload resume file to Cloudinary                                 |
| DELETE | /api/resume/:id       | Delete existing resume                                           |
| POST   | /api/upload/avatar    | Upload profile image to Cloudinary                               |

### Applications

| Method | Endpoint                              | Description                                  |
|--------|---------------------------------------|----------------------------------------------|
| POST   | /api/applications                     | Submit job application                       |
| GET    | /api/applications/user/:id            | Retrieve applications submitted by a user    |
| GET    | /api/applications/:id/detail          | Get detailed application information         |
| GET    | /api/employer/jobs/:id/applications   | Retrieve all applications for a specific job |
| PUT    | /api/employer/applications/:id/status | Update application status                    |

### Employer

| Method | Endpoint           | Description                                        |
|--------|--------------------|----------------------------------------------------|
| GET    | /api/employer/jobs | Retrieve jobs posted by the authenticated employer |
| POST   | /api/employer/jobs | Create a new job posting                           |

### Utilities

| Method | Endpoint              | Description                                |
|--------|-----------------------|--------------------------------------------|
| GET    | /api/skills           | Retrieve available skills for autocomplete |
| GET    | /api/favorites        | Retrieve user's favorite jobs              |
| POST   | /api/favorites/toggle | Add or remove a job from favorites         |

### Debug (Development Only)

| Method | Endpoint           | Description                |
|--------|--------------------|----------------------------|
| GET    | /api/check-columns | Inspect database schema    |
| GET    | /debug/routes      | List all registered routes |

## Technical Highlights

### N+1 Query Elimination

The initial match score implementation queried the database once per job per criterion, resulting in over 200 queries for a page displaying 50 jobs. The optimized implementation loads the user's profile data (skills, experience, industry) once per request and reuses it across all job calculations. Total queries per request: 5.

### Serverless Database Reliability

Neon PostgreSQL closes idle connections after periods of inactivity, which caused intermittent SSL connection errors. The fix uses SQLAlchemy's pool_pre_ping setting to validate connections before use, combined with pool_recycle=300 to proactively recycle connections every 5 minutes. The application now recovers gracefully without user-visible failures.

### File Storage

All uploaded files are stored on Cloudinary rather than the local filesystem. This is essential for Render's free tier, which uses ephemeral storage — any files written to disk are lost on each deployment. Cloudinary integration also provides automatic image optimization and transformation capabilities.

Previous files are automatically deleted when new ones are uploaded, preventing orphaned files and reducing storage usage.

### Security

- Passwords are hashed with bcrypt using a cost factor of 12
- CORS is restricted to specific origins: localhost for development, and production Vercel domains for deployment
- All database access uses parameterized SQL queries to prevent injection
- No secrets are committed to version control; all sensitive configuration is provided via environment variables

### Availability

UptimeRobot pings the API every 14 minutes to prevent Render's free-tier cold starts. Without this, the service would sleep after 15 minutes of inactivity, causing 30-60 second delays on the first request.

## Project Structure

    jobjab-backend/
    ├── app.py                     # Main Flask application with all routes
    ├── requirements.txt           # Python dependencies
    ├── .env                       # Environment variables (not committed)
    └── .env.example               # Template for environment variables

## Environment Variables

| Variable              | Description                  |
|-----------------------|------------------------------|
| DATABASE_URL          | PostgreSQL connection string | 
| CLOUDINARY_CLOUD_NAME | Cloudinary cloud name        | 
| CLOUDINARY_API_KEY    | Cloudinary API key           | 
| CLOUDINARY_API_SECRET | Cloudinary API secret        | 
| PYTHON_VERSION        | Python version for Render    | 3.12.3 

## Author

Supagorn

- GitHub: @SUPAGORN0306
- Frontend Repository: jobjab
- Backend Repository: jobjab-backend

## License

This project is licensed under the MIT License.
