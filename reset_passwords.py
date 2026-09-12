"""
Reset password ให้ user ที่ hash ผิด (ไม่ได้ใช้ bcrypt)
"""
import bcrypt
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

# === ตั้งค่า ===
NEW_PASSWORD = "test123"

# === สร้าง hash ใหม่ ===
new_hash = bcrypt.hashpw(NEW_PASSWORD.encode(), bcrypt.gensalt(rounds=12)).decode()
print(f"🔑 New hash: {new_hash}")
print()

# === เชื่อมต่อ DB Neon ===
engine = create_engine(os.getenv("DATABASE_URL"))

with engine.connect() as conn:
    # 1. ดู user ที่ hash ผิด
    result = conn.execute(text("""
        SELECT id, email, LENGTH(password_hash) AS len
        FROM users 
        WHERE LENGTH(password_hash) != 60 
           OR password_hash NOT LIKE '$2b$%'
        ORDER BY id
    """))
    bad_users = result.fetchall()
    
    print(f"❌ Found {len(bad_users)} users with invalid hash:")
    for u in bad_users:
        print(f"   - id={u.id}, email={u.email}, len={u.len}")
    print()
    
    if not bad_users:
        print("✨ No users to fix!")
    else:
        # 2. Reset password
        result = conn.execute(
            text("""
                UPDATE users 
                SET password_hash = :h 
                WHERE LENGTH(password_hash) != 60 
                   OR password_hash NOT LIKE '$2b$%'
            """),
            {"h": new_hash}
        )
        print(f"✅ Updated {result.rowcount} users")
        conn.commit()
        
        # 3. ตรวจสอบ
        check = conn.execute(text("""
            SELECT id, email, LENGTH(password_hash) AS len, LEFT(password_hash, 10) AS prefix
            FROM users 
            ORDER BY id
        """))
        print("\n🔍 Verified:")
        for row in check:
            status = "✅" if row.len == 60 else "❌"
            print(f"   {status} id={row.id}, {row.email}, len={row.len}")

print()
print(f"✨ Done! All users can now login with: {NEW_PASSWORD}")
