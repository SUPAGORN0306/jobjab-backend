"""
utils/files.py — File upload helpers

Functions:
- allowed_file: ตรวจ extension รูปภาพ
- allowed_resume_file: ตรวจ extension resume (PDF)
"""

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
ALLOWED_RESUME_EXTENSIONS = {'pdf'}


def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def allowed_resume_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_RESUME_EXTENSIONS
