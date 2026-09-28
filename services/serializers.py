"""
services/serializers.py — Serializers และ formatters

Functions:
- format_salary: format ช่วงเงินเดือน
- get_company_initial: ตัวอักษรแรกของชื่อบริษัท
- serialize_row: แปลง Row → dict (isoformat dates)
"""


def format_salary(min_val, max_val):
    try:
        if min_val is None and max_val is None:
            return "N/A"
        min_val = float(min_val) if min_val else 0
        max_val = float(max_val) if max_val else 0
        return f"${int(min_val):,} - ${int(max_val):,}"
    except Exception:
        return "N/A"


def get_company_initial(company_name):
    if not company_name:
        return "J"
    return company_name.strip()[0].upper()


def serialize_row(row):
    result = dict(row)
    for key, value in result.items():
        if hasattr(value, 'isoformat'):
            result[key] = value.isoformat()
    return result
